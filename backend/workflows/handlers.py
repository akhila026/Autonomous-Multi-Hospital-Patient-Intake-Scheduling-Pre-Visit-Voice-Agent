import asyncio
import logging
from typing import Optional
from backend import models
from backend.database import SessionLocal
from backend.workflows.events import event_dispatcher, DomainEvent, EventType
from backend.workflows.engine import WorkflowEngine
from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

_handlers_registered = False


async def audit_event_observer(event: DomainEvent):
    """
    Global Observer (PRD Section 19 & 21).
    Logs an immutable AuditEvent to the database for every dispatched domain event.
    """
    try:
        db = SessionLocal()
        try:
            audit_entry = models.AuditEvent(
                correlation_id=event.correlation_id,
                hospital_id=event.tenant_id,
                actor_id=event.actor_id or "EVENT_BUS",
                actor_role=event.actor_role or "SYSTEM",
                action=event.event_type.value.upper(),
                resource_type="DOMAIN_EVENT",
                resource_id=event.entity_id,
                status="DISPATCHED",
                details={
                    "event_id": event.id,
                    "event_type": event.event_type.value,
                    "payload": event.payload,
                    "timestamp": event.timestamp.isoformat()
                }
            )
            db.add(audit_entry)
            db.commit()
        finally:
            db.close()
    except Exception as ex:
        logger.error(f"Audit observer failed for event {event.event_type}: {ex}")


async def handle_appointment_booked(event: DomainEvent):
    """Triggered on appointment_booked: runs POST_BOOKING_WORKFLOW."""
    appointment_id = event.entity_id
    hospital_id = event.tenant_id or event.payload.get("hospital_id")
    if not hospital_id:
        db = SessionLocal()
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
            if appt:
                hospital_id = appt.hospital_id
        finally:
            db.close()

    if hospital_id:
        db = SessionLocal()
        try:
            from backend.services.observability_service import ObservabilityService, BookingTraceStage
            ObservabilityService.record_stage(
                correlation_id=event.correlation_id,
                stage=BookingTraceStage.WORKFLOW,
                status="COMPLETED",
                details={
                    "workflow_type": "POST_BOOKING_WORKFLOW",
                    "appointment_id": appointment_id,
                    "hospital_id": hospital_id
                },
                actor_id="WORKFLOW_ENGINE",
                actor_role="SYSTEM",
                tenant_id=hospital_id,
                db=db
            )

            await WorkflowEngine.execute(
                db=db,
                workflow_type="POST_BOOKING_WORKFLOW",
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                payload=event.payload,
                idempotency_key=f"WF-POSTBOOK-{appointment_id}",
                correlation_id=event.correlation_id
            )
        except Exception as wf_err:
            logger.warning(f"Error running post booking workflow for {appointment_id}: {wf_err}")
        finally:
            db.close()


async def handle_appointment_cancelled(event: DomainEvent):
    """Triggered on appointment_cancelled: runs APPOINTMENT_CANCELLATION_WORKFLOW."""
    appointment_id = event.entity_id
    hospital_id = event.tenant_id or event.payload.get("hospital_id")
    if not hospital_id:
        db = SessionLocal()
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
            if appt:
                hospital_id = appt.hospital_id
        finally:
            db.close()

    if hospital_id:
        db = SessionLocal()
        try:
            await WorkflowEngine.execute(
                db=db,
                workflow_type="APPOINTMENT_CANCELLATION_WORKFLOW",
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                payload=event.payload,
                idempotency_key=f"WF-CANCEL-{appointment_id}",
                correlation_id=event.correlation_id
            )
        except Exception as wf_err:
            logger.warning(f"Error running cancellation workflow for {appointment_id}: {wf_err}")
        finally:
            db.close()


async def handle_appointment_rescheduled(event: DomainEvent):
    """Triggered on appointment_rescheduled: sends notifications and updates reminders."""
    appointment_id = event.entity_id
    db = SessionLocal()
    try:
        appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if appt:
            doctor = db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first()
            patient = db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first()
            doc_name = doctor.full_name if doctor else "Doctor"
            patient_name = patient.name if patient else "Patient"
            patient_phone = patient.phone if patient else "+1-555-0100"
            slot_str = str(event.payload.get("slot_time") or appt.start_time)

            # Patient Notification
            NotificationService.create_or_schedule_notification(
                db=db,
                recipient_type="PATIENT",
                recipient_contact=patient_phone,
                template="APPOINTMENT_RESCHEDULED",
                context={"doctor_name": doc_name, "slot_time": slot_str},
                hospital_id=appt.hospital_id,
                appointment_id=appt.id,
                idempotency_key=f"NOTIF-RESCHED-P-{appt.id}-{slot_str}"
            )

            # Doctor Notification
            NotificationService.create_or_schedule_notification(
                db=db,
                recipient_type="DOCTOR",
                recipient_contact=doctor.email if doctor and doctor.email else "doctor@hospital.org",
                template="DOCTOR_RESCHEDULED",
                context={"patient_name": patient_name, "slot_time": slot_str},
                hospital_id=appt.hospital_id,
                appointment_id=appt.id,
                idempotency_key=f"NOTIF-RESCHED-D-{appt.id}-{slot_str}"
            )
    finally:
        db.close()


