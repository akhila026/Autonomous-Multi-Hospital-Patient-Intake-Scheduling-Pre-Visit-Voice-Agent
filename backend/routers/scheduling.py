from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend import models, schemas
from backend.scheduling.service import SchedulingService
from backend.auth.roles import UserRole
from backend.auth.tenant import TenantContext
from backend.auth.dependencies import get_current_user, get_optional_current_user

router = APIRouter(prefix="/scheduling", tags=["Scheduling"])

def _format_appointment_response(appt: models.Appointment) -> schemas.AppointmentResponse:
    sync_logs = [
        schemas.EhrSyncLogResponse(
            id=log.id,
            action=log.action,
            status=log.status,
            request_payload=log.request_payload,
            response_payload=log.response_payload,
            response_time_ms=log.response_time_ms,
            error_message=log.error_message,
            created_at=log.created_at
        ) for log in getattr(appt, "sync_logs", [])
    ]

    return schemas.AppointmentResponse(
        id=appt.id,
        patient_id=appt.patient_id,
        patient_name=appt.patient.full_name if appt.patient else None,
        doctor_id=appt.doctor_id,
        doctor_name=appt.doctor.full_name if appt.doctor else None,
        doctor_specialty=appt.doctor.specialty if appt.doctor else None,
        hospital_id=appt.hospital_id,
        hospital_name=appt.hospital.name if appt.hospital else None,
        slot_id=appt.slot_id,
        slot_start=appt.slot.start_time if (appt.slot and appt.slot.start_time) else appt.start_time,
        slot_end=appt.slot.end_time if (appt.slot and appt.slot.end_time) else appt.end_time,
        status=appt.status,
        ehr_appointment_id=appt.ehr_appointment_id,
        idempotency_key=appt.idempotency_key,
        retry_count=appt.retry_count or 0,
        chief_complaint=appt.chief_complaint,
        urgency_level=appt.urgency_level or "ROUTINE",
        ai_triage_notes=appt.ai_triage_notes,
        created_at=appt.created_at,
        updated_at=appt.updated_at,
        sync_logs=sync_logs
    )

@router.get("/availability")
def get_availability(
    doctor_id: str = Query(..., description="Doctor ID to check availability for"),
    start_date: Optional[str] = Query(None, description="Start date in YYYY-MM-DD format"),
    end_date: Optional[str] = Query(None, description="End date in YYYY-MM-DD format"),
    appointment_type: str = Query("IN_PERSON", description="Requested appointment type"),
    calendar_id: Optional[str] = Query(None, description="Specific calendar ID"),
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns strictly bookable discrete time slots adhering to all 7 bookability rules:
    active doctor, active calendar, within working hours, not blocked, not on leave,
    not already booked, and compatible appointment type.
    """
    tenant_context = None
    if current_user:
        is_platform = (current_user.role == UserRole.PLATFORM_ADMIN.value)
        tenant_context = TenantContext(hospital_id=current_user.hospital_id, is_platform_admin=is_platform)

    slots = SchedulingService.check_availability(
        db=db,
        doctor_id=doctor_id,
        start_date=start_date,
        end_date=end_date,
        appointment_type=appointment_type,
        calendar_id=calendar_id,
        tenant_context=tenant_context
    )
    return slots

@router.post("/validate", response_model=schemas.SlotValidationResponse)
def validate_slot(
    req: schemas.SlotValidationRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Real-time validation of a candidate slot or time interval against all 7 PRD rules.
    Returns whether the slot is bookable and the reason if not.
    """
    tenant_context = None
    if current_user:
        is_platform = (current_user.role == UserRole.PLATFORM_ADMIN.value)
        tenant_context = TenantContext(hospital_id=current_user.hospital_id, is_platform_admin=is_platform)

    res = SchedulingService.validate_slot(
        db=db,
        doctor_id=req.doctor_id,
        start_time=req.start_time,
        end_time=req.end_time,
        slot_id=req.slot_id,
        appointment_type=req.appointment_type,
        tenant_context=tenant_context
    )
    return res

@router.post("/reserve", response_model=schemas.AppointmentResponse, status_code=status.HTTP_200_OK)
def create_reservation(
    req: schemas.ReservationCreateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Creates an appointment hold / reservation with atomic double-booking prevention.
    Enforces tenant isolation, doctor ownership, patient authorization, and the 7 rules.
    """
    appt = SchedulingService.create_reservation(db=db, req=req, current_user=current_user)
    return _format_appointment_response(appt)

@router.post("/release/{appointment_id}", response_model=schemas.ReservationReleaseResponse)
def release_reservation(
    appointment_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Releases an active appointment reservation, verifies caller permissions,
    cancels the appointment, and resets the slot to AVAILABLE if it is still valid.
    """
    return SchedulingService.release_slot(db=db, appointment_id=appointment_id, current_user=current_user)
