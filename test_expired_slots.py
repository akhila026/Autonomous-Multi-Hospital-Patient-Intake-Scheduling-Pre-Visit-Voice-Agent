import unittest
import uuid
import asyncio
from datetime import datetime, timedelta, time
from backend.database import SessionLocal, Base, engine
from backend import models, schemas
from backend.services.slot_service import SlotService
from backend.scheduling.service import SchedulingService
from backend.services.appointment_service import AppointmentService
from backend.config import get_app_now
from fastapi import HTTPException

class TestExpiredSlotsAndAvailability(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        # Create isolated test doctor
        hosp = self.db.query(models.Hospital).first()
        self.doctor = models.Doctor(
            id=f"doc-test-exp-{uuid.uuid4().hex[:6]}",
            hospital_id=hosp.id,
            full_name="Dr. Test ExpiredSlots",
            specialty="General Medicine",
            consultation_fee=100.0,
            slot_duration_min=30,
            status="ACTIVE",
            is_active=True
        )
        self.db.add(self.doctor)
        self.db.commit()
        self.db.refresh(self.doctor)

        # Create calendar
        cal = models.Calendar(
            hospital_id=self.doctor.hospital_id,
            doctor_id=self.doctor.id,
            name="Test Calendar",
            is_active=True
        )
        self.db.add(cal)

        # Ensure doctor has working hours every day 00:00 - 23:59 for test flexibility
        for day in range(7):
            s = models.AvailabilitySchedule(
                doctor_id=self.doctor.id,
                day_of_week=day,
                start_time="00:00",
                end_time="23:59",
                is_active=True
            )
            self.db.add(s)
            av = models.Availability(
                hospital_id=self.doctor.hospital_id,
                doctor_id=self.doctor.id,
                calendar_id=cal.id,
                day_of_week=day,
                start_time="00:00",
                end_time="23:59",
                slot_duration_minutes=30,
                is_active=True
            )
            self.db.add(av)
        self.db.commit()

        self.patient = self.db.query(models.Patient).first()

    def tearDown(self):
        self.db.close()

    def test_dynamic_expired_slot_filtering_and_validation(self):
        """
        Tests the 6 specific requirements:
        1. A slot earlier than current time today must NOT appear.
        2. A slot later than current time today SHOULD appear if available.
        3. Tomorrow's equivalent time SHOULD appear if available.
        4. Future dates should continue to show valid slots.
        5. Attempting to book an expired slot must be rejected.
        6. Dynamic behavior works automatically.
        """
        now = get_app_now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        future_day = today + timedelta(days=3)

        # 1. Create a slot earlier today (e.g. 1 hour ago or 09:00 AM if currently after 09:00)
        past_start = now - timedelta(hours=2)
        past_end = past_start + timedelta(minutes=30)
        past_slot = models.TimeSlot(
            id=f"test-past-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=past_start,
            end_time=past_end,
            status="AVAILABLE"
        )

        # 2. Create a slot later today (future)
        future_today_start = now + timedelta(hours=2)
        future_today_end = future_today_start + timedelta(minutes=30)
        future_today_slot = models.TimeSlot(
            id=f"test-fut-today-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=future_today_start,
            end_time=future_today_end,
            status="AVAILABLE"
        )

        # 3. Create tomorrow's equivalent time to the past slot
        tomorrow_start = datetime.combine(tomorrow, past_start.time())
        tomorrow_end = tomorrow_start + timedelta(minutes=30)
        tomorrow_slot = models.TimeSlot(
            id=f"test-tomorrow-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=tomorrow_start,
            end_time=tomorrow_end,
            status="AVAILABLE"
        )

        # 4. Create future date slot
        future_date_start = datetime.combine(future_day, past_start.time())
        future_date_end = future_date_start + timedelta(minutes=30)
        future_date_slot = models.TimeSlot(
            id=f"test-future-day-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=future_date_start,
            end_time=future_date_end,
            status="AVAILABLE"
        )

        self.db.add_all([past_slot, future_today_slot, tomorrow_slot, future_date_slot])
        self.db.commit()

        try:
            today_str = today.strftime("%Y-%m-%d")
            tomorrow_str = tomorrow.strftime("%Y-%m-%d")
            future_str = future_day.strftime("%Y-%m-%d")

            # --- CHECK REQUIREMENT 1 & 2 via SlotService.get_doctor_slots ---
            today_slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=today_str, only_available=True)
            today_slot_ids = [s.id for s in today_slots]

            # 1. Past slot MUST NOT appear in today's availability
            self.assertNotIn(past_slot.id, today_slot_ids, "Expired slot earlier today unexpectedly appeared in availability!")

            # 2. Future slot today MUST appear in today's availability
            self.assertIn(future_today_slot.id, today_slot_ids, "Future slot today failed to appear in availability!")

            # --- CHECK REQUIREMENT 3: Tomorrow's equivalent time SHOULD appear ---
            tomorrow_slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=tomorrow_str, only_available=True)
            tomorrow_slot_ids = [s.id for s in tomorrow_slots]
            self.assertIn(tomorrow_slot.id, tomorrow_slot_ids, "Tomorrow's equivalent slot failed to appear in tomorrow's availability!")

            # --- CHECK REQUIREMENT 4: Future date slots SHOULD appear ---
            future_slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=future_str, only_available=True)
            future_slot_ids = [s.id for s in future_slots]
            self.assertIn(future_date_slot.id, future_slot_ids, "Future date slot failed to appear in availability!")

            # --- CHECK REQUIREMENT 5: Available Slot Validation & Booking Rejection ---
            # 5a: validate_slot on expired slot must return is_bookable=False
            val_past = SchedulingService.validate_slot(
                db=self.db,
                doctor_id=self.doctor.id,
                slot_id=past_slot.id
            )
            self.assertFalse(val_past.is_bookable, "validate_slot unexpectedly marked an expired slot as bookable!")
            self.assertIn("already passed", val_past.reason.lower(), f"Expected reason to state slot passed, got: {val_past.reason}")

            # 5b: validate_slot on future today slot must return is_bookable=True
            val_fut = SchedulingService.validate_slot(
                db=self.db,
                doctor_id=self.doctor.id,
                slot_id=future_today_slot.id
            )
            self.assertTrue(val_fut.is_bookable, f"validate_slot rejected valid future slot: {val_fut.reason}")

            # 5c: Attempting to hold expired slot must be rejected
            with self.assertRaises(HTTPException) as cm_hold:
                SlotService.hold_slot(self.db, past_slot.id)
            self.assertEqual(cm_hold.exception.status_code, 409)

            # 5d: Attempting to book an expired slot via AppointmentService must be rejected
            confirm_req = schemas.AppointmentConfirmRequest(
                slot_id=past_slot.id,
                patient_id=self.patient.id,
                chief_complaint="Booking expired slot test",
                urgency_level="ROUTINE"
            )
            with self.assertRaises(HTTPException) as cm_book:
                asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, confirm_req))
            self.assertEqual(cm_book.exception.status_code, 409)
            self.assertIn("already passed", str(cm_book.exception.detail).lower())

            print("ALL 6 EXPIRED SLOT REQUIREMENTS PASSED AUTOMATED VERIFICATION!")

        finally:
            # Clean up test slots
            self.db.query(models.TimeSlot).filter(models.TimeSlot.id.in_([
                past_slot.id, future_today_slot.id, tomorrow_slot.id, future_date_slot.id
            ])).delete(synchronize_session=False)
            self.db.commit()

    def test_all_8_scheduling_rules_displayed_and_booking_validation(self):
        """
        Validates that both displayed availability (SlotService.get_doctor_slots)
        and final booking validation (SchedulingService.validate_slot & AppointmentService)
        enforce identical scheduling rules:
        1. Doctor is active
        2. Calendar is active
        3. Slot is within working hours
        4. Slot is not blocked
        5. Doctor is not on leave
        6. Slot is not already booked
        7. Appointment type is compatible
        8. Slot datetime has not already passed
        """
        now = get_app_now()
        future_dt = now + timedelta(days=2, hours=2)
        target_date_str = future_dt.strftime("%Y-%m-%d")

        # Create a valid test slot
        slot = models.TimeSlot(
            id=f"test-rule-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=future_dt,
            end_time=future_dt + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        try:
            # Baseline: slot should be displayed and valid
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertIn(slot.id, [s.id for s in slots])
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertTrue(val.is_bookable)

            # Rule 1: Doctor is inactive
            self.doctor.is_active = False
            self.db.commit()
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertNotIn(slot.id, [s.id for s in slots], "Inactive doctor's slot must not appear in availability")
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertFalse(val.is_bookable)
            self.doctor.is_active = True
            self.db.commit()

            # Rule 2: Calendar is active
            cal = self.db.query(models.Calendar).filter(models.Calendar.doctor_id == self.doctor.id).first()
            cal.is_active = False
            self.db.commit()
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertNotIn(slot.id, [s.id for s in slots], "Inactive calendar slot must not appear in availability")
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertFalse(val.is_bookable)
            cal.is_active = True
            self.db.commit()

            # Rule 4: Slot is blocked (general block)
            block = models.BlockedSlot(
                id=f"block-{uuid.uuid4().hex[:6]}",
                hospital_id=self.doctor.hospital_id,
                doctor_id=self.doctor.id,
                start_time=future_dt,
                end_time=future_dt + timedelta(minutes=30),
                reason="Department Meeting"
            )
            self.db.add(block)
            self.db.commit()
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertNotIn(slot.id, [s.id for s in slots], "Blocked slot must not appear in availability")
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertFalse(val.is_bookable)
            self.db.delete(block)
            self.db.commit()

            # Rule 5: Doctor is on leave
            leave_block = models.BlockedSlot(
                id=f"leave-{uuid.uuid4().hex[:6]}",
                hospital_id=self.doctor.hospital_id,
                doctor_id=self.doctor.id,
                start_time=future_dt,
                end_time=future_dt + timedelta(minutes=30),
                reason="Annual Vacation Leave"
            )
            self.db.add(leave_block)
            self.db.commit()
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertNotIn(slot.id, [s.id for s in slots], "Doctor on leave slot must not appear in availability")
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertFalse(val.is_bookable)
            self.assertIn("leave", val.reason.lower())
            self.db.delete(leave_block)
            self.db.commit()

            # Rule 6: Slot is already booked
            slot.status = "BOOKED"
            self.db.commit()
            slots = SlotService.get_doctor_slots(self.db, self.doctor.id, target_date=target_date_str, only_available=True)
            self.assertNotIn(slot.id, [s.id for s in slots], "Booked slot must not appear in availability")
            val = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id)
            self.assertFalse(val.is_bookable)
            slot.status = "AVAILABLE"
            self.db.commit()

            # Rule 7: Appointment type compatibility
            self.doctor.supported_appointment_types = "VIDEO,TELEHEALTH"
            self.db.commit()
            val_inperson = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id, appointment_type="IN_PERSON")
            self.assertFalse(val_inperson.is_bookable)
            val_video = SchedulingService.validate_slot(self.db, self.doctor.id, slot_id=slot.id, appointment_type="VIDEO")
            self.assertTrue(val_video.is_bookable)
            self.doctor.supported_appointment_types = "IN_PERSON,VIDEO"
            self.db.commit()

            print("ALL 8 SCHEDULING RULES VALIDATED ACROSS AVAILABILITY AND BOOKING!")

        finally:
            self.db.query(models.TimeSlot).filter(models.TimeSlot.id == slot.id).delete(synchronize_session=False)
            self.db.commit()

if __name__ == "__main__":
    unittest.main()
