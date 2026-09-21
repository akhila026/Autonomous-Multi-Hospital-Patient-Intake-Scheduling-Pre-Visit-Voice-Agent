import unittest
import uuid
from datetime import datetime, timedelta, time
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal
from backend import models
from backend.auth.security import hash_password, create_access_token
from backend.auth.roles import UserRole


class TestSchedulingService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Fetch seeded hospitals
        cls.hosp_a = cls.db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-88910").first()
        cls.hosp_b = cls.db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-99201").first()
        assert cls.hosp_a and cls.hosp_b, "Seeded hospitals missing"

        # Platform Admin
        cls.p_admin = cls.db.query(models.User).filter(models.User.email == "platform.admin@aegiscare.io").first()
        cls.token_p_admin = create_access_token({"sub": cls.p_admin.id, "role": UserRole.PLATFORM_ADMIN.value})

        # Hospital A Admin
        cls.admin_a = cls.db.query(models.User).filter(models.User.email == "david.miller@stjudehealth.org").first()
        cls.token_admin_a = create_access_token({"sub": cls.admin_a.id, "role": UserRole.HOSPITAL_ADMIN.value, "hospital_id": cls.hosp_a.id})

        # Doctor A in Hospital A (Sarah Jenkins)
        cls.doc_a = cls.db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Sarah Jenkins%")).first()
        cls.doc_a_user = cls.db.query(models.User).filter(models.User.email == "sarah.jenkins@stjudehealth.org").first()
        cls.token_doc_a = create_access_token({"sub": cls.doc_a_user.id, "role": UserRole.DOCTOR.value, "hospital_id": cls.hosp_a.id, "doctor_id": cls.doc_a.id})

        # Ensure Doctor A has primary calendar and working hours (Monday to Friday, 09:00 - 17:00)
        cal_a = cls.db.query(models.Calendar).filter(models.Calendar.doctor_id == cls.doc_a.id).first()
        if not cal_a:
            cal_a = models.Calendar(
                hospital_id=cls.hosp_a.id,
                doctor_id=cls.doc_a.id,
                name="Dr. Jenkins Primary Calendar",
                is_active=True
            )
            cls.db.add(cal_a)
            cls.db.commit()
        cls.cal_a = cal_a

        # Doctor B in Hospital B (Marcus Vance)
        cls.doc_b = cls.db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Marcus Vance%")).first()
        cls.doc_b_user = cls.db.query(models.User).filter(models.User.email == "marcus.vance@metrogeneral.org").first()
        cls.token_doc_b = create_access_token({"sub": cls.doc_b_user.id, "role": UserRole.DOCTOR.value, "hospital_id": cls.hosp_b.id, "doctor_id": cls.doc_b.id})

        # Patient 1 in Hospital A (Alice Morgan)
        cls.pat_a = cls.db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first()
        cls.pat_a_user = cls.db.query(models.User).filter(models.User.email == "alice.morgan@example.com").first()
        cls.token_pat_a = create_access_token({"sub": cls.pat_a_user.id, "role": UserRole.PATIENT.value, "hospital_id": cls.hosp_a.id, "patient_id": cls.pat_a.id})

        # Patient 2 in Hospital A (For concurrency testing)
        p2_email = f"patient2.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_a2_user = models.User(
            email=p2_email,
            full_name="Second Patient A",
            role=UserRole.PATIENT.value,
            hospital_id=cls.hosp_a.id,
            password_hash=hash_password("Pass123!"),
            is_active=True
        )
        cls.db.add(cls.pat_a2_user)
        cls.db.commit()

        cls.pat_a2 = models.Patient(
            user_id=cls.pat_a2_user.id,
            primary_hospital_id=cls.hosp_a.id,
            full_name="Second Patient A",
            email=p2_email,
            phone="+1-555-111-2222"
        )
        cls.db.add(cls.pat_a2)
        cls.db.commit()
        cls.token_pat_a2 = create_access_token({"sub": cls.pat_a2_user.id, "role": UserRole.PATIENT.value, "hospital_id": cls.hosp_a.id, "patient_id": cls.pat_a2.id})

        # Patient 3 in Hospital B (For tenant isolation testing)
        pb_email = f"patient_b.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_b_user = models.User(
            email=pb_email,
            full_name="Hospital B Patient",
            role=UserRole.PATIENT.value,
            hospital_id=cls.hosp_b.id,
            password_hash=hash_password("Pass123!"),
            is_active=True
        )
        cls.db.add(cls.pat_b_user)
        cls.db.commit()

        cls.pat_b = models.Patient(
            user_id=cls.pat_b_user.id,
            primary_hospital_id=cls.hosp_b.id,
            full_name="Hospital B Patient",
            email=pb_email,
            phone="+1-555-333-4444"
        )
        cls.db.add(cls.pat_b)
        cls.db.commit()
        cls.token_pat_b = create_access_token({"sub": cls.pat_b_user.id, "role": UserRole.PATIENT.value, "hospital_id": cls.hosp_b.id, "patient_id": cls.pat_b.id})

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _auth(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _get_future_weekday_date(self, weekday: int = 1, week_offset: int = 2):
        """Returns a future datetime on the specified weekday with week offset."""
        now = datetime.utcnow()
        days_ahead = (weekday - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target = now + timedelta(days=days_ahead + 7 * week_offset)
        return target.date()

    def _clean_slot_window(self, doctor_id: str, start_time: datetime, end_time: datetime):
        """Ensures complete test idempotency by clearing existing test appointments/slots in window."""
        appts = self.db.query(models.Appointment).filter(
            models.Appointment.doctor_id == doctor_id,
            models.Appointment.start_time < end_time,
            models.Appointment.end_time > start_time
        ).all()
        for a in appts:
            self.db.delete(a)

        slots = self.db.query(models.TimeSlot).filter(
            models.TimeSlot.doctor_id == doctor_id,
            models.TimeSlot.start_time < end_time,
            models.TimeSlot.end_time > start_time
        ).all()
        for s in slots:
            self.db.delete(s)
        self.db.commit()

    # =========================================================================
    # 1. VALID SLOT BOOKING SUCCEEDS
    # =========================================================================
    def test_01_valid_slot_booking_succeeds(self):
        """Active doctor, active calendar, within hours, not blocked, compatible type -> booking succeeds."""
        target_date = self._get_future_weekday_date(weekday=1, week_offset=2)  # Tuesday (weekday 1 is configured)
        slot_start = datetime.combine(target_date, time(10, 0))
        slot_end = datetime.combine(target_date, time(10, 30))

        self._clean_slot_window(self.doc_a.id, slot_start, slot_end)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=slot_start,
            end_time=slot_end,
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()
        self.db.refresh(slot)

        # 1. Real-time validation
        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": self.doc_a.id,
            "appointment_type": "IN_PERSON"
        })
        self.assertEqual(val_res.status_code, 200)
        val_data = val_res.json()
        self.assertTrue(val_data["is_bookable"])
        self.assertIsNone(val_data["reason"])

        # 2. Reservation
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_a.id,
            "hospital_id": self.hosp_a.id,
            "appointment_type": "IN_PERSON",
            "chief_complaint": "Routine checkup"
        }, headers=self._auth(self.token_pat_a))

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "HELD")
        self.assertEqual(data["doctor_id"], self.doc_a.id)
        self.assertEqual(data["patient_id"], self.pat_a.id)

        # Verify DB state
        self.db.refresh(slot)
        self.assertEqual(slot.status, "HELD")
        self.assertIsNotNone(slot.hold_expires_at)

    # =========================================================================
    # 2. BLOCKED SLOT REJECTED
    # =========================================================================
    def test_02_blocked_slot_booking_rejected(self):
        """Slot overlapping a BlockedSlot -> rejected with reason."""
        target_date = self._get_future_weekday_date(weekday=2, week_offset=3)  # Wednesday
        block_start = datetime.combine(target_date, time(14, 0))
        block_end = datetime.combine(target_date, time(16, 0))

        self._clean_slot_window(self.doc_a.id, block_start, block_end)

        block = models.BlockedSlot(
            hospital_id=self.hosp_a.id,
            doctor_id=self.doc_a.id,
            calendar_id=self.cal_a.id,
            start_time=block_start,
            end_time=block_end,
            reason="Orthopedic Surgery"
        )
        self.db.add(block)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=datetime.combine(target_date, time(14, 30)),
            end_time=datetime.combine(target_date, time(15, 0)),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        # Validate
        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": self.doc_a.id,
            "appointment_type": "IN_PERSON"
        })
        self.assertEqual(val_res.status_code, 200)
        val_data = val_res.json()
        self.assertFalse(val_data["is_bookable"])
        self.assertIn("blocked", val_data["reason"].lower())

        # Attempt to reserve
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_a.id,
            "hospital_id": self.hosp_a.id
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(res.status_code, 400)
        self.assertIn("blocked", res.json()["detail"].lower())

    # =========================================================================
    # 3. ALREADY BOOKED / HELD SLOT REJECTED
    # =========================================================================
    def test_03_already_booked_slot_rejected(self):
        """Slot with status BOOKED -> rejected with HTTP 409 Conflict."""
        target_date = self._get_future_weekday_date(weekday=1, week_offset=4)
        slot_start = datetime.combine(target_date, time(11, 0))
        slot_end = datetime.combine(target_date, time(11, 30))
        self._clean_slot_window(self.doc_a.id, slot_start, slot_end)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=slot_start,
            end_time=slot_end,
            status="BOOKED"
        )
        self.db.add(slot)
        self.db.commit()

        # Validate
        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": self.doc_a.id
        })
        self.assertEqual(val_res.status_code, 200)
        self.assertFalse(val_res.json()["is_bookable"])
        self.assertIn("booked", val_res.json()["reason"].lower())

        # Reserve -> 409 Conflict
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_a.id,
            "hospital_id": self.hosp_a.id
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(res.status_code, 409)

    # =========================================================================
    # 4. INACTIVE DOCTOR REJECTED
    # =========================================================================
    def test_04_inactive_doctor_rejected(self):
        """Inactive doctor -> booking rejected."""
        inactive_doc = models.Doctor(
            hospital_id=self.hosp_a.id,
            full_name="Dr. Inactive Specialist",
            specialty="Orthopedics",
            is_active=False,
            status="INACTIVE"
        )
        self.db.add(inactive_doc)
        self.db.commit()

        target_date = self._get_future_weekday_date(weekday=1)
        slot = models.TimeSlot(
            doctor_id=inactive_doc.id,
            start_time=datetime.combine(target_date, time(9, 0)),
            end_time=datetime.combine(target_date, time(9, 30)),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        # Validate
        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": inactive_doc.id
        })
        self.assertEqual(val_res.status_code, 200)
        self.assertFalse(val_res.json()["is_bookable"])
        self.assertIn("inactive", val_res.json()["reason"].lower())

        # Reserve
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": inactive_doc.id,
            "hospital_id": self.hosp_a.id
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(res.status_code, 400)

    # =========================================================================
    # 5. OUTSIDE WORKING HOURS REJECTED
    # =========================================================================
    def test_05_outside_working_hours_rejected(self):
        """Slot outside configured working hours (07:00 or Sunday) -> rejected."""
        target_date = self._get_future_weekday_date(weekday=1)
        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=datetime.combine(target_date, time(6, 30)),
            end_time=datetime.combine(target_date, time(7, 0)),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": self.doc_a.id
        })
        self.assertEqual(val_res.status_code, 200)
        self.assertFalse(val_res.json()["is_bookable"])
        self.assertIn("outside", val_res.json()["reason"].lower())

        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_a.id,
            "hospital_id": self.hosp_a.id
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(res.status_code, 400)

    # =========================================================================
    # 6. DOCTOR LEAVE REJECTED
    # =========================================================================
    def test_06_doctor_leave_rejected(self):
        """Doctor on leave -> rejected with clear leave notification."""
        target_date = self._get_future_weekday_date(weekday=3, week_offset=7)  # Thursday
        block_start = datetime.combine(target_date, time(0, 0))
        block_end = datetime.combine(target_date, time(23, 59))
        self._clean_slot_window(self.doc_a.id, block_start, block_end)

        leave_block = models.BlockedSlot(
            hospital_id=self.hosp_a.id,
            doctor_id=self.doc_a.id,
            calendar_id=self.cal_a.id,
            start_time=block_start,
            end_time=block_end,
            reason="Medical Conference & Annual Leave"
        )
        self.db.add(leave_block)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=datetime.combine(target_date, time(10, 0)),
            end_time=datetime.combine(target_date, time(10, 30)),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": self.doc_a.id
        })
        self.assertEqual(val_res.status_code, 200)
        self.assertFalse(val_res.json()["is_bookable"])
        self.assertIn("leave", val_res.json()["reason"].lower())

    # =========================================================================
    # 7. INCOMPATIBLE APPOINTMENT TYPE REJECTED
    # =========================================================================
    def test_07_incompatible_appointment_type_rejected(self):
        """Doctor configured for IN_PERSON only -> VIDEO_CONSULT rejected."""
        doc_in_person_only = models.Doctor(
            hospital_id=self.hosp_a.id,
            full_name="Dr. Hands On, Surgeon",
            specialty="Orthopedics",
            supported_appointment_types="IN_PERSON",
            is_active=True,
            status="ACTIVE"
        )
        self.db.add(doc_in_person_only)
        self.db.commit()

        # Add working schedule on Tuesday (weekday 1)
        sched = models.AvailabilitySchedule(
            doctor_id=doc_in_person_only.id,
            day_of_week=1,
            start_time="09:00",
            end_time="17:00",
            is_active=True
        )
        self.db.add(sched)

        target_date = self._get_future_weekday_date(weekday=1)
        slot = models.TimeSlot(
            doctor_id=doc_in_person_only.id,
            start_time=datetime.combine(target_date, time(10, 0)),
            end_time=datetime.combine(target_date, time(10, 30)),
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        # Validate with unsupported type
        val_res = self.client.post("/api/scheduling/validate", json={
            "slot_id": slot.id,
            "doctor_id": doc_in_person_only.id,
            "appointment_type": "VIDEO_CONSULT"
        })
        self.assertEqual(val_res.status_code, 200)
        self.assertFalse(val_res.json()["is_bookable"])
        self.assertIn("not supported", val_res.json()["reason"].lower())

        # Reserve with unsupported type
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": doc_in_person_only.id,
            "hospital_id": self.hosp_a.id,
            "appointment_type": "VIDEO_CONSULT"
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(res.status_code, 400)

    # =========================================================================
    # 8. CONCURRENT BOOKING - EXACTLY ONE SUCCEEDS
    # =========================================================================
    def test_08_concurrent_booking_exactly_one_succeeds(self):
        """
        Two simultaneous requests targeting the same slot run in parallel:
        Exactly one succeeds (200), the other receives 409 Conflict.
        Exactly one appointment created in DB.
        """
        target_date = self._get_future_weekday_date(weekday=1, week_offset=9)
        slot_start = datetime.combine(target_date, time(15, 0))
        slot_end = datetime.combine(target_date, time(15, 30))

        self._clean_slot_window(self.doc_a.id, slot_start, slot_end)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=slot_start,
            end_time=slot_end,
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()
        slot_id = slot.id

        def book_for_patient(patient_id: str, token: str):
            client = TestClient(app)
            return client.post("/api/scheduling/reserve", json={
                "slot_id": slot_id,
                "patient_id": patient_id,
                "doctor_id": self.doc_a.id,
                "hospital_id": self.hosp_a.id,
                "appointment_type": "IN_PERSON",
                "chief_complaint": f"Concurrent booking test for {patient_id}"
            }, headers={"Authorization": f"Bearer {token}"})

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(book_for_patient, self.pat_a.id, self.token_pat_a)
            fut2 = executor.submit(book_for_patient, self.pat_a2.id, self.token_pat_a2)

            res1 = fut1.result()
            res2 = fut2.result()

        status_codes = [res1.status_code, res2.status_code]
        self.assertIn(200, status_codes, f"Neither succeeded: {status_codes}, bodies: {res1.text}, {res2.text}")
        self.assertIn(409, status_codes, f"Neither failed with 409 Conflict: {status_codes}")

        # Verify DB: exactly 1 appointment created for this slot
        db_appts = self.db.query(models.Appointment).filter(
            models.Appointment.slot_id == slot_id,
            models.Appointment.status == "HELD"
        ).all()
        self.assertEqual(len(db_appts), 1, "Expected exactly 1 held appointment in DB")

    # =========================================================================
    # 9. TENANT ISOLATION ENFORCEMENT
    # =========================================================================
    def test_09_tenant_isolation_rejected(self):
        """
        Hospital Admin / Patient from Hospital A cannot reserve a slot for Hospital B doctor.
        Cross-tenant booking is rejected with 403 Forbidden or 400 Bad Request.
        """
        target_date = self._get_future_weekday_date(weekday=1, week_offset=10)
        slot_start = datetime.combine(target_date, time(10, 0))
        slot_end = datetime.combine(target_date, time(10, 30))
        self._clean_slot_window(self.doc_b.id, slot_start, slot_end)

        slot_b = models.TimeSlot(
            doctor_id=self.doc_b.id,
            start_time=slot_start,
            end_time=slot_end,
            status="AVAILABLE"
        )
        self.db.add(slot_b)
        self.db.commit()

        # Hospital A Admin attempts to reserve Hospital B doctor
        res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot_b.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_b.id,
            "hospital_id": self.hosp_b.id
        }, headers=self._auth(self.token_admin_a))
        self.assertIn(res.status_code, [400, 403], f"Expected 400 or 403, got {res.status_code}")

    # =========================================================================
    # 10. RELEASE RESERVATION
    # =========================================================================
    def test_10_release_reservation(self):
        """
        1. Valid reservation is released; slot returns to AVAILABLE.
        2. Unauthorized patient attempting to release someone else's reservation is rejected with 403.
        """
        target_date = self._get_future_weekday_date(weekday=1, week_offset=11)
        slot_start = datetime.combine(target_date, time(16, 0))
        slot_end = datetime.combine(target_date, time(16, 30))
        self._clean_slot_window(self.doc_a.id, slot_start, slot_end)

        slot = models.TimeSlot(
            doctor_id=self.doc_a.id,
            start_time=slot_start,
            end_time=slot_end,
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()

        # Step 1: Book reservation
        book_res = self.client.post("/api/scheduling/reserve", json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "doctor_id": self.doc_a.id,
            "hospital_id": self.hosp_a.id,
            "appointment_type": "IN_PERSON"
        }, headers=self._auth(self.token_pat_a))
        self.assertEqual(book_res.status_code, 200)
        appt_id = book_res.json()["id"]

        # Step 2: Patient B attempts to release Patient A's reservation -> 403 Forbidden
        unauth_res = self.client.post(
            f"/api/scheduling/release/{appt_id}",
            headers=self._auth(self.token_pat_b)
        )
        self.assertEqual(unauth_res.status_code, 403)

        # Step 3: Patient A releases own reservation -> 200 OK
        auth_res = self.client.post(
            f"/api/scheduling/release/{appt_id}",
            headers=self._auth(self.token_pat_a)
        )
        self.assertEqual(auth_res.status_code, 200)
        rel_data = auth_res.json()
        self.assertTrue(rel_data["success"])
        self.assertEqual(rel_data["slot_status"], "AVAILABLE")

        # Verify DB states
        self.db.refresh(slot)
        self.assertEqual(slot.status, "AVAILABLE")
        self.assertIsNone(slot.hold_expires_at)

        appt = self.db.query(models.Appointment).filter(models.Appointment.id == appt_id).first()
        self.assertEqual(appt.status, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
