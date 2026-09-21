import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.services.appointment_service import AppointmentService
from backend.auth.roles import UserRole
from backend.auth.dependencies import (
    get_current_user, get_optional_current_user, verify_doctor_ownership
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/appointments", tags=["Appointments"])

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
        ) for log in appt.sync_logs
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
        slot_start=appt.slot.start_time if appt.slot else None,
        slot_end=appt.slot.end_time if appt.slot else None,
        status=appt.status,
        ehr_appointment_id=appt.ehr_appointment_id,
        idempotency_key=appt.idempotency_key,
        retry_count=appt.retry_count,
        chief_complaint=appt.chief_complaint,
        urgency_level=appt.urgency_level,
        ai_triage_notes=appt.ai_triage_notes,
        cancellation_reason=getattr(appt, "cancellation_reason", None),
        cancelled_at=getattr(appt, "cancelled_at", None),
        created_at=appt.created_at,
        updated_at=appt.updated_at,
        sync_logs=sync_logs
    )

@router.post("/hold")
def hold_appointment_slot(
    req: schemas.SlotHoldRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    # Enforce patient ownership if authenticated as patient
    if current_user and current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        if not patient_id:
            p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
            patient_id = p.id if p else current_user.id
        if req.patient_id and req.patient_id != patient_id and req.patient_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You cannot hold a slot for another patient."
            )

    appt = AppointmentService.create_slot_hold(db, req)
    return {"message": "Slot held provisionally for 5 minutes", "appointment_id": appt.id, "slot_id": appt.slot_id}

@router.post("/confirm", response_model=schemas.AppointmentResponse)
async def confirm_appointment(
    req: schemas.AppointmentConfirmRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    # Enforce patient ownership if authenticated as patient
    if current_user and current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        if not patient_id:
            p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
            patient_id = p.id if p else current_user.id
        if req.patient_id and req.patient_id != patient_id and req.patient_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You cannot confirm an appointment for another patient."
            )
        if not req.patient_id:
            req.patient_id = patient_id

    appt = await AppointmentService.confirm_and_sync_appointment(db, req)
    return _format_appointment_response(appt)

