import json
import re
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models


class ConversationContext(BaseModel):
    """
    Durable Conversation Context Entity (PRD Section 10 & 23).
    Maintains active intent, entity selections, preferences, and workflow states
    to resolve contextual references and follow-up requests.
    """
    session_id: str
    patient_id: Optional[str] = None
    hospital_id: Optional[str] = None
    channel: str = "web_voice"

    # Core contextual state requested by user
    current_intent: Optional[str] = None
    selected_hospital: Optional[Dict[str, Any]] = None
    selected_doctor: Optional[Dict[str, Any]] = None
    selected_slot: Optional[Dict[str, Any]] = None
    current_appointment: Optional[Dict[str, Any]] = None
    relevant_preferences: Dict[str, Any] = Field(default_factory=dict)
    communication_preferences: Dict[str, Any] = Field(default_factory=dict)
    completed_workflow_state: Dict[str, Any] = Field(default_factory=dict)

    # Ephemeral working memory for reference resolution
    last_doctors_listed: List[Dict[str, Any]] = Field(default_factory=list)
    last_slots_listed: List[Dict[str, Any]] = Field(default_factory=list)
    chief_complaint: Optional[str] = None
    urgency_level: str = "ROUTINE"
    turns: List[Dict[str, Any]] = Field(default_factory=list)

    def add_turn(self, role: str, content: str, tool_calls: Optional[List[Dict[str, Any]]] = None):
        self.turns.append({
            "role": role,
            "content": content,
            "tool_calls": tool_calls or [],
            "timestamp": datetime.utcnow().isoformat()
        })

    def mark_workflow_step(self, step_name: str, details: Optional[Dict[str, Any]] = None):
        self.completed_workflow_state[step_name] = {
            "completed_at": datetime.utcnow().isoformat(),
            "details": details or {}
        }

    def resolve_reference(self, user_text: str) -> Dict[str, Any]:
        """
        Resolves conversational references (e.g. 'book that slot', 'the first doctor',
        'tomorrow afternoon', 'reschedule my appointment') against working memory.
        """
        resolved: Dict[str, Any] = {}
        lower = user_text.lower()

        # 1. Resolve Doctor references
        if self.last_doctors_listed and not self.selected_doctor:
            if "first" in lower or "1st" in lower:
                self.selected_doctor = self.last_doctors_listed[0]
                resolved["doctor"] = self.selected_doctor
            elif len(self.last_doctors_listed) > 1 and ("second" in lower or "2nd" in lower):
                self.selected_doctor = self.last_doctors_listed[1]
                resolved["doctor"] = self.selected_doctor
            else:
                for doc in self.last_doctors_listed:
                    name_parts = doc.get("full_name", "").lower().split()
                    for part in name_parts:
                        if len(part) > 3 and part in lower:
                            self.selected_doctor = doc
                            resolved["doctor"] = doc
                            break

        # 2. Resolve Slot references
        if self.last_slots_listed:
            opt_match = re.search(r"\b(?:option|slot|choice)?\s*([1-9])\b", lower)
            if "first" in lower or "1st" in lower or "earliest" in lower:
                self.selected_slot = self.last_slots_listed[0]
                resolved["slot"] = self.selected_slot
            elif "second" in lower or "2nd" in lower:
                if len(self.last_slots_listed) > 1:
                    self.selected_slot = self.last_slots_listed[1]
                    resolved["slot"] = self.selected_slot
            elif "third" in lower or "3rd" in lower:
                if len(self.last_slots_listed) > 2:
                    self.selected_slot = self.last_slots_listed[2]
                    resolved["slot"] = self.selected_slot
            elif opt_match:
                idx = int(opt_match.group(1)) - 1
                if 0 <= idx < len(self.last_slots_listed):
                    self.selected_slot = self.last_slots_listed[idx]
                    resolved["slot"] = self.selected_slot
            elif "that slot" in lower or "book it" in lower or "that one" in lower:
                if len(self.last_slots_listed) == 1 or not self.selected_slot:
                    self.selected_slot = self.last_slots_listed[0]
                    resolved["slot"] = self.selected_slot
            else:
                # Match by time pattern (e.g., '10:00', '2 pm', '14:00')
                for s in self.last_slots_listed:
                    start_str = str(s.get("start_time", ""))
                    if any(t in lower for t in [start_str[-5:], start_str[11:16]] if t):
                        self.selected_slot = s
                        resolved["slot"] = s
                        break

        # 3. Resolve Appointment references
        if ("that appointment" in lower or "it" in lower or "my appointment" in lower) and self.current_appointment:
            resolved["appointment"] = self.current_appointment

        # 4. Resolve Channel Preferences
        if "sms" in lower or "text" in lower or "phone" in lower:
            self.communication_preferences["channel"] = "SMS"
        elif "email" in lower:
            self.communication_preferences["channel"] = "EMAIL"
        elif "whatsapp" in lower:
            self.communication_preferences["channel"] = "WHATSAPP"

        return resolved


