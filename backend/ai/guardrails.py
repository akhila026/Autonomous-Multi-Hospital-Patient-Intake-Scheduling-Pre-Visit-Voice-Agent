import re
from typing import Optional
from pydantic import BaseModel


class GuardrailResult(BaseModel):
    """Result of clinical guardrail evaluation."""
    passed: bool
    is_emergency: bool = False
    is_clinical_prohibited: bool = False
    refusal_reason: Optional[str] = None
    reply: Optional[str] = None


# Acute life-threatening red flags requiring immediate emergency care
EMERGENCY_PATTERNS = [
    r"\b(chest\s+pain|heart\s+attack|crushing\s+chest|pressure\s+in\s+chest)\b",
    r"\b(can'?t\s+breathe|severe\s+shortness\s+of\s+breath|choking|asphyxia|respiratory\s+arrest)\b",
    r"\b(stroke|facial\s+droop|slurred\s+speech|sudden\s+weakness|hemiparesis|loss\s+of\s+speech)\b",
    r"\b(severe\s+bleeding|uncontrolled\s+bleeding|coughing\s+blood|vomiting\s+blood|hemorrhage)\b",
    r"\b(unconscious|passed\s+out|unresponsive|seizure|convulsing|coma)\b",
    r"\b(anaphylaxis|swollen\s+throat|epipen|severe\s+allergic\s+reaction)\b",
    r"\b(overdose|poisoning|ingested\s+toxin|suicidal\s+thoughts|suicide\s+attempt)\b"
]

# Clinical requests strictly prohibited for an administrative AI assistant
CLINICAL_PROHIBITION_PATTERNS = [
    # 1. Diagnosis
    (
        r"\b(diagnos(e|is|ing)|what\s+(is\s+my\s+diagnosis|disease\s+do\s+i\s+have|illness\s+do\s+i\s+have|condition\s+is\s+this)|give\s+me\s+a\s+diagnosis|do\s+i\s+have\s+(cancer|diabetes|asthma|covid|flu|pneumonia|strep|lupus|infection))\b",
        "DIAGNOSIS_REQUEST",
        "I cannot provide a medical diagnosis. Only a licensed physician can diagnose medical conditions. However, I can help you schedule an appointment with one of our specialized doctors for an in-person or clinical evaluation."
    ),
    # 2. Prescription
    (
        r"\b(prescribe|write\s+a\s+prescription|can\s+you\s+give\s+me\s+(antibiotics|painkillers|adderall|xanax|steroids|ozempic)|refill\s+my\s+prescription|order\s+my\s+meds)\b",
        "PRESCRIPTION_REQUEST",
        "I cannot prescribe medications or refill prescriptions. Prescribing requires direct authorization from a certified medical practitioner. I would be glad to book a consultation for you with a prescribing physician."
    ),
    # 3. Medication Dosage / Modification
    (
        r"\b((can|should)\s+i\s+(double|triple|increase|decrease|change|stop\s+taking)\s+my\s+(dose|medication|pill|dosage)|change\s+my\s+(dose|dosage|medication)|should\s+i\s+stop\s+taking)\b",
        "MEDICATION_CHANGE_REQUEST",
        "I cannot recommend changes to your medication or adjust dosages. Modifying your medication regimen without doctor supervision can be hazardous. Please schedule a follow-up visit with your doctor to review your prescription."
    ),
    # 4. Clinical Treatment Recommendation
    (
        r"\b(how\s+should\s+i\s+treat|recommend\s+a\s+treatment|how\s+to\s+cure|what\s+treatment\s+do\s+i\s+need|cure\s+my)\b",
        "TREATMENT_RECOMMENDATION_REQUEST",
        "I cannot provide clinical treatment plans or recommend home treatments. I can assist you with finding an appropriate clinical department or booking an appointment with an experienced specialist."
    ),
]


class ClinicalGuardrails:
    """
    Strict Clinical Guardrails for Administrative Patient-Access AI (PRD Section 3 & 20).
    Ensures zero clinical assessment, diagnosing, prescribing, or medication modification.
    """

    @staticmethod
    def evaluate(message: str) -> GuardrailResult:
        text = message.strip()
        lower_text = text.lower()

        # 1. Evaluate Emergency Red-Flags
        for pattern in EMERGENCY_PATTERNS:
            if re.search(pattern, lower_text, re.IGNORECASE):
                emergency_reply = (
                    "🚨 **CRITICAL MEDICAL ALERT**: Your reported symptoms indicate a potential medical emergency. "
                    "As an administrative assistant, I cannot treat emergencies. "
                    "**Please call 911 or proceed immediately to your nearest Emergency Room (ER).** "
                    "Do not wait for a routine outpatient appointment."
                )
                return GuardrailResult(
                    passed=False,
                    is_emergency=True,
                    refusal_reason="EMERGENCY_RED_FLAG_DETECTED",
                    reply=emergency_reply
                )

        # 2. Evaluate Clinical Prohibitions
        for pattern, reason, refusal_reply in CLINICAL_PROHIBITION_PATTERNS:
            if re.search(pattern, lower_text, re.IGNORECASE):
                full_reply = (
                    f"⚠️ **Clinical Advisory Notice**: {refusal_reply}\n\n"
                    "Would you like me to find an available doctor in our network for an evaluation?"
                )
                return GuardrailResult(
                    passed=False,
                    is_clinical_prohibited=True,
                    refusal_reason=reason,
                    reply=full_reply
                )

        # 3. Passed all guardrails
        return GuardrailResult(passed=True)
