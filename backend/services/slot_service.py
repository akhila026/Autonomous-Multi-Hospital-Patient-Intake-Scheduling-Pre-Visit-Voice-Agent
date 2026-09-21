from datetime import datetime, timedelta, time
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from fastapi import HTTPException
from backend import models, schemas
from backend.config import settings

class SlotService:
    @staticmethod
    def cleanup_expired_holds(db: Session):
        """Releases any slots whose hold duration has elapsed."""
        from backend.scheduling.locks import slot_lock_manager
        now = datetime.utcnow()
        expired_slots = db.query(models.TimeSlot).filter(
            models.TimeSlot.status == "HELD",
            models.TimeSlot.hold_expires_at < now
        ).all()

        for slot in expired_slots:
            slot.status = "AVAILABLE"
            slot.hold_expires_at = None
            slot_lock_manager.release_hold_sync(slot.id)

        if expired_slots:
            db.commit()

    @staticmethod
    def generate_slots_for_doctor(db: Session, doctor_id: str, days_ahead: int = 7) -> List[models.TimeSlot]:
        doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")
        if doctor.hospital and doctor.hospital.status != "APPROVED":
            raise HTTPException(status_code=400, detail=f"Cannot generate slots for doctor in unapproved hospital (status: '{doctor.hospital.status}').")
        if doctor.status != "ACTIVE" or not doctor.is_active:
            raise HTTPException(status_code=400, detail=f"Cannot generate slots for doctor with status '{doctor.status}'. Doctor must be ACTIVE.")

        schedules = db.query(models.AvailabilitySchedule).filter(
            models.AvailabilitySchedule.doctor_id == doctor_id,
            models.AvailabilitySchedule.is_active == True
        ).all()

        if not schedules:
            default_schedules = []
            for d in range(5):  # Mon-Fri
                sch = models.AvailabilitySchedule(
                    doctor_id=doctor_id,
                    day_of_week=d,
                    start_time="09:00",
                    end_time="17:00",
                    is_active=True
                )
                db.add(sch)
                default_schedules.append(sch)
            db.commit()
            schedules = default_schedules

        # Map schedule by day of week (0=Monday ... 6=Sunday)
        schedule_map = {s.day_of_week: s for s in schedules}

        now = datetime.utcnow()
        today = now.date()
        new_slots = []

        slot_duration = timedelta(minutes=doctor.slot_duration_min or 30)

        for day_offset in range(days_ahead):
            target_date = today + timedelta(days=day_offset)
            weekday = target_date.weekday()

            if weekday in schedule_map:
                sched = schedule_map[weekday]
                start_h, start_m = map(int, sched.start_time.split(":"))
                end_h, end_m = map(int, sched.end_time.split(":"))

                slot_start = datetime.combine(target_date, time(start_h, start_m))
                day_end = datetime.combine(target_date, time(end_h, end_m))

                while slot_start + slot_duration <= day_end:
                    slot_end = slot_start + slot_duration

                    # Only generate slots in the future
                    if slot_start > now:
                        # Check if slot already exists
                        exists = db.query(models.TimeSlot).filter(
                            models.TimeSlot.doctor_id == doctor_id,
                            models.TimeSlot.start_time == slot_start
                        ).first()

                        if not exists:
                            slot = models.TimeSlot(
                                doctor_id=doctor_id,
                                start_time=slot_start,
                                end_time=slot_end,
                                status="AVAILABLE"
                            )
                            db.add(slot)
                            new_slots.append(slot)

                    slot_start = slot_end

        db.commit()
        return new_slots

    @staticmethod
    def get_doctor_slots(
        db: Session,
        doctor_id: str,
        target_date: Optional[str] = None,
        only_available: bool = True
    ) -> List[models.TimeSlot]:
        SlotService.cleanup_expired_holds(db)

        from backend.config import get_app_now
        now = get_app_now()

        q = db.query(models.TimeSlot).filter(models.TimeSlot.doctor_id == doctor_id)

        if only_available:
            q = q.filter(models.TimeSlot.status == "AVAILABLE")
            # Dynamic filtering: compare complete slot datetime with current datetime
            # Do not show slots whose start time has already passed
            q = q.filter(models.TimeSlot.start_time > now)

        if target_date:
            try:
                d = datetime.strptime(target_date, "%Y-%m-%d").date()
                day_start = datetime.combine(d, time.min)
                day_end = datetime.combine(d, time.max)
                q = q.filter(models.TimeSlot.start_time >= day_start, models.TimeSlot.start_time <= day_end)
            except ValueError:
                pass
        slots = q.order_by(models.TimeSlot.start_time.asc()).all()
        if not only_available:
            return slots

        # Ensure displayed availability satisfies all 8 scheduling rules:
        # 0. Slot datetime has not passed
        # 1. Doctor is active
        # 2. Calendar is active
        # 3. Slot within working hours
        # 4. Slot is not blocked
        # 5. Doctor is not on leave
        # 6. Slot not already booked / no overlapping appointment
        # 7. Compatible appointment type
        from backend.scheduling.service import SchedulingService
        valid_slots = []
        for s in slots:
            val = SchedulingService.validate_slot(
                db=db,
                doctor_id=doctor_id,
                slot_id=s.id,
                start_time=s.start_time,
                end_time=s.end_time
            )
            if val.is_bookable:
                valid_slots.append(s)
        return valid_slots

    @staticmethod
    def hold_slot(db: Session, slot_id: str, hold_minutes: int = 5) -> models.TimeSlot:
        SlotService.cleanup_expired_holds(db)

        slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).with_for_update().first()
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found")

        from backend.config import get_app_now
        now = get_app_now()
        if slot.start_time <= now:
            raise HTTPException(status_code=409, detail="This time slot has already passed and cannot be booked.")

        if slot.status == "BOOKED":
            raise HTTPException(status_code=409, detail="This time slot has already been booked.")
        if slot.status == "HELD" and slot.hold_expires_at and slot.hold_expires_at > now:
            raise HTTPException(status_code=409, detail="This time slot is temporarily held by another patient. Please choose another or try again in a few minutes.")

        slot.status = "HELD"
        slot.hold_expires_at = datetime.utcnow() + timedelta(minutes=hold_minutes)
        db.commit()
        db.refresh(slot)
        return slot

    @staticmethod
    def release_slot_hold(db: Session, slot_id: str):
        slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
        if slot and slot.status == "HELD":
            slot.status = "AVAILABLE"
            slot.hold_expires_at = None
            db.commit()