@router.get("/{appointment_id}", response_model=schemas.AppointmentResponse)
def get_appointment(
    appointment_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Patient & Doctor Ownership, and Tenant Isolation:
    - Patients can ONLY view their own appointment.
    - Doctors can view their own appointments.
    - Hospital Admins can only view appointments for their own hospital.
    """
    appt = AppointmentService.get_appointment_by_id(db, appointment_id)

    # 1. Platform Admin override
    if current_user.role == UserRole.PLATFORM_ADMIN.value:
        return _format_appointment_response(appt)

    # 2. Patient Ownership
    if current_user.role == UserRole.PATIENT.value:
        is_owner = (appt.patient_id == current_user.id) or (
            hasattr(current_user, "patient_profile") and current_user.patient_profile and appt.patient_id == current_user.patient_profile.id
        ) or (appt.patient and appt.patient.user_id == current_user.id)
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You are not authorized to view another patient's appointment."
            )
        return _format_appointment_response(appt)

    # 3. Doctor Ownership
    if current_user.role == UserRole.DOCTOR.value:
        is_owner = (appt.doctor_id == current_user.id) or (
            hasattr(current_user, "doctor_profile") and current_user.doctor_profile and appt.doctor_id == current_user.doctor_profile.id
        ) or (appt.doctor and appt.doctor.user_id == current_user.id)
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doctor Ownership Violation: You can only view your own scheduled appointments."
            )
        return _format_appointment_response(appt)

    # 4. Hospital Admin Tenant Isolation
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if appt.hospital_id != current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation Violation: You cannot view appointments belonging to another hospital."
            )
        return _format_appointment_response(appt)

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

@router.get("/doctor/{doctor_id}/queue", response_model=List[schemas.AppointmentResponse])
def get_doctor_queue(
    doctor_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Doctor Ownership for queue access.
    """
    verify_doctor_ownership(doctor_id, current_user, db)
    appts = AppointmentService.get_doctor_queue(db, doctor_id)
    return [_format_appointment_response(a) for a in appts]

@router.post("/{appointment_id}/reschedule", response_model=schemas.AppointmentResponse)
async def reschedule_appointment(
    appointment_id: str,
    req: schemas.AppointmentRescheduleRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reschedules an existing appointment to a new available slot.
    Enforces Patient Ownership: Patients can only reschedule their own appointments.
    Updates slot allocations, Mock EHR records, and reminder schedules.
    """
    appt = AppointmentService.get_appointment_by_id(db, appointment_id)

    # 1. Enforce Ownership
    if current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        if not patient_id:
            p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
            patient_id = p.id if p else current_user.id
        is_owner = (appt.patient_id == current_user.id) or (appt.patient_id == patient_id) or (appt.patient and appt.patient.user_id == current_user.id)
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You are not authorized to reschedule another patient's appointment."
            )
    elif current_user.role == UserRole.DOCTOR.value:
        doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else current_user.id
        if appt.doctor_id != doctor_id and appt.doctor_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Doctor Ownership Violation: You can only reschedule your own appointments.")
    elif current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if appt.hospital_id != current_user.hospital_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant Isolation Violation: Cross-hospital rescheduling is prohibited.")

    if appt.status in ("CANCELLED", "COMPLETED"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot reschedule an appointment with status '{appt.status}'.")

    # 2. Check Target Slot
    new_slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == req.new_slot_id).first()
    if not new_slot:
        raise HTTPException(status_code=404, detail="Target slot not found.")
    if new_slot.status != "AVAILABLE":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Target slot is not available for rescheduling.")

    if new_slot.doctor and new_slot.doctor.hospital_id != appt.hospital_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target slot belongs to a different hospital tenant.")

    # 3. Transition slots
    if appt.slot:
        appt.slot.status = "AVAILABLE"
        appt.slot.hold_expires_at = None

    new_slot.status = "BOOKED"
    appt.slot_id = new_slot.id
    appt.start_time = new_slot.start_time
    appt.end_time = new_slot.end_time
    appt.status = "RESCHEDULED"
    appt.updated_at = datetime.utcnow()

    # 4. Sync with EHR
    if appt.ehr_appointment_id:
        try:
            from backend.ehr.connector import get_ehr_connector
            connector = get_ehr_connector()
            await connector.reschedule_appointment(
                external_appointment_id=appt.ehr_appointment_id,
                new_slot_time=new_slot.start_time.isoformat()
            )
        except Exception as ehr_err:
            logger.warning(f"Mock EHR reschedule sync notice: {ehr_err}")

    # 5. Reschedule Reminders
    pending_reminders = db.query(models.ReminderLog).filter(
        models.ReminderLog.appointment_id == appt.id,
        models.ReminderLog.status == "SCHEDULED"
    ).all()
    for r in pending_reminders:
        if r.reminder_type == "PRE_VISIT_24H":
            r.scheduled_for = new_slot.start_time - timedelta(hours=24)
        elif r.reminder_type == "PRE_VISIT_1H":
            r.scheduled_for = new_slot.start_time - timedelta(hours=1)

    r_resched = models.ReminderLog(
        appointment_id=appt.id,
        reminder_type="RESCHEDULE_CONFIRMATION",
        scheduled_for=datetime.utcnow(),
        status="SENT",
        channel=appt.patient.communication_preference if (appt.patient and appt.patient.communication_preference) else "SMS",
        message_content=f"Your appointment with {appt.doctor.full_name if appt.doctor else 'your doctor'} has been rescheduled to {new_slot.start_time.strftime('%b %d at %I:%M %p')}.",
        sent_at=datetime.utcnow()
    )
    db.add(r_resched)

    # 6. Audit & Domain Event
    db.add(models.AuditEvent(
        correlation_id=str(uuid.uuid4()),
        hospital_id=appt.hospital_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        action="APPOINTMENT_RESCHEDULED",
        resource_type="APPOINTMENT",
        resource_id=appt.id,
        status="SUCCESS",
        details={
            "new_slot_id": new_slot.id,
            "new_start_time": new_slot.start_time.isoformat(),
            "reason": req.reason
        }
    ))
    db.commit()
    db.refresh(appt)

    try:
        from backend.workflows.events import event_dispatcher, DomainEvent, EventType
        await event_dispatcher.dispatch(DomainEvent(
            event_type=EventType.APPOINTMENT_RESCHEDULED,
            entity_id=appt.id,
            tenant_id=appt.hospital_id,
            correlation_id=str(uuid.uuid4()),
            payload={
                "appointment_id": appt.id,
                "doctor_name": appt.doctor.full_name if appt.doctor else "Doctor",
                "patient_name": appt.patient.full_name if appt.patient else "Patient",
                "slot_time": str(new_slot.start_time),
                "reason": req.reason
            }
        ))
    except Exception as ev_err:
        logger.warning(f"Failed to dispatch APPOINTMENT_RESCHEDULED event: {ev_err}")

    return _format_appointment_response(appt)

@router.post("/{appointment_id}/cancel", response_model=schemas.AppointmentResponse)
async def cancel_appointment(
    appointment_id: str,
    req: Optional[schemas.AppointmentCancelRequest] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Cancels an existing appointment, releases the held time slot, and updates Mock EHR.
    Enforces Patient Ownership: Patients can only cancel their own appointments.
    """
    appt = AppointmentService.get_appointment_by_id(db, appointment_id)

    # 1. Enforce Ownership
    if current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        if not patient_id:
            p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
            patient_id = p.id if p else current_user.id
        is_owner = (appt.patient_id == current_user.id) or (appt.patient_id == patient_id) or (appt.patient and appt.patient.user_id == current_user.id)
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You are not authorized to cancel another patient's appointment."
            )
    elif current_user.role == UserRole.DOCTOR.value:
        doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else current_user.id
        if appt.doctor_id != doctor_id and appt.doctor_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Doctor Ownership Violation: You can only cancel your own appointments.")
    elif current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if appt.hospital_id != current_user.hospital_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant Isolation Violation: Cross-hospital cancellation is prohibited.")

    if appt.status == "CANCELLED":
        return _format_appointment_response(appt)
    if appt.status == "COMPLETED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot cancel an already completed appointment.")

    # 2. Release slot
    if appt.slot:
        appt.slot.status = "AVAILABLE"
        appt.slot.hold_expires_at = None

    reason = (req.reason if req else None) or "Patient requested cancellation"
    appt.status = "CANCELLED"
    appt.cancellation_reason = reason
    appt.cancelled_at = datetime.utcnow()
    appt.updated_at = datetime.utcnow()

    # 3. Sync with EHR
    if appt.ehr_appointment_id:
        try:
            from backend.ehr.connector import get_ehr_connector
            connector = get_ehr_connector()
            await connector.cancel_appointment(
                external_appointment_id=appt.ehr_appointment_id,
                reason=reason
            )
        except Exception as ehr_err:
            logger.warning(f"Mock EHR cancel sync notice: {ehr_err}")

    # 4. Cancel scheduled reminders
    pending_reminders = db.query(models.ReminderLog).filter(
        models.ReminderLog.appointment_id == appt.id,
        models.ReminderLog.status == "SCHEDULED"
    ).all()
    for r in pending_reminders:
        r.status = "CANCELLED"

    # 5. Audit & Domain Event
    db.add(models.AuditEvent(
        correlation_id=str(uuid.uuid4()),
        hospital_id=appt.hospital_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        action="APPOINTMENT_CANCELLED",
        resource_type="APPOINTMENT",
        resource_id=appt.id,
        status="SUCCESS",
        details={"reason": reason}
    ))
    db.commit()
    db.refresh(appt)

    try:
        from backend.workflows.events import event_dispatcher, DomainEvent, EventType
        await event_dispatcher.dispatch(DomainEvent(
            event_type=EventType.APPOINTMENT_CANCELLED,
            entity_id=appt.id,
            tenant_id=appt.hospital_id,
            correlation_id=str(uuid.uuid4()),
            payload={
                "appointment_id": appt.id,
                "doctor_name": appt.doctor.full_name if appt.doctor else "Doctor",
                "patient_name": appt.patient.full_name if appt.patient else "Patient",
                "reason": reason
            }
        ))
    except Exception as ev_err:
        logger.warning(f"Failed to dispatch APPOINTMENT_CANCELLED event: {ev_err}")

    return _format_appointment_response(appt)

