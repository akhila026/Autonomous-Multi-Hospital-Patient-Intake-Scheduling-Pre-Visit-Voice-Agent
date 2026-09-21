from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class ConversationContext(BaseModel):
    """
    Explicit structured conversational state (PRD Section 10 & 23).
    Separates transactional state from unstructured memory.
    """
    session_id: str
    patient_id: Optional[str] = None
    current_intent: Optional[str] = None  # e.g. "BOOK_APPOINTMENT", "RESCHEDULE", "TRIAGE"
    selected_hospital_id: Optional[str] = None
    selected_doctor_id: Optional[str] = None
    selected_slot_id: Optional[str] = None
    current_appointment_id: Optional[str] = None
    extracted_symptoms: List[str] = Field(default_factory=list)
    urgency_level: str = "ROUTINE"  # ROUTINE, URGENT, EMERGENCY
    preferences: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def update_intent(self, intent: str):
        self.current_intent = intent
        self.updated_at = datetime.utcnow()

    def clear_selection(self):
        self.selected_hospital_id = None
        self.selected_doctor_id = None
        self.selected_slot_id = None
        self.updated_at = datetime.utcnow()
