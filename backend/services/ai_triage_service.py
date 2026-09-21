import re
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from backend.config import settings
from backend import models, schemas
from backend.services.doctor_service import DoctorService
from backend.services.slot_service import SlotService

# Emergency red-flag patterns
EMERGENCY_PATTERNS = [
    r"\b(chest\s+pain|heart\s+attack|pressure\s+in\s+chest)\b",
    r"\b(can'?t\s+breathe|difficulty\s+breathing|choking|shortness\s+of\s+breath)\b",
    r"\b(stroke|face\s+droop|slurred\s+speech|sudden\s+numbness|paralysis)\b",
    r"\b(severe\s+bleeding|coughing\s+blood|vomiting\s+blood)\b",
    r"\b(unconscious|passed\s+out|seizure|convulsion)\b",
]

# Specialty symptom keywords
SPECIALTY_RULES = {
    "Orthopedics": [
        "knee", "joint", "shoulder", "hip", "ankle", "sprain", "bone",
        "fracture", "swollen knee", "ligament", "torn acl", "back pain",
        "spine", "cartilage", "runner's knee", "tendon"
    ],
    "Dermatology": [
        "skin", "rash", "itch", "itchy", "acne", "mole", "eczema",
        "psoriasis", "hives", "bump", "blister", "dermatitis", "red spots"
    ],
    "Cardiology": [
        "heart", "palpitations", "racing pulse", "arrhythmia", "high blood pressure",
        "hypertension", "murmur", "angina"
    ],
    "General Medicine": [
        "fever", "headache", "fatigue", "cold", "flu", "cough", "sore throat",
        "dizziness", "weakness", "chills", "mild pain", "checkup", "general"
    ],
    "Gastroenterology": [
        "stomach", "abdominal", "acid reflux", "gerd", "heartburn", "nausea",
        "vomiting", "diarrhea", "constipation", "bloating", "cramps"
    ],
    "Ophthalmology": [
        "eye", "vision", "blurred vision", "pink eye", "conjunctivitis", "stye", "cornea"
    ],
    "Pediatrics": [
        "child", "baby", "infant", "toddler", "pediatric"
    ]
}

class AITriageService:
    @staticmethod
    def process_triage(
        db: Session,
        req: schemas.AITriageChatRequest
    ) -> schemas.AITriageChatResponse:
        user_text = req.message.lower().strip()

        # 1. Check for Emergency Red-Flags
        for pattern in EMERGENCY_PATTERNS:
            if re.search(pattern, user_text, re.IGNORECASE):
                return schemas.AITriageChatResponse(
                    reply=(
                        "⚠️ **CRITICAL MEDICAL ALERT**: Based on the symptoms described (such as chest pain, "
                        "difficulty breathing, or severe sudden symptoms), you may be experiencing an acute medical emergency. "
                        "Please call **911** or proceed to the nearest **Emergency Room (ER)** immediately. "
                        "Do not wait for an outpatient appointment."
                    ),
                    specialty_recommended="Emergency Medicine",
                    urgency_level="EMERGENCY",
                    is_emergency=True,
                    chief_complaint=req.message,
                    suggested_doctors=[],
                    available_slots=[]
                )

        # 2. Determine Specialty via Clinical Rule / NLP Match
        matched_specialty = "General Medicine"
        max_hits = 0

        for specialty, keywords in SPECIALTY_RULES.items():
            hits = sum(1 for kw in keywords if kw in user_text)
            if hits > max_hits:
                max_hits = hits
                matched_specialty = specialty

        # Assess urgency level
        urgency = "ROUTINE"
        if any(w in user_text for w in ["severe", "unbearable", "sudden", "since this morning", "getting worse", "can't walk"]):
            urgency = "URGENT"

        # 3. Agentic Tool Calling: Discover doctors & real slots
        doctors = DoctorService.get_doctors(db, specialty=matched_specialty, active_only=True)
        if not doctors and matched_specialty != "General Medicine":
            # Fallback to general medicine if no specialist found
            doctors = DoctorService.get_doctors(db, specialty="General Medicine", active_only=True)
            if not doctors:
                # Get any active doctors
                doctors = DoctorService.get_doctors(db, active_only=True)

        doctor_schemas = []
        available_slots = []

        for doc in doctors[:3]:
            doc_schema = schemas.DoctorResponse(
                id=doc.id,
                hospital_id=doc.hospital_id,
                hospital_name=doc.hospital.name if doc.hospital else None,
                full_name=doc.full_name,
                specialty=doc.specialty,
                bio=doc.bio,
                consultation_fee=doc.consultation_fee,
                slot_duration_min=doc.slot_duration_min,
                is_active=doc.is_active,
                created_at=doc.created_at
            )
            doctor_schemas.append(doc_schema)

            # Fetch up to 3 available slots for this doctor
            slots = SlotService.get_doctor_slots(db, doc.id, only_available=True)
            for s in slots[:3]:
                available_slots.append(schemas.TimeSlotResponse.model_validate(s))

        # 4. Formulate conversational response
        if doctors:
            top_doc = doctors[0]
            slot_count = len(available_slots)

            reply_text = (
                f"I understand your concerns regarding {req.message.strip()}. "
                f"Based on your symptoms, a consultation in **{matched_specialty}** is recommended. "
                f"I've located **{top_doc.full_name}** ({top_doc.specialty} at {top_doc.hospital.name if top_doc.hospital else 'our clinic'}), "
                f"with {slot_count} available appointment slot{'s' if slot_count != 1 else ''}. "
                f"Would you like to reserve one of the slots shown below?"
            )
        else:
            reply_text = (
                f"I understand you are experiencing symptoms related to {matched_specialty}. "
                f"We are currently onboarding specialists in this area. "
                f"Please check back shortly or register with our primary care team."
            )

        return schemas.AITriageChatResponse(
            reply=reply_text,
            specialty_recommended=matched_specialty,
            urgency_level=urgency,
            is_emergency=False,
            chief_complaint=req.message,
            suggested_doctors=doctor_schemas,
            available_slots=available_slots
        )