class ConversationContextManager:
    """
    Manages persistent retrieval, updating, and reference resolution of AI Contexts.
    Decoupled from AI logic and controlled capabilities.
    """

    @staticmethod
    def get_or_create(db: Session, session_id: str, patient_id: Optional[str] = None, hospital_id: Optional[str] = None) -> ConversationContext:
        """Retrieves active conversation context from DB or creates a fresh session."""
        conv = db.query(models.AIConversation).filter(models.AIConversation.session_id == session_id).first()
        if not conv:
            conv = models.AIConversation(
                session_id=session_id,
                patient_id=patient_id,
                hospital_id=hospital_id,
                status="ACTIVE",
                transcript=[]
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)

            ctx_model = models.AIContext(
                conversation_id=conv.id,
                hospital_id=hospital_id,
                current_intent=None,
                preferences={}
            )
            db.add(ctx_model)
            db.commit()

            return ConversationContext(
                session_id=session_id,
                patient_id=patient_id,
                hospital_id=hospital_id
            )

        # Reconstruct from DB
        ctx_model = conv.ai_context
        prefs = (ctx_model.preferences or {}) if ctx_model else {}

        context = ConversationContext(
            session_id=session_id,
            patient_id=conv.patient_id,
            hospital_id=conv.hospital_id,
            channel=conv.channel or "web_voice",
            current_intent=ctx_model.current_intent if ctx_model else None,
            selected_hospital=prefs.get("selected_hospital"),
            selected_doctor=prefs.get("selected_doctor"),
            selected_slot=prefs.get("selected_slot"),
            current_appointment=prefs.get("current_appointment"),
            relevant_preferences=prefs.get("relevant_preferences", {}),
            communication_preferences=prefs.get("communication_preferences", {"channel": "SMS"}),
            completed_workflow_state=prefs.get("completed_workflow_state", {}),
            last_doctors_listed=prefs.get("last_doctors_listed", []),
            last_slots_listed=prefs.get("last_slots_listed", []),
            chief_complaint=prefs.get("chief_complaint"),
            urgency_level=ctx_model.urgency_level if ctx_model else "ROUTINE",
            turns=conv.transcript or []
        )
        return context

    @staticmethod
    def save(db: Session, context: ConversationContext):
        """Persists updated context and turn transcript to SQLite."""
        conv = db.query(models.AIConversation).filter(models.AIConversation.session_id == context.session_id).first()
        if not conv:
            return

        conv.patient_id = context.patient_id or conv.patient_id
        conv.hospital_id = context.hospital_id or conv.hospital_id
        conv.transcript = context.turns
        conv.updated_at = datetime.utcnow()

        ctx_model = conv.ai_context
        if not ctx_model:
            ctx_model = models.AIContext(conversation_id=conv.id)
            db.add(ctx_model)

        ctx_model.current_intent = context.current_intent
        ctx_model.selected_hospital_id = context.selected_hospital.get("id") if context.selected_hospital else None
        ctx_model.selected_doctor_id = context.selected_doctor.get("id") if context.selected_doctor else None
        ctx_model.selected_slot_id = context.selected_slot.get("id") if context.selected_slot else None
        ctx_model.current_appointment_id = context.current_appointment.get("id") if context.current_appointment else None
        ctx_model.urgency_level = context.urgency_level
        ctx_model.updated_at = datetime.utcnow()

        # Pack structured preferences and working memory
        ctx_model.preferences = {
            "selected_hospital": context.selected_hospital,
            "selected_doctor": context.selected_doctor,
            "selected_slot": context.selected_slot,
            "current_appointment": context.current_appointment,
            "relevant_preferences": context.relevant_preferences,
            "communication_preferences": context.communication_preferences,
            "completed_workflow_state": context.completed_workflow_state,
            "last_doctors_listed": context.last_doctors_listed,
            "last_slots_listed": context.last_slots_listed,
            "chief_complaint": context.chief_complaint
        }

        db.commit()
