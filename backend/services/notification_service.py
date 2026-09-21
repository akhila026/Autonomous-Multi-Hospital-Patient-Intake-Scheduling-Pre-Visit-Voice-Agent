import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from backend import models

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Configurable Multi-Party Notification Service (PRD Section 17 & 22).
    Generates, schedules, and dispatches multi-channel notifications for:
    - Patient (Confirmations, 24h/1h Reminders, Cancellations, Rescheduling, Questionnaire Reminders, Updates)
    - Doctor (New Bookings, Cancellations, Rescheduling, Questionnaire Completion, Schedule Reminders)
    - Hospital (Application Status, Appointment Volume Activity, Integration Failures, Operational Alerts)
    """

    TEMPLATES = {
        # --- PATIENT TEMPLATES ---
        "APPOINTMENT_CONFIRMATION": (
            "Appointment Confirmed with Dr. {doctor_name} on {slot_time} at {hospital_name}. "
            "EHR Ref: {ehr_appointment_id}. Please arrive 10 minutes early."
        ),
        "PRE_VISIT_REMINDER_24H": (
            "Reminder: Your consultation with Dr. {doctor_name} is tomorrow at {slot_time}. "
            "Please ensure your pre-visit health intake questionnaire is completed."
        ),
        "PRE_VISIT_REMINDER_1H": (
            "Reminder: Your consultation with Dr. {doctor_name} begins in 1 hour ({slot_time}). "
            "Please be ready 5 minutes prior to start."
        ),
        "APPOINTMENT_CANCELLATION": (
            "Notice: Your appointment with Dr. {doctor_name} for {slot_time} has been cancelled. "
            "Reason: {reason}."
        ),
        "APPOINTMENT_RESCHEDULED": (
            "Notice: Your appointment has been rescheduled. New consultation time: {slot_time} with Dr. {doctor_name}."
        ),
        "QUESTIONNAIRE_REMINDER": (
            "Action Required: Please complete your pre-visit health questionnaire for your upcoming visit with Dr. {doctor_name}."
        ),
        "IMPORTANT_UPDATES": (
            "Important Healthcare Update from {hospital_name}: {message_content}"
        ),

        # --- DOCTOR TEMPLATES ---
        "NEW_APPOINTMENT": (
            "New Consultation Scheduled: Patient {patient_name} on {slot_time}. "
            "Chief Complaint: {chief_complaint}."
        ),
        "DOCTOR_CANCELLATION": (
            "Consultation Cancelled: Patient {patient_name} for {slot_time} has been cancelled. Reason: {reason}."
        ),
        "DOCTOR_RESCHEDULED": (
            "Consultation Rescheduled: Patient {patient_name} moved to {slot_time}."
        ),
        "QUESTIONNAIRE_COMPLETION": (
            "Intake Ready: Patient {patient_name} has completed their pre-visit intake questionnaire for {slot_time}."
        ),
        "UPCOMING_APPOINTMENT": (
            "Schedule Reminder: Upcoming consultation with {patient_name} at {slot_time}."
        ),

        # --- HOSPITAL TEMPLATES ---
        "APPLICATION_STATUS": (
            "Hospital Onboarding Update: {hospital_name} application status is now {status}."
        ),
        "APPOINTMENT_ACTIVITY": (
            "Operational Activity Alert: {hospital_name} - {details}."
        ),
        "INTEGRATION_FAILURES": (
            "Integration Alert: EHR operation failed for appointment {appointment_id}. Error: {error_message}."
        ),
        "OPERATIONAL_ALERTS": (
            "Operational Alert: Human coordinator action required for appointment {appointment_id}. Reason: {reason}."
        )
    }

    @staticmethod
    def render_content(template_key: str, context: Dict[str, Any]) -> str:
        """Interpolates dynamic variables into notification templates safely."""
        template_str = NotificationService.TEMPLATES.get(template_key, "{message_content}")
        try:
            # Format using available keys, providing defaults for missing ones
            safe_context = {k: str(v) if v is not None else "" for k, v in context.items()}
            # Provide fallbacks
            for k in ["doctor_name", "patient_name", "slot_time", "hospital_name", "ehr_appointment_id", "reason", "message_content", "details", "status", "appointment_id", "error_message", "chief_complaint"]:
                if k not in safe_context:
                    safe_context[k] = "N/A"
            return template_str.format(**safe_context)
        except Exception as e:
            return context.get("message_content", f"Healthcare Notification: {template_key}")

    @staticmethod
    def create_or_schedule_notification(
        db: Session,
        recipient_type: str,
        recipient_contact: str,
        template: str,
        context: Dict[str, Any],
        channel: str = "SMS",
        scheduled_for: Optional[datetime] = None,
        hospital_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> models.Notification:
        """
        Creates and immediately delivers or schedules a notification with strict idempotency.
        """
        if not idempotency_key:
            idempotency_key = f"NOTIF-{recipient_type}-{template}-{appointment_id or uuid.uuid4().hex[:8]}"

        # Check existing idempotency to prevent duplicate notification sends
        existing = db.query(models.Notification).filter(
            models.Notification.idempotency_key == idempotency_key
        ).first()
        if existing:
            return existing

        now = datetime.utcnow()
        sched_time = scheduled_for or now
        is_immediate = sched_time <= now

        content = NotificationService.render_content(template, context)

        notif = models.Notification(
            hospital_id=hospital_id,
            appointment_id=appointment_id,
            recipient_type=recipient_type.upper(),
            recipient_contact=recipient_contact,
            channel=channel.upper(),
            template=template,
            message_content=content,
            idempotency_key=idempotency_key,
            payload=context,
            scheduled_for=sched_time,
            sent_at=now if is_immediate else None,
            status="SENT" if is_immediate else "SCHEDULED"
        )
        db.add(notif)

        # Also maintain ReminderLog for backward compatibility if appointment reminder
        if appointment_id and recipient_type.upper() == "PATIENT" and "REMINDER" in template:
            rem_type = "PRE_VISIT_24H" if "24H" in template else ("PRE_VISIT_1H" if "1H" in template else "CONFIRMATION")
            rem = models.ReminderLog(
                appointment_id=appointment_id,
                reminder_type=rem_type,
                scheduled_for=sched_time,
                channel=channel.upper(),
                message_content=content,
                sent_at=now if is_immediate else None,
                status="SENT" if is_immediate else "SCHEDULED"
            )
            db.add(rem)

        db.commit()
        db.refresh(notif)
        return notif

    @staticmethod
    def dispatch_due_notifications(db: Session) -> int:
        """
        Scans for scheduled notifications that are now due and marks them as sent.
        """
        now = datetime.utcnow()
        due_notifs = db.query(models.Notification).filter(
            models.Notification.status == "SCHEDULED",
            models.Notification.scheduled_for <= now
        ).all()

        count = 0
        for n in due_notifs:
            n.status = "SENT"
            n.sent_at = now
            count += 1

        # Also update reminder logs
        due_reminders = db.query(models.ReminderLog).filter(
            models.ReminderLog.status == "SCHEDULED",
            models.ReminderLog.scheduled_for <= now
        ).all()
        for r in due_reminders:
            r.status = "SENT"
            r.sent_at = now

        if due_notifs or due_reminders:
            db.commit()
        return count

    @staticmethod
    def get_notifications(
        db: Session,
        recipient_type: Optional[str] = None,
        hospital_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[models.Notification]:
        query = db.query(models.Notification)
        if recipient_type:
            query = query.filter(models.Notification.recipient_type == recipient_type.upper())
        if hospital_id:
            query = query.filter(models.Notification.hospital_id == hospital_id)
        if appointment_id:
            query = query.filter(models.Notification.appointment_id == appointment_id)
        if status:
            query = query.filter(models.Notification.status == status.upper())
        return query.order_by(models.Notification.created_at.desc()).limit(limit).all()
