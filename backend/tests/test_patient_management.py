import unittest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal
from backend import models
from backend.auth.security import hash_password, create_access_token
from backend.auth.roles import UserRole

class TestPatientManagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Seeded hospital
        cls.hospital = cls.db.query(models.Hospital).first()
        assert cls.hospital, "Hospital required for test"

        # Seeded doctor
        cls.doctor = cls.db.query(models.Doctor).filter(models.Doctor.hospital_id == cls.hospital.id).first()
        assert cls.doctor, "Doctor required for test"

        # Create two distinct test patients with user accounts for ownership verification
        cls.pat_a_email = f"patient.alpha.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_a_user = models.User(
            email=cls.pat_a_email,
            password_hash=hash_password("PassAlpha123!"),
            full_name="Alpha Test Patient",
            role=UserRole.PATIENT.value,
            phone="+1-555-111-2222",
            hospital_id=cls.hospital.id,
            is_active=True
        )
        cls.db.add(cls.pat_a_user)
        cls.db.commit()

        cls.pat_a = models.Patient(
            user_id=cls.pat_a_user.id,
            primary_hospital_id=cls.hospital.id,
            patient_mrn=f"MRN-A-{uuid.uuid4().hex[:6].upper()}",
            full_name="Alpha Test Patient",
            email=cls.pat_a_email,
            phone="+1-555-111-2222",
            date_of_birth="1990-01-15",
            gender="Female",
            emergency_contact="Emergency Alpha (+1-555-111-9999)",
            communication_preference="SMS",
            external_patient_id="EXT-PAT-ALPHA",
            preferences={"language": "en", "accessibility": "standard"}
        )
        cls.db.add(cls.pat_a)
        cls.db.commit()

        cls.pat_b_email = f"patient.beta.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_b_user = models.User(
            email=cls.pat_b_email,
            password_hash=hash_password("PassBeta123!"),
            full_name="Beta Test Patient",
            role=UserRole.PATIENT.value,
            phone="+1-555-333-4444",
            hospital_id=cls.hospital.id,
            is_active=True
        )
        cls.db.add(cls.pat_b_user)
        cls.db.commit()

        cls.pat_b = models.Patient(
            user_id=cls.pat_b_user.id,
            primary_hospital_id=cls.hospital.id,
            patient_mrn=f"MRN-B-{uuid.uuid4().hex[:6].upper()}",
            full_name="Beta Test Patient",
            email=cls.pat_b_email,
            phone="+1-555-333-4444",
            date_of_birth="1985-06-20",
            gender="Male",
            emergency_contact="Emergency Beta (+1-555-333-8888)",
            communication_preference="EMAIL",
            external_patient_id="EXT-PAT-BETA",
            preferences={"language": "es"}
        )
        cls.db.add(cls.pat_b)
        cls.db.commit()

        # JWT tokens
        cls.token_a = create_access_token({
            "sub": cls.pat_a_user.id,
            "email": cls.pat_a_user.email,
            "role": UserRole.PATIENT.value,
            "patient_id": cls.pat_a.id,
            "hospital_id": cls.hospital.id
        })

        cls.token_b = create_access_token({
            "sub": cls.pat_b_user.id,
            "email": cls.pat_b_user.email,
            "role": UserRole.PATIENT.value,
            "patient_id": cls.pat_b.id,
            "hospital_id": cls.hospital.id
        })

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _auth(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _create_available_slot(self, days_offset=7, hour=10):
        start = datetime.utcnow() + timedelta(days=days_offset)
        # Ensure it falls on a weekday (Monday-Friday) for doctor working hours
        while start.weekday() > 4:
            start += timedelta(days=1)
        start = start.replace(hour=hour, minute=0, second=0, microsecond=0)
        end = start + timedelta(minutes=30)

        # Clear any conflicting appointments or blocked slots in this exact slot window
        self.db.query(models.Appointment).filter(
            models.Appointment.doctor_id == self.doctor.id,
            models.Appointment.start_time == start
        ).delete()
        self.db.query(models.BlockedSlot).filter(
            models.BlockedSlot.doctor_id == self.doctor.id,
            models.BlockedSlot.start_time <= end,
            models.BlockedSlot.end_time >= start
        ).delete()
        self.db.commit()

        existing = self.db.query(models.TimeSlot).filter(
            models.TimeSlot.doctor_id == self.doctor.id,
            models.TimeSlot.start_time == start
        ).first()
        if existing:
            existing.status = "AVAILABLE"
            existing.hold_expires_at = None
            self.db.commit()
            self.db.refresh(existing)
            return existing

        slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=start,
            end_time=end,
            status="AVAILABLE"
        )
        self.db.add(slot)
        self.db.commit()
        self.db.refresh(slot)
        return slot

    # =========================================================================
    # 1. REGISTRATION AND LOGIN
    # =========================================================================

    def test_01_patient_registration_with_password(self):
        """Registering with a password creates both Patient and User accounts."""
        new_email = f"new.patient.{uuid.uuid4().hex[:6]}@example.com"
        reg_payload = {
            "full_name": "Clara Higgins",
            "email": new_email,
            "phone": "+1-555-912-3456",
            "password": "SecurePassword123!",
            "date_of_birth": "1994-08-12",
            "gender": "Female",
            "emergency_contact": "Mark Higgins (+1-555-912-7890)",
            "communication_preference": "WHATSAPP",
            "external_patient_id": "EXT-HIGGINS-01",
            "preferences": {"language": "en"}
        }
        res = self.client.post("/api/patients/register", json=reg_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["full_name"], "Clara Higgins")
        self.assertEqual(data["email"], new_email)
        self.assertEqual(data["communication_preference"], "WHATSAPP")
        self.assertEqual(data["external_patient_id"], "EXT-HIGGINS-01")
        self.assertIsNotNone(data["patient_mrn"])

        # Verify immediate login with the registered credentials
        login_res = self.client.post("/api/auth/login", json={
            "email": new_email,
            "password": "SecurePassword123!"
        })
        self.assertEqual(login_res.status_code, 200)
        token_data = login_res.json()
        self.assertIn("access_token", token_data)
        self.assertEqual(token_data["role"], "PATIENT")
        self.assertEqual(token_data["patient_id"], data["id"])

    def test_02_get_current_patient_profile(self):
        """GET /patients/me retrieves authenticated patient profile."""
        res = self.client.get("/api/patients/me", headers=self._auth(self.token_a))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], self.pat_a.id)
        self.assertEqual(data["full_name"], self.pat_a.full_name)
        self.assertEqual(data["date_of_birth"], "1990-01-15")
        self.assertEqual(data["communication_preference"], "SMS")
        self.assertEqual(data["external_patient_id"], "EXT-PAT-ALPHA")

    # =========================================================================
    # 2. PROFILE & PREFERENCES MANAGEMENT (DATA MINIMIZATION)
    # =========================================================================

    def test_03_patient_profile_update_and_data_minimization(self):
        """Patient updates profile fields; data minimization preserves only allowed preferences."""
        update_payload = {
            "phone": "+1-555-111-9999",
            "date_of_birth": "1990-01-20",
            "emergency_contact": "Updated Emergency Contact",
            "communication_preference": "EMAIL",
            "preferences": {
                "language": "fr",
                "accessibility": "large_text",
                "disallowed_field": "some_arbitrary_unneeded_data"
            }
        }
        res = self.client.patch(
            f"/api/patients/{self.pat_a.id}",
            headers=self._auth(self.token_a),
            json=update_payload
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["phone"], "+1-555-111-9999")
        self.assertEqual(data["date_of_birth"], "1990-01-20")
        self.assertEqual(data["communication_preference"], "EMAIL")
        # Ensure disallowed field was pruned via data minimization
        self.assertIn("language", data["preferences"])
        self.assertNotIn("disallowed_field", data["preferences"])

    def test_04_patient_preferences_update_via_dashboard(self):
        """PUT /dashboard/patient/preferences updates delivery preference and contact details."""
        res = self.client.put(
            "/api/dashboard/patient/preferences",
            headers=self._auth(self.token_a),
            json={
                "communication_preference": "VOICE",
                "emergency_contact": "New Emergency (+1-555-000-1111)"
            }
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["communication_preference"], "VOICE")
        self.assertEqual(data["emergency_contact"], "New Emergency (+1-555-000-1111)")

    # =========================================================================
    # 3. APPOINTMENT LIFECYCLE: BOOK, RESCHEDULE, CANCEL
    # =========================================================================

    def test_05_book_reschedule_and_cancel_appointment_lifecycle(self):
        """Full appointment lifecycle: Book -> Reschedule -> Cancel."""
        slot1 = self._create_available_slot(days_offset=14, hour=10)
        slot2 = self._create_available_slot(days_offset=15, hour=14)

        # 1. Hold slot
        hold_res = self.client.post("/api/appointments/hold", headers=self._auth(self.token_a), json={
            "slot_id": slot1.id,
            "patient_id": self.pat_a.id,
            "chief_complaint": "Persistent lower back discomfort",
            "urgency_level": "ROUTINE"
        })
        self.assertEqual(hold_res.status_code, 200)
        appt_id = hold_res.json()["appointment_id"]

        # 2. Confirm booking
        confirm_res = self.client.post("/api/appointments/confirm", headers=self._auth(self.token_a), json={
            "slot_id": slot1.id,
            "patient_id": self.pat_a.id,
            "chief_complaint": "Persistent lower back discomfort",
            "idempotency_key": f"IDEM-TEST-{uuid.uuid4().hex[:8]}"
        })
        self.assertEqual(confirm_res.status_code, 200)
        appt_data = confirm_res.json()
        self.assertEqual(appt_data["status"], "CONFIRMED")
        self.assertEqual(appt_data["slot_id"], slot1.id)

        # Verify slot1 is BOOKED
        self.db.refresh(slot1)
        self.assertEqual(slot1.status, "BOOKED")

        # 3. Reschedule appointment to slot2
        resched_res = self.client.post(
            f"/api/appointments/{appt_id}/reschedule",
            headers=self._auth(self.token_a),
            json={
                "new_slot_id": slot2.id,
                "reason": "Work conflict on original day"
            }
        )
        self.assertEqual(resched_res.status_code, 200)
        resched_data = resched_res.json()
        self.assertEqual(resched_data["status"], "RESCHEDULED")
        self.assertEqual(resched_data["slot_id"], slot2.id)

        # Old slot1 should be released (AVAILABLE) and new slot2 should be BOOKED
        self.db.refresh(slot1)
        self.db.refresh(slot2)
        self.assertEqual(slot1.status, "AVAILABLE")
        self.assertEqual(slot2.status, "BOOKED")

        # 4. Cancel appointment
        cancel_res = self.client.post(
            f"/api/appointments/{appt_id}/cancel",
            headers=self._auth(self.token_a),
            json={"reason": "Condition improved spontaneously"}
        )
        self.assertEqual(cancel_res.status_code, 200)
        cancel_data = cancel_res.json()
        self.assertEqual(cancel_data["status"], "CANCELLED")
        self.assertEqual(cancel_data["cancellation_reason"], "Condition improved spontaneously")

        # slot2 should now be released back to AVAILABLE
        self.db.refresh(slot2)
        self.assertEqual(slot2.status, "AVAILABLE")

    # =========================================================================
    # 4. PRE-VISIT QUESTIONNAIRES
    # =========================================================================

    def test_06_previsit_questionnaire_completion(self):
        """Patient can retrieve and complete a pre-visit questionnaire for their appointment."""
        slot = self._create_available_slot(days_offset=16, hour=10)
        confirm_res = self.client.post("/api/appointments/confirm", headers=self._auth(self.token_a), json={
            "slot_id": slot.id,
            "patient_id": self.pat_a.id,
            "chief_complaint": "Right knee stiffness and swelling",
            "urgency_level": "ROUTINE",
            "idempotency_key": f"IDEM-Q-{uuid.uuid4().hex[:8]}"
        })
        self.assertEqual(confirm_res.status_code, 200)
        appt_id = confirm_res.json()["id"]

        # Retrieve questionnaire
        q_res = self.client.get(f"/api/questionnaires/{appt_id}", headers=self._auth(self.token_a))
        self.assertEqual(q_res.status_code, 200)
        q_data = q_res.json()
        self.assertEqual(q_data["appointment_id"], appt_id)
        self.assertIn("questions", q_data)
        self.assertGreater(len(q_data["questions"]), 0)

        # Submit answers
        submit_res = self.client.post(
            f"/api/questionnaires/{appt_id}/submit",
            headers=self._auth(self.token_a),
            json={
                "answers": {
                    "q1": "2-3 days",
                    "q2": "4",
                    "q3": "Ibuprofen 200mg twice a day",
                    "q4": "No known drug allergies"
                }
            }
        )
        self.assertEqual(submit_res.status_code, 200)
        sub_data = submit_res.json()
        self.assertIn(sub_data["status"], ("SUBMITTED", "COMPLETED"))
        self.assertIsNotNone(sub_data["patient_intake_summary"])
        self.assertIn("Ibuprofen", sub_data["patient_intake_summary"])

    # =========================================================================
    # 5. PATIENT DASHBOARD 7 SECTIONS
    # =========================================================================

    def test_07_patient_dashboard_7_sections(self):
        """Dashboard renders all 7 required sections with scoped appointment and profile data."""
        res = self.client.get("/api/dashboard/patient", headers=self._auth(self.token_a))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        required_sections = [
            "home",
            "ai_assistant",
            "upcoming_appointments",
            "historical_appointments",
            "questionnaires",
            "preferences",
            "profile_management"
        ]
        for section in required_sections:
            self.assertIn(section, data, f"Missing required dashboard section: {section}")

        self.assertEqual(data["profile_management"]["id"], self.pat_a.id)
        self.assertIn("communication_preference", data["preferences"])

    # =========================================================================
    # 6. STRICT PATIENT OWNERSHIP ENFORCEMENT
    # =========================================================================

    def test_08_cross_patient_profile_access_blocked(self):
        """Patient A cannot view or update Patient B's profile (HTTP 403 Forbidden)."""
        # GET Patient B's profile with Patient A's token
        res_get = self.client.get(f"/api/patients/{self.pat_b.id}", headers=self._auth(self.token_a))
        self.assertEqual(res_get.status_code, 403)
        self.assertIn("Patient Ownership Violation", res_get.json()["detail"])

        # PATCH Patient B's profile with Patient A's token
        res_patch = self.client.patch(
            f"/api/patients/{self.pat_b.id}",
            headers=self._auth(self.token_a),
            json={"phone": "+1-555-000-HACK"}
        )
        self.assertEqual(res_patch.status_code, 403)
        self.assertIn("Patient Ownership Violation", res_patch.json()["detail"])

    def test_09_cross_patient_appointment_and_actions_blocked(self):
        """Patient A cannot view, reschedule, or cancel Patient B's appointment."""
        slot_b = self._create_available_slot(days_offset=17, hour=14)
        slot_b2 = self._create_available_slot(days_offset=18, hour=10)

        confirm_b = self.client.post("/api/appointments/confirm", headers=self._auth(self.token_b), json={
            "slot_id": slot_b.id,
            "patient_id": self.pat_b.id,
            "chief_complaint": "Beta Patient consultation",
            "idempotency_key": f"IDEM-B-{uuid.uuid4().hex[:8]}"
        })
        self.assertEqual(confirm_b.status_code, 200)
        appt_b_id = confirm_b.json()["id"]

        # Patient A attempts to view Patient B's appointment
        view_res = self.client.get(f"/api/appointments/{appt_b_id}", headers=self._auth(self.token_a))
        self.assertEqual(view_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", view_res.json()["detail"])

        # Patient A attempts to reschedule Patient B's appointment
        resched_res = self.client.post(
            f"/api/appointments/{appt_b_id}/reschedule",
            headers=self._auth(self.token_a),
            json={"new_slot_id": slot_b2.id}
        )
        self.assertEqual(resched_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", resched_res.json()["detail"])

        # Patient A attempts to cancel Patient B's appointment
        cancel_res = self.client.post(
            f"/api/appointments/{appt_b_id}/cancel",
            headers=self._auth(self.token_a),
            json={"reason": "Unauthorized cancellation"}
        )
        self.assertEqual(cancel_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", cancel_res.json()["detail"])

        # Patient A attempts to submit Patient B's questionnaire
        quest_res = self.client.post(
            f"/api/questionnaires/{appt_b_id}/submit",
            headers=self._auth(self.token_a),
            json={"answers": {"q1": "unauthorized answer"}}
        )
        self.assertEqual(quest_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", quest_res.json()["detail"])

    def test_10_cross_patient_conversational_context_blocked(self):
        """Patient A cannot inspect Patient B's AI session context."""
        sess_b_id = f"voice-sess-b-{uuid.uuid4().hex[:8]}"
        # Create session belonging to Patient B
        conv_b = models.AIConversation(
            session_id=sess_b_id,
            patient_id=self.pat_b.id,
            hospital_id=self.hospital.id,
            channel="web_voice"
        )
        self.db.add(conv_b)
        self.db.commit()

        # Patient A attempts to inspect Patient B's context
        ctx_res = self.client.get(f"/api/ai/agent/context/{sess_b_id}", headers=self._auth(self.token_a))
        self.assertEqual(ctx_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", ctx_res.json()["detail"])

        # Patient A attempts to reset Patient B's context
        reset_res = self.client.post(f"/api/ai/agent/reset/{sess_b_id}", headers=self._auth(self.token_a))
        self.assertEqual(reset_res.status_code, 403)
        self.assertIn("Patient Ownership Violation", reset_res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