@router.get("", response_model=List[schemas.AppointmentResponse])
def list_appointments(
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Lists appointments scoped by caller's role and tenant:
    - Patients: only own appointments
    - Doctors: only appointments assigned to them
    - Hospital Admins: only appointments for their hospital
    - Platform Admins: all appointments
    """
    q = db.query(models.Appointment)

    if current_user:
        if current_user.role == UserRole.PATIENT.value:
            patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
            if not patient_id:
                p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
                patient_id = p.id if p else current_user.id
            q = q.filter((models.Appointment.patient_id == patient_id) | (models.Appointment.patient_id == current_user.id))
        elif current_user.role == UserRole.DOCTOR.value:
            doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else None
            q = q.filter((models.Appointment.doctor_id == doctor_id) | (models.Appointment.doctor_id == current_user.id))
        elif current_user.role == UserRole.HOSPITAL_ADMIN.value:
            if current_user.hospital_id:
                q = q.filter(models.Appointment.hospital_id == current_user.hospital_id)

    if status:
        q = q.filter(models.Appointment.status == status)

    appts = q.order_by(models.Appointment.created_at.desc()).limit(limit).all()
    return [_format_appointment_response(a) for a in appts]

@router.post("/{appointment_id}/status", response_model=schemas.AppointmentResponse)
def update_appointment_status(
    appointment_id: str,
    req: schemas.AppointmentStatusUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Updates the explicit appointment status.
    Permitted for Doctors, Hospital Admins, and Platform Admins.
    Patients cannot manually override clinical appointment statuses.
    """
    appt = AppointmentService.get_appointment_by_id(db, appointment_id)
    if current_user.role == UserRole.PATIENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patients cannot directly alter appointment operational status. Please use cancel/reschedule."
        )
    elif current_user.role == UserRole.DOCTOR.value:
        doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else current_user.id
        if appt.doctor_id != doctor_id and appt.doctor_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Doctor Ownership Violation: You can only update your own appointments.")
    elif current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if appt.hospital_id != current_user.hospital_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant Isolation Violation: Cross-hospital status modification is prohibited.")

    updated_appt = AppointmentService.update_appointment_status(
        db=db,
        appointment_id=appointment_id,
        new_status=req.status,
        actor_id=current_user.id,
        actor_role=current_user.role,
        notes=req.notes
    )
    return _format_appointment_response(updated_appt)

@router.post("/{appointment_id}/reconcile", response_model=schemas.AppointmentResponse)
def reconcile_appointment(
    appointment_id: str,
    req: Optional[schemas.AppointmentReconcileRequest] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reconciles an appointment state with external EHR or verification records.
    Permitted for Hospital Admins and Platform Admins.
    """
    appt = AppointmentService.get_appointment_by_id(db, appointment_id)
    if current_user.role not in (UserRole.PLATFORM_ADMIN.value, UserRole.HOSPITAL_ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Hospital Admins and Platform Admins can trigger manual state reconciliation."
        )
    if current_user.role == UserRole.HOSPITAL_ADMIN.value and appt.hospital_id != current_user.hospital_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant Isolation Violation: Cross-hospital reconciliation is prohibited.")

    external_status = req.external_status if req else None
    notes = req.resolution_notes if req else None
    updated_appt = AppointmentService.reconcile_appointment(
        db=db,
        appointment_id=appointment_id,
        external_status=external_status,
        resolution_notes=notes
    )
    return _format_appointment_response(updated_appt)
