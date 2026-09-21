import time
import uuid
import asyncio
from datetime import datetime, timedelta
from backend.database import SessionLocal
from backend import models, schemas
from backend.services.appointment_service import AppointmentService
from backend.scheduling.service import SchedulingService
from backend.services.slot_service import SlotService
from backend.ehr.connector import MockEHRConnector

def run_test():
    db = SessionLocal()
    connector = MockEHRConnector()
    connector.set_chaos_config(mode="NORMAL", is_active=False)

    doctor = db.query(models.Doctor).filter(models.Doctor.is_active == True).first()
    patient = db.query(models.Patient).first()
    print(f"Testing with Doctor: {doctor.full_name} ({doctor.id}), Patient: {patient.full_name} ({patient.id})")

    # Generate or find slots
    now = datetime.utcnow()
    def find_free_slot(days_ahead_start=2):
        for day_offset in range(days_ahead_start, days_ahead_start + 15):
            target = now + timedelta(days=day_offset)
            if target.weekday() >= 5:
                continue
            for hour in [14, 15, 16]:
                cand_start = datetime(target.year, target.month, target.day, hour, 0, 0)
                cand_end = cand_start + timedelta(minutes=30)
                val = SchedulingService.validate_slot(
                    db=db,
                    doctor_id=doctor.id,
                    start_time=cand_start,
                    end_time=cand_end
                )
                if val.is_bookable:
                    slot = models.TimeSlot(
                        doctor_id=doctor.id,
                        start_time=cand_start,
                        end_time=cand_end,
                        status="AVAILABLE"
                    )
                    db.add(slot)
                    db.commit()
                    db.refresh(slot)
                    return slot
        raise RuntimeError("No free slot found")

    slot1 = find_free_slot(2)
    slot2 = find_free_slot(4)

    print(f"Slot 1: {slot1.id}, Slot 2: {slot2.id}")

    # Test 1: First booking
    t0 = time.time()
    req1 = schemas.AppointmentConfirmRequest(
        slot_id=slot1.id,
        patient_id=patient.id,
        chief_complaint="Booking 1 - Routine Checkup",
        urgency_level="ROUTINE",
        idempotency_key=f"TEST-BOOK-1-{uuid.uuid4().hex[:6]}"
    )
    appt1 = asyncio.run(AppointmentService.confirm_and_sync_appointment(db, req1))
    t1 = time.time()
    print(f"Booking 1 completed in {t1 - t0:.2f}s, status: {appt1.status}, ehr_id: {appt1.ehr_appointment_id}")

    # Test 2: Second booking (different slot)
    t2 = time.time()
    req2 = schemas.AppointmentConfirmRequest(
        slot_id=slot2.id,
        patient_id=patient.id,
        chief_complaint="Booking 2 - Follow-up",
        urgency_level="ROUTINE",
        idempotency_key=f"TEST-BOOK-2-{uuid.uuid4().hex[:6]}"
    )
    appt2 = asyncio.run(AppointmentService.confirm_and_sync_appointment(db, req2))
    t3 = time.time()
    print(f"Booking 2 completed in {t3 - t2:.2f}s, status: {appt2.status}, ehr_id: {appt2.ehr_appointment_id}")

    # Test 3: Booking the same slot twice
    t4 = time.time()
    req3 = schemas.AppointmentConfirmRequest(
        slot_id=slot1.id,
        patient_id=patient.id,
        chief_complaint="Booking 3 - Duplicate Slot",
        urgency_level="ROUTINE",
        idempotency_key=f"TEST-BOOK-3-{uuid.uuid4().hex[:6]}"
    )
    try:
        appt3 = asyncio.run(AppointmentService.confirm_and_sync_appointment(db, req3))
        print("Booking same slot twice unexpectedly succeeded!")
    except Exception as e:
        print(f"Booking same slot twice correctly rejected in {time.time() - t4:.2f}s with: {e}")

    # Test 4: Idempotent replay of same request
    t5 = time.time()
    appt1_replay = asyncio.run(AppointmentService.confirm_and_sync_appointment(db, req1))
    print(f"Idempotent replay completed in {time.time() - t5:.2f}s, matched appt id: {appt1_replay.id == appt1.id}")

    db.close()

if __name__ == "__main__":
    run_test()
