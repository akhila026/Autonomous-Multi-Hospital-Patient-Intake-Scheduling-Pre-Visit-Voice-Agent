from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from backend import models

class ReminderService:
    @staticmethod
    def get_appointment_reminders(db: Session, appointment_id: str) -> List[models.ReminderLog]:
        return db.query(models.ReminderLog).filter(
            models.ReminderLog.appointment_id == appointment_id
        ).order_by(models.ReminderLog.created_at.asc()).all()

    @staticmethod
    def get_all_reminders(db: Session, limit: int = 50) -> List[models.ReminderLog]:
        return db.query(models.ReminderLog).order_by(models.ReminderLog.created_at.desc()).limit(limit).all()

    @staticmethod
    def dispatch_pending_reminders(db: Session) -> int:
        now = datetime.utcnow()
        pending = db.query(models.ReminderLog).filter(
            models.ReminderLog.status == "SCHEDULED",
            models.ReminderLog.scheduled_for <= now
        ).all()

        count = 0
        for rem in pending:
            rem.status = "SENT"
            rem.sent_at = now
            count += 1

        if pending:
            db.commit()
        return count
