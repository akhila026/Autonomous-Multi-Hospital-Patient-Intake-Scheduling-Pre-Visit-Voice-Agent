import re
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class ExtractedEntities(BaseModel):
    specialty: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_id: Optional[str] = None
    slot_id: Optional[str] = None
    appointment_id: Optional[str] = None
    hospital_name: Optional[str] = None
    target_date: Optional[str] = None
    time_of_day: Optional[str] = None
    chief_complaint: Optional[str] = None
    answers: Dict[str, Any] = Field(default_factory=dict)


class DetectedIntent(BaseModel):
    intent: str
    confidence: float
    entities: ExtractedEntities
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None


SPECIALTY_KEYWORDS = {
    "Orthopedics": ["ortho", "orthopedic", "bone", "joint", "knee", "shoulder", "ankle", "sprain", "spine", "fracture", "swollen", "twisted", "back pain", "neck pain", "leg pain", "arm pain"],
    "Cardiology": ["cardio", "cardiology", "heart", "palpitation", "blood pressure", "hypertension", "angina"],
    "Dermatology": ["derma", "dermatology", "skin", "rash", "acne", "mole", "eczema", "hives"],
    "General Medicine": ["general", "primary care", "physician", "checkup", "flu", "cold", "fever", "headache", "migraine", "annual", "dizziness", "fatigue", "sore throat", "cough", "infection", "sick", "nausea", "unwell"],
    "Pediatrics": ["pediatric", "child", "baby", "infant"],
    "Gastroenterology": ["gastro", "stomach", "digestive", "acid reflux", "gerd", "bloating", "cramps"],
    "Ophthalmology": ["eye", "vision", "cataract", "optometry"]
}


class IntentDetector:
    """
    Detects administrative healthcare intents and extracts scheduling parameters.
    Maintains zero clinical assessment logic (PRD Section 10).
    """

    @staticmethod
    def detect(message: str, context: Optional[Any] = None) -> DetectedIntent:
        lower = message.lower().strip()
        entities = ExtractedEntities()

        # 1. Extract Specialty
        for spec, keywords in SPECIALTY_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                entities.specialty = spec
                entities.chief_complaint = message
                break

        # 2. Extract Doctor Name Pattern (e.g. 'Dr. Sarah Jenkins', 'Dr. Vance')
        doc_match = re.search(r"(dr\.?\s+[a-z]+(\s+[a-z]+)?)", lower)
        if doc_match:
            entities.doctor_name = doc_match.group(1).title()

        # 3. Extract Time of Day
        if "morning" in lower:
            entities.time_of_day = "morning"
        elif "afternoon" in lower or "evening" in lower:
            entities.time_of_day = "afternoon"

        # 4. Check for Escalation Intent
        if any(w in lower for w in ["human", "agent", "real person", "operator", "representative", "transfer me", "speak to someone"]):
            return DetectedIntent(
                intent="ESCALATE_TO_HUMAN",
                confidence=0.98,
                entities=entities
            )

        # 5. Check for Questionnaire Submission Intent
        is_awaiting_questionnaire = False
        if context and hasattr(context, "completed_workflow_state"):
            if "APPOINTMENT_CONFIRMED" in context.completed_workflow_state and "QUESTIONNAIRE_SUBMITTED" not in context.completed_workflow_state:
                is_awaiting_questionnaire = True

        if ("questionnaire" in lower or "survey" in lower or "answers" in lower or "intake" in lower) or (
            is_awaiting_questionnaire and any(w in lower for w in ["symptom", "started", "days ago", "hours ago", "pain", "since", "injury", "felt", "began", "twisting"])
        ):
            return DetectedIntent(
                intent="FILL_QUESTIONNAIRE",
                confidence=0.92,
                entities=entities
            )

        # 6. Check for Reschedule Intent
        if any(w in lower for w in ["reschedule", "change my time", "change appointment", "move my appointment", "different slot"]):
            return DetectedIntent(
                intent="RESCHEDULE_APPOINTMENT",
                confidence=0.95,
                entities=entities
            )

        # 7. Check for Cancel Intent
        if any(w in lower for w in ["cancel my appointment", "cancel appointment", "cancel it", "won't make it", "cannot make it"]):
            return DetectedIntent(
                intent="CANCEL_APPOINTMENT",
                confidence=0.95,
                entities=entities
            )

        # 8. Check for Retrieval / Status Intent
        if any(w in lower for w in ["when is my appointment", "appointment details", "check my appointment", "view my appointment", "my visit"]):
            return DetectedIntent(
                intent="RETRIEVE_APPOINTMENT",
                confidence=0.90,
                entities=entities
            )

        # 9. Check for Hospital Search Intent
        if "hospital" in lower or "clinic" in lower or "facility" in lower:
            return DetectedIntent(
                intent="SEARCH_HOSPITALS",
                confidence=0.88,
                entities=entities
            )

        # 10. Check for Direct Booking / Consultation Request Intent
        if (
            any(w in lower for w in ["book", "reserve", "schedule an appointment", "make an appointment", "confirm appointment", "that slot", "book it"])
            or (entities.doctor_name and any(w in lower for w in ["see", "visit", "consult", "meet", "appointment", "schedule", "need"]))
            or ("need to see" in lower or "want to see" in lower)
            or (context and getattr(context, "last_slots_listed", None) and (
                any(w in lower for w in ["option", "slot", "earliest", "first", "second", "third", "choice"]) or
                bool(re.search(r"\b(?:option|slot)?\s*[1-9]\b", lower))
            ))
        ):
            return DetectedIntent(
                intent="BOOK_APPOINTMENT",
                confidence=0.92,
                entities=entities
            )

        # 11. Check for Availability Intent
        if any(w in lower for w in ["availability", "available times", "free slots", "open slots", "when are they free", "time slots"]):
            return DetectedIntent(
                intent="CHECK_AVAILABILITY",
                confidence=0.90,
                entities=entities
            )

        # 12. Check for Doctor Search Intent
        if any(w in lower for w in ["doctor", "physician", "specialist", "find a doctor", "cardiologist", "orthopedic", "dermatologist", "dr."]) or entities.doctor_name:
            return DetectedIntent(
                intent="SEARCH_DOCTORS",
                confidence=0.88,
                entities=entities
            )

        # 13. Greeting
        if re.search(r"\b(hello|hi|hey|good\s+morning|good\s+afternoon|greetings)\b", lower):
            return DetectedIntent(
                intent="GREETING",
                confidence=0.95,
                entities=entities
            )

        # 14. Fallback General Inquiry
        return DetectedIntent(
            intent="GENERAL_INQUIRY",
            confidence=0.50,
            entities=entities
        )
