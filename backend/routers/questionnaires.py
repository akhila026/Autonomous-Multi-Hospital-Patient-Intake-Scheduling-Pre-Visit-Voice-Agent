from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.services.questionnaire_service import QuestionnaireService
from backend.auth.roles import UserRole
from backend.auth.dependencies import get_optional_current_user, get_current_user

router = APIRouter(prefix="/questionnaires", tags=["Pre-Visit Questionnaires"])


# =====================================================================
# 1. TEMPLATE MANAGEMENT ENDPOINTS (Admin / Doctor Configuration)
# =====================================================================

@router.post("/templates", response_model=schemas.QuestionnaireTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    req: schemas.QuestionnaireTemplateCreate,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Configures an approved pre-visit questionnaire template."""
    return QuestionnaireService.create_template(db, req, current_user)


@router.get("/templates", response_model=List[schemas.QuestionnaireTemplateResponse])
def list_templates(
    hospital_id: Optional[str] = Query(None),
    specialty_id: Optional[str] = Query(None),
    doctor_id: Optional[str] = Query(None),
    appointment_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Retrieves active pre-visit questionnaire templates with optional filters."""
    return QuestionnaireService.get_templates(
        db,
        hospital_id=hospital_id,
        specialty_id=specialty_id,
        doctor_id=doctor_id,
        appointment_type=appointment_type
    )


@router.get("/templates/{template_id}", response_model=schemas.QuestionnaireTemplateResponse)
def get_template(
    template_id: str,
    db: Session = Depends(get_db)
):
    """Retrieves a single questionnaire template by ID."""
    return QuestionnaireService.get_template_by_id(db, template_id)


@router.put("/templates/{template_id}", response_model=schemas.QuestionnaireTemplateResponse)
def update_template(
    template_id: str,
    req: schemas.QuestionnaireTemplateUpdate,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Updates an existing questionnaire template."""
    return QuestionnaireService.update_template(db, template_id, req, current_user)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Deactivates a questionnaire template."""
    QuestionnaireService.delete_template(db, template_id, current_user)
    return None


# =====================================================================
# 2. DOCTOR REVIEW QUEUE ENDPOINTS
# =====================================================================

@router.get("/doctor/{doctor_id}/pending-reviews")
def get_doctor_pending_reviews(
    doctor_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Retrieves questionnaires submitted or urgently flagged for a doctor's appointments."""
    if current_user and current_user.role == UserRole.DOCTOR.value:
        doc_profile = getattr(current_user, "doctor_profile", None)
        if doc_profile and doc_profile.id != doctor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doctor Review Authorization Violation: Cannot view another doctor's review queue."
            )
    return QuestionnaireService.get_pending_reviews_for_doctor(db, doctor_id)


@router.post("/{appointment_id}/review", response_model=schemas.DoctorReviewResponse)
def submit_doctor_review(
    appointment_id: str,
    req: schemas.DoctorReviewRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Records doctor sign-off and clinical review notes for a submitted questionnaire."""
    doctor_id = None
    if current_user and current_user.role == UserRole.DOCTOR.value:
        doc_profile = getattr(current_user, "doctor_profile", None)
        doctor_id = doc_profile.id if doc_profile else current_user.id
    elif current_user:
        doctor_id = current_user.id
    else:
        # If unauthenticated in test environment, extract from appointment
        quest = QuestionnaireService.get_by_appointment(db, appointment_id)
        doctor_id = quest.appointment.doctor_id if quest.appointment else "DOC-UNKNOWN"

    updated = QuestionnaireService.submit_doctor_review(
        db,
        appointment_id=appointment_id,
        doctor_id=doctor_id,
        doctor_notes=req.doctor_notes
    )
    return schemas.DoctorReviewResponse(
        id=updated.id,
        appointment_id=updated.appointment_id,
        patient_id=updated.patient_id,
        doctor_id=doctor_id,
        reviewed_at=updated.reviewed_at,
        status=updated.status,
        doctor_notes=updated.doctor_notes
    )


# =====================================================================
# 3. CONVERSATIONAL INTAKE & PATIENT SUBMISSION ENDPOINTS
# =====================================================================

@router.post("/{appointment_id}/conversational-turn", response_model=schemas.QuestionnaireConversationalTurnResponse)
def conversational_turn(
    appointment_id: str,
    req: schemas.QuestionnaireConversationalTurnRequest,
    db: Session = Depends(get_db)
):
    """Processes a conversational turn for questionnaire collection and evaluates urgency."""
    return QuestionnaireService.process_conversational_turn(
        db=db,
        appointment_id=appointment_id,
        patient_utterance=req.patient_utterance
    )


@router.get("/{appointment_id}", response_model=schemas.QuestionnaireResponse)
def get_questionnaire(
    appointment_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Retrieves the pre-visit questionnaire for a given appointment."""
    quest = QuestionnaireService.get_by_appointment(db, appointment_id)

    if current_user:
        # Patient Ownership
        if current_user.role == UserRole.PATIENT.value:
            patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
            is_owner = (quest.patient_id == current_user.id) or (patient_id and quest.patient_id == patient_id)
            if not is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient Ownership Violation: You are not authorized to view another patient's questionnaire."
                )
        # Hospital Admin Tenant Isolation
        elif current_user.role == UserRole.HOSPITAL_ADMIN.value:
            if quest.appointment and quest.appointment.hospital_id != current_user.hospital_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant Isolation Violation: You cannot view questionnaires belonging to another hospital."
                )

    return schemas.QuestionnaireResponse(
        id=quest.id,
        appointment_id=quest.appointment_id,
        patient_id=quest.patient_id,
        questions=quest.questions or [],
        answers=quest.answers,
        patient_intake_summary=quest.patient_intake_summary,
        ai_clinical_summary=quest.patient_intake_summary,
        status=quest.status,
        is_urgent=bool(getattr(quest, "is_urgent", False)),
        urgent_reasons=getattr(quest, "urgent_reasons", None),
        reviewed_by=getattr(quest, "reviewed_by", None),
        reviewed_at=getattr(quest, "reviewed_at", None),
        doctor_notes=getattr(quest, "doctor_notes", None),
        submitted_at=quest.submitted_at
    )


@router.post("/{appointment_id}/submit", response_model=schemas.QuestionnaireResponse)
def submit_questionnaire(
    appointment_id: str,
    req: schemas.QuestionnaireSubmitRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Directly submits questionnaire answers, evaluates urgency, and compiles intake summary."""
    quest = QuestionnaireService.get_by_appointment(db, appointment_id)

    if current_user and current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        is_owner = (quest.patient_id == current_user.id) or (patient_id and quest.patient_id == patient_id)
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You cannot submit answers for another patient's questionnaire."
            )

    quest = QuestionnaireService.submit_answers(db, appointment_id, req)
    return schemas.QuestionnaireResponse(
        id=quest.id,
        appointment_id=quest.appointment_id,
        patient_id=quest.patient_id,
        questions=quest.questions or [],
        answers=quest.answers,
        patient_intake_summary=quest.patient_intake_summary,
        ai_clinical_summary=quest.patient_intake_summary,
        status=quest.status,
        is_urgent=bool(getattr(quest, "is_urgent", False)),
        urgent_reasons=getattr(quest, "urgent_reasons", None),
        reviewed_by=getattr(quest, "reviewed_by", None),
        reviewed_at=getattr(quest, "reviewed_at", None),
        doctor_notes=getattr(quest, "doctor_notes", None),
        submitted_at=quest.submitted_at
    )