async def handle_hospital_approved(event: DomainEvent):
    """Triggered on hospital_approved: notifies hospital administration."""
    hospital_id = event.entity_id
    db = SessionLocal()
    try:
        hosp = db.query(models.Hospital).filter(models.Hospital.id == hospital_id).first()
        hosp_name = hosp.name if hosp else event.payload.get("hospital_name", "Hospital")
        NotificationService.create_or_schedule_notification(
            db=db,
            recipient_type="HOSPITAL",
            recipient_contact="admin@hospital.org",
            template="APPLICATION_STATUS",
            context={"hospital_name": hosp_name, "status": "APPROVED"},
            hospital_id=hospital_id,
            idempotency_key=f"NOTIF-HOSP-APPROVED-{hospital_id}"
        )
    finally:
        db.close()


async def handle_questionnaire_completed(event: DomainEvent):
    """Triggered on questionnaire_completed: alerts doctor that intake is ready."""
    appointment_id = event.payload.get("appointment_id") or event.entity_id
    db = SessionLocal()
    try:
        appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if appt:
            doctor = db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first()
            patient = db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first()
            patient_name = patient.name if patient else "Patient"
            NotificationService.create_or_schedule_notification(
                db=db,
                recipient_type="DOCTOR",
                recipient_contact=doctor.email if doctor and doctor.email else "doctor@hospital.org",
                template="QUESTIONNAIRE_COMPLETION",
                context={
                    "patient_name": patient_name,
                    "slot_time": str(appt.start_time)
                },
                hospital_id=appt.hospital_id,
                appointment_id=appt.id,
                idempotency_key=f"NOTIF-QUEST-COMP-{appt.id}"
            )
    finally:
        db.close()


async def handle_ehr_operation_failed(event: DomainEvent):
    """Triggered on ehr_operation_failed: alerts hospital and optionally runs recovery workflow."""
    appointment_id = event.payload.get("appointment_id") or event.entity_id
    hospital_id = event.tenant_id
    error_msg = event.payload.get("error") or "Unknown EHR timeout or connector error"

    db = SessionLocal()
    try:
        NotificationService.create_or_schedule_notification(
            db=db,
            recipient_type="HOSPITAL",
            recipient_contact="admin@hospital.org",
            template="INTEGRATION_FAILURES",
            context={
                "appointment_id": appointment_id,
                "error_message": error_msg
            },
            hospital_id=hospital_id,
            appointment_id=appointment_id,
            idempotency_key=f"NOTIF-EHR-FAIL-{appointment_id}"
        )

        # Run EHR Recovery Workflow only if explicitly requested
        if event.payload.get("auto_recover") is True and hospital_id:
            await WorkflowEngine.execute(
                db=db,
                workflow_type="EHR_RECOVERY_WORKFLOW",
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                payload=event.payload,
                idempotency_key=f"WF-EHR-RECOVER-{appointment_id}",
                correlation_id=event.correlation_id
            )
    finally:
        db.close()


async def handle_human_escalation(event: DomainEvent):
    """Triggered on human_escalation: delivers operational alerts to hospital coordinator."""
    hospital_id = event.tenant_id
    reason = event.payload.get("reason", "Action required by care coordinator")
    appt_id = event.payload.get("appointment_id")

    db = SessionLocal()
    try:
        NotificationService.create_or_schedule_notification(
            db=db,
            recipient_type="HOSPITAL",
            recipient_contact="coordinator@hospital.org",
            template="OPERATIONAL_ALERTS",
            context={
                "appointment_id": appt_id or event.entity_id,
                "reason": reason
            },
            hospital_id=hospital_id,
            appointment_id=appt_id,
            idempotency_key=f"NOTIF-ESCALATE-{event.id}"
        )
    finally:
        db.close()


def register_event_handlers():
    """Initializes and registers all core event handlers and observers."""
    global _handlers_registered
    if _handlers_registered:
        return

    # 1. Global Observers
    event_dispatcher.subscribe_all(audit_event_observer)

    # 2. Type-Specific Handlers
    event_dispatcher.subscribe(EventType.APPOINTMENT_BOOKED, handle_appointment_booked)
    event_dispatcher.subscribe(EventType.APPOINTMENT_CANCELLED, handle_appointment_cancelled)
    event_dispatcher.subscribe(EventType.APPOINTMENT_RESCHEDULED, handle_appointment_rescheduled)
    event_dispatcher.subscribe(EventType.HOSPITAL_APPROVED, handle_hospital_approved)
    event_dispatcher.subscribe(EventType.QUESTIONNAIRE_COMPLETED, handle_questionnaire_completed)
    event_dispatcher.subscribe(EventType.EHR_OPERATION_FAILED, handle_ehr_operation_failed)
    event_dispatcher.subscribe(EventType.RECONCILIATION_REQUIRED, handle_ehr_operation_failed)
    event_dispatcher.subscribe(EventType.HUMAN_ESCALATION, handle_human_escalation)

    _handlers_registered = True
    logger.info("Domain event handlers successfully registered.")
