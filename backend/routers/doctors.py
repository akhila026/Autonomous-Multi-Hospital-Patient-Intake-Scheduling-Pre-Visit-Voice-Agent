from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.services.doctor_service import DoctorService
from backend.services.slot_service import SlotService
from backend.auth.roles import UserRole
from backend.auth.dependencies import (
    require_hospital_admin, get_current_user, verify_doctor_ownership
)

router = APIRouter(prefix="/doctors", tags=["Doctors"])

def doctor_to_response(doc: models.Doctor) -> schemas.DoctorResponse:
    return schemas.DoctorResponse(
        id=doc.id,
        hospital_id=doc.hospital_id,
        hospital_name=doc.hospital.name if doc.hospital else None,
        department_id=doc.department_id,
        full_name=doc.full_name,
        specialty=doc.specialty,
        photo_url=doc.photo_url,
        qualifications=doc.qualifications,
        experience_years=doc.experience_years,
        languages=doc.languages or "English",
        bio=doc.bio,
        consultation_fee=doc.consultation_fee,
        slot_duration_min=doc.slot_duration_min,
        supported_appointment_types=doc.supported_appointment_types or "IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT",
        external_provider_id=doc.external_provider_id,
        status=doc.status or ("ACTIVE" if doc.is_active else "INACTIVE"),
        is_active=doc.is_active,
        created_at=doc.created_at
    )

@router.post("/hospital/{hospital_id}", response_model=schemas.DoctorResponse)
def create_doctor(
    hospital_id: str,
    req: schemas.DoctorCreateRequest,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    """
    Creates a new doctor profile.
    Enforces Tenant Isolation:
    Hospital administrators can only create doctors within their own hospital.
    Attempting to create a doctor for another hospital is rejected with HTTP 403.
    """
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if current_user.hospital_id != hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant Isolation Violation: You are not authorized to create doctors in hospital '{hospital_id}'."
            )

    doc = DoctorService.create_doctor(db, hospital_id, req)
    return doctor_to_response(doc)

