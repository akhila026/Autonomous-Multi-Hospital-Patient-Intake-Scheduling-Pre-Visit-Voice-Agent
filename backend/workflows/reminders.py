from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend import models
from backend.services.notification_service import NotificationService

class NotificationPayload(BaseModel):
    recipient_type: str  # PATIENT, DOCTOR, HOSPITAL
    recipient_contact: str
    channel: str = "SMS"  # SMS, EMAIL, WHATSAPP, VOICE
    template: str
    scheduled_for: datetime
    sent: bool = False
    details: Optional[Dict[str, Any]] = None

class ReminderScheduler:
    """
    Schedules and dispatches configurable notifications (PRD Section 17 & 22):
    - Patient: Confirmation, 24h reminder, 1h reminder, questionnaire reminder, updates
    - Doctor: New appointment, cancellation, rescheduling, questionnaire completion, upcoming reminder
    - Hospital: Application status, appointment activity, integration failures, operational alerts
    """
    @staticmethod
    def schedule_patient_reminder(
        appointment_id: str,
        slot_time: datetime,
        channel: str = "SMS",
        doctor_name: str = "Physician",
        patient_phone: str = "+1-555-0100",
        db: Optional[Session] = None,
        hospital_id: Optional[str] = None
    ) -> NotificationPayload:
        rem_24h = slot_time - timedelta(hours=24)
        if db:
            notif = NotificationService.create_or_schedule_notification(
                db=db,
                recipient_type="PATIENT",
                recipient_contact=patient_phone,
                template="PRE_VISIT_REMINDER_24H",
                context={"doctor_name": doctor_name, "slot_time": str(slot_time)},
                channel=channel,
                scheduled_for=rem_24h,
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                idempotency_key=f"SCHED-P-24H-{appointment_id}"
            )
            return NotificationPayload(
                recipient_type="PATIENT",
                recipient_contact=patient_phone,
                channel=channel,
                template="PRE_VISIT_REMINDER_24H",
                scheduled_for=rem_24h,
                sent=notif.status == "SENT"
            )
        return NotificationPayload(
            recipient_type="PATIENT",
            recipient_contact=patient_phone,
            channel=channel,
            template="UPCOMING_CONSULTATION_24H",
            scheduled_for=rem_24h
        )

    @staticmethod
    def schedule_questionnaire_reminder(
        appointment_id: str,
        patient_phone: str,
        doctor_name: str,
        db: Optional[Session] = None,
        hospital_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None
    ) -> NotificationPayload:
        sched = scheduled_for or datetime.utcnow()
        if db:
            notif = NotificationService.create_or_schedule_notification(
                db=db,
                recipient_type="PATIENT",
                recipient_contact=patient_phone,
                template="QUESTIONNAIRE_REMINDER",
                context={"doctor_name": doctor_name},
                channel="SMS",
                scheduled_for=sched,
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                idempotency_key=f"SCHED-P-QUEST-{appointment_id}"
            )
            return NotificationPayload(
                recipient_type="PATIENT",
                recipient_contact=patient_phone,
                channel="SMS",
                template="QUESTIONNAIRE_REMINDER",
                scheduled_for=sched,
                sent=notif.status == "SENT"
            )
        return NotificationPayload(
            recipient_type="PATIENT",
            recipient_contact=patient_phone,
            channel="SMS",
            template="QUESTIONNAIRE_REMINDER",
            scheduled_for=sched
        )

    @staticmethod
    def dispatch_due(db: Session) -> int:
        """Dispatches all scheduled notifications that are now due."""
        return NotificationService.dispatch_due_notifications(db)
