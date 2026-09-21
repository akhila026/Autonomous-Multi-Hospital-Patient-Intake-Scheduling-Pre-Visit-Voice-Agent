from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import schemas
from backend.services.reminder_service import ReminderService

router = APIRouter(prefix="/reminders", tags=["Reminders"])

@router.get("/appointment/{appointment_id}", response_model=List[schemas.ReminderLogResponse])
def get_appointment_reminders(appointment_id: str, db: Session = Depends(get_db)):
    reminders = ReminderService.get_appointment_reminders(db, appointment_id)
    return [
        schemas.ReminderLogResponse(
            id=r.id,
            appointment_id=r.appointment_id,
            reminder_type=r.reminder_type,
            scheduled_for=r.scheduled_for,
            status=r.status,
            channel=r.channel,
            message_content=r.message_content,
            sent_at=r.sent_at
        ) for r in reminders
    ]

@router.get("", response_model=List[schemas.ReminderLogResponse])
def get_all_reminders(db: Session = Depends(get_db)):
    reminders = ReminderService.get_all_reminders(db)
    return [
        schemas.ReminderLogResponse(
            id=r.id,
            appointment_id=r.appointment_id,
            reminder_type=r.reminder_type,
            scheduled_for=r.scheduled_for,
            status=r.status,
            channel=r.channel,
            message_content=r.message_content,
            sent_at=r.sent_at
        ) for r in reminders
    ]

@router.post("/dispatch-pending")
def dispatch_pending(db: Session = Depends(get_db)):
    count = ReminderService.dispatch_pending_reminders(db)
    return {"message": f"Dispatched {count} pending reminders"}