@router.patch("/{doctor_id}", response_model=schemas.DoctorResponse)
def update_doctor(
    doctor_id: str,
    req: schemas.DoctorUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Updates doctor clinical details or lifecycle status.
    Enforces Doctor Ownership & Tenant Isolation:
    Doctor can update their own profile.
    Hospital Administrator can update doctors in their facility.
    """
    verify_doctor_ownership(doctor_id, current_user, db)
    doc = DoctorService.update_doctor(db, doctor_id, req)
    return doctor_to_response(doc)

@router.patch("/{doctor_id}/status", response_model=schemas.DoctorResponse)
def update_doctor_status(
    doctor_id: str,
    req: schemas.DoctorStatusUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Updates doctor lifecycle status: INVITED -> ACTIVE -> INACTIVE / SUSPENDED.
    """
    verify_doctor_ownership(doctor_id, current_user, db)
    doc = DoctorService.update_doctor_status(db, doctor_id, req.status)
    return doctor_to_response(doc)

@router.get("", response_model=List[schemas.DoctorResponse])
def list_doctors(
    hospital_id: Optional[str] = Query(None),
    specialty: Optional[str] = Query(None),
    active_only: bool = Query(True),
    db: Session = Depends(get_db)
):
    docs = DoctorService.get_doctors(db, hospital_id=hospital_id, specialty=specialty, active_only=active_only)
    return [doctor_to_response(d) for d in docs]

@router.get("/{doctor_id}", response_model=schemas.DoctorResponse)
def get_doctor(doctor_id: str, db: Session = Depends(get_db)):
    d = DoctorService.get_doctor_by_id(db, doctor_id)
    return doctor_to_response(d)

@router.post("/{doctor_id}/schedules")
def set_doctor_schedules(
    doctor_id: str,
    req: schemas.AvailabilityScheduleSetRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Doctor Ownership:
    A doctor can ONLY manage their own schedule and availability.
    Cross-doctor modification is rejected with HTTP 403 Forbidden.
    Hospital administrators can manage doctors within their own hospital.
    """
    verify_doctor_ownership(doctor_id, current_user, db)

    schedules = DoctorService.set_availability_schedules(db, doctor_id, req.schedules)
    # Auto-generate slots for the next 7 days based on new schedule
    SlotService.generate_slots_for_doctor(db, doctor_id, days_ahead=7)
    return {"message": "Schedules saved and slots generated successfully", "count": len(schedules)}

@router.post("/{doctor_id}/generate-slots", response_model=List[schemas.TimeSlotResponse])
def generate_slots(
    doctor_id: str,
    req: schemas.SlotGenerateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Doctor Ownership for generating slots.
    """
    verify_doctor_ownership(doctor_id, current_user, db)
    slots = SlotService.generate_slots_for_doctor(db, doctor_id, days_ahead=req.days_ahead)
    return slots

@router.get("/{doctor_id}/slots", response_model=List[schemas.TimeSlotResponse])
def get_doctor_slots(
    doctor_id: str,
    date: Optional[str] = Query(None),
    available_only: bool = Query(True),
    db: Session = Depends(get_db)
):
    return SlotService.get_doctor_slots(db, doctor_id, target_date=date, only_available=available_only)

# --- Blocked Slots Endpoints ---
@router.get("/{doctor_id}/blocked-slots", response_model=List[schemas.BlockedSlotResponse])
def list_doctor_blocked_slots(
    doctor_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_doctor_ownership(doctor_id, current_user, db)
    return DoctorService.list_blocked_slots(db, doctor_id)

@router.post("/{doctor_id}/blocked-slots", response_model=schemas.BlockedSlotResponse)
def create_doctor_blocked_slot(
    doctor_id: str,
    req: schemas.BlockedSlotCreateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_doctor_ownership(doctor_id, current_user, db)
    doc = DoctorService.get_doctor_by_id(db, doctor_id)
    return DoctorService.create_blocked_slot(db, doctor_id, doc.hospital_id, req)

@router.delete("/{doctor_id}/blocked-slots/{blocked_slot_id}")
def delete_doctor_blocked_slot(
    doctor_id: str,
    blocked_slot_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_doctor_ownership(doctor_id, current_user, db)
    success = DoctorService.delete_blocked_slot(db, doctor_id, blocked_slot_id)
    return {"message": "Blocked slot deleted successfully", "success": success}

# --- Doctor Authorized Appointments & Pre-Visit Intake ---
@router.get("/{doctor_id}/appointments")
def get_doctor_appointments(
    doctor_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_doctor_ownership(doctor_id, current_user, db)
    appts = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id
    ).order_by(models.Appointment.start_time.asc()).all()
    return [
        {
            "id": a.id,
            "patient_name": a.patient.full_name if a.patient else "Patient",
            "patient_id": a.patient_id,
            "start_time": a.start_time.isoformat() if a.start_time else None,
            "end_time": a.end_time.isoformat() if a.end_time else None,
            "status": a.status,
            "chief_complaint": a.chief_complaint,
            "ehr_appointment_id": a.ehr_appointment_id
        }
        for a in appts
    ]

@router.get("/{doctor_id}/intake-responses")
def get_doctor_intake_responses(
    doctor_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_doctor_ownership(doctor_id, current_user, db)
    responses = db.query(models.QuestionnaireResponse).join(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id
    ).order_by(models.QuestionnaireResponse.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "appointment_id": r.appointment_id,
            "patient_name": r.patient.full_name if r.patient else "Patient",
            "status": r.status,
            "is_urgent": r.is_urgent,
            "urgent_reasons": r.urgent_reasons,
            "patient_intake_summary": r.patient_intake_summary,
            "doctor_notes": r.doctor_notes,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None
        }
        for r in responses
    ]
