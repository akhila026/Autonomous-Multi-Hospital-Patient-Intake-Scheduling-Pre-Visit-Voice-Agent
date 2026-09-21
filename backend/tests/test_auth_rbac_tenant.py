import unittest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal
from backend import models
from backend.auth.security import hash_password, create_access_token
from backend.auth.roles import UserRole

class TestAuthRbacTenant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Fetch seeded entities
        cls.hosp_a = cls.db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-88910").first()
        cls.hosp_b = cls.db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-99201").first()
        assert cls.hosp_a and cls.hosp_b, "Seeded hospitals missing"

        # Platform Admin
        cls.p_admin_user = cls.db.query(models.User).filter(models.User.email == "platform.admin@aegiscare.io").first()

        # Hospital Admins
        cls.admin_a_user = cls.db.query(models.User).filter(models.User.email == "david.miller@stjudehealth.org").first()
        cls.admin_b_user = cls.db.query(models.User).filter(models.User.email == "claire.vance@metrogeneral.org").first()

        # Doctors
        cls.doc_a = cls.db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Sarah Jenkins%")).first()
        cls.doc_a_user = cls.db.query(models.User).filter(models.User.email == "sarah.jenkins@stjudehealth.org").first()

        cls.doc_b = cls.db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Marcus Vance%")).first()
        cls.doc_b_user = cls.db.query(models.User).filter(models.User.email == "marcus.vance@metrogeneral.org").first()

        # Patients
        cls.pat_a = cls.db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first()
        cls.pat_a_user = cls.db.query(models.User).filter(models.User.email == "alice.morgan@example.com").first()

        # Create Patient B (scoped to Hospital B) for isolation tests
        patient_b_email = f"patient.b.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_b_user = models.User(
            email=patient_b_email,
            full_name="Patient Bob",
            role=UserRole.PATIENT.value,
            hospital_id=cls.hosp_b.id,
            password_hash=hash_password("BobPass123!"),
            is_active=True
        )
        cls.db.add(cls.pat_b_user)
        cls.db.commit()

        cls.pat_b = models.Patient(
            user_id=cls.pat_b_user.id,
            primary_hospital_id=cls.hosp_b.id,
            full_name="Patient Bob",
            email=patient_b_email,
            phone="+1-555-888-9999"
        )
        cls.db.add(cls.pat_b)
        cls.db.commit()

        # Create Appointments for Patient A and Patient B
        cls.appt_a = models.Appointment(
            hospital_id=cls.hosp_a.id,
            doctor_id=cls.doc_a.id,
            patient_id=cls.pat_a.id,
            idempotency_key=f"IDEM-TEST-A-{uuid.uuid4().hex}",
            status="CONFIRMED",
            chief_complaint="Knee swelling"
        )
        cls.appt_b = models.Appointment(
            hospital_id=cls.hosp_b.id,
            doctor_id=cls.doc_b.id,
            patient_id=cls.pat_b.id,
            idempotency_key=f"IDEM-TEST-B-{uuid.uuid4().hex}",
            status="CONFIRMED",
            chief_complaint="Skin rash"
        )
        cls.db.add_all([cls.appt_a, cls.appt_b])
        cls.db.commit()

        # Helper tokens
        cls.token_p_admin = create_access_token({"sub": cls.p_admin_user.id, "role": UserRole.PLATFORM_ADMIN.value})
        cls.token_admin_a = create_access_token({"sub": cls.admin_a_user.id, "role": UserRole.HOSPITAL_ADMIN.value, "hospital_id": cls.hosp_a.id})
        cls.token_admin_b = create_access_token({"sub": cls.admin_b_user.id, "role": UserRole.HOSPITAL_ADMIN.value, "hospital_id": cls.hosp_b.id})
        cls.token_doc_a = create_access_token({"sub": cls.doc_a_user.id, "role": UserRole.DOCTOR.value, "hospital_id": cls.hosp_a.id, "doctor_id": cls.doc_a.id})
        cls.token_doc_b = create_access_token({"sub": cls.doc_b_user.id, "role": UserRole.DOCTOR.value, "hospital_id": cls.hosp_b.id, "doctor_id": cls.doc_b.id})
        cls.token_pat_a = create_access_token({"sub": cls.pat_a_user.id, "role": UserRole.PATIENT.value, "hospital_id": cls.hosp_a.id, "patient_id": cls.pat_a.id})
        cls.token_pat_b = create_access_token({"sub": cls.pat_b_user.id, "role": UserRole.PATIENT.value, "hospital_id": cls.hosp_b.id, "patient_id": cls.pat_b.id})

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _auth_header(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    # =========================================================================
    # 1. AUTHENTICATION TESTS
    # =========================================================================

    def test_valid_login_succeeds(self):
        """Valid login returns 200, JWT token, and verified role."""
        res = self.client.post("/api/auth/login", json={
            "email": "alice.morgan@example.com",
            "password": "PatientPass123!"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["role"], "PATIENT")
        self.assertEqual(data["email"], "alice.morgan@example.com")

    def test_invalid_credentials_fail(self):
        """Invalid password returns HTTP 401 Unauthorized."""
        res = self.client.post("/api/auth/login", json={
            "email": "alice.morgan@example.com",
            "password": "WrongPassword999!"
        })
        self.assertEqual(res.status_code, 401)
        self.assertIn("Invalid email or password", res.json()["detail"])

    def test_nonexistent_user_login_fails(self):
        """Non-existent email returns HTTP 401 Unauthorized."""
        res = self.client.post("/api/auth/login", json={
            "email": "ghost.user@example.com",
            "password": "AnyPassword123!"
        })
        self.assertEqual(res.status_code, 401)

    def test_unauthenticated_protected_endpoint_rejected(self):
        """Accessing protected endpoint without token returns HTTP 401."""
        res = self.client.get("/api/auth/me")
        self.assertEqual(res.status_code, 401)

    def test_invalid_jwt_rejected(self):
        """Tampered or invalid JWT returns HTTP 401."""
        res = self.client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.jwt.payload"})
        self.assertEqual(res.status_code, 401)

    def test_expired_jwt_rejected(self):
        """Expired JWT token returns HTTP 401."""
        expired_token = create_access_token(
            {"sub": self.pat_a_user.id, "role": "PATIENT"},
            expires_delta=timedelta(seconds=-60)
        )
        res = self.client.get("/api/auth/me", headers=self._auth_header(expired_token))
        self.assertEqual(res.status_code, 401)
        self.assertIn("expired", res.json()["detail"].lower())

    # =========================================================================
    # 2. ROLE-BASED ACCESS CONTROL (RBAC) TESTS
    # =========================================================================

    def test_platform_admin_only_endpoints(self):
        """Platform Admin can approve hospitals; Hospital Admin and Patient are rejected with 403."""
        # 1. Platform Admin succeeds
        res_p = self.client.patch(
            f"/api/hospitals/{self.hosp_a.id}/approval",
            json={"status": "APPROVED"},
            headers=self._auth_header(self.token_p_admin)
        )
        self.assertEqual(res_p.status_code, 200)

        # 2. Hospital Admin rejected
        res_h = self.client.patch(
            f"/api/hospitals/{self.hosp_a.id}/approval",
            json={"status": "APPROVED"},
            headers=self._auth_header(self.token_admin_a)
        )
        self.assertEqual(res_h.status_code, 403)

        # 3. Patient rejected
        res_pat = self.client.patch(
            f"/api/hospitals/{self.hosp_a.id}/approval",
            json={"status": "APPROVED"},
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res_pat.status_code, 403)

    def test_hospital_admin_endpoints_reject_doctor_and_patient(self):
        """Doctors and Patients cannot create doctors or list hospital patient registry."""
        # Patient attempting doctor creation -> 403
        res_pat = self.client.post(
            f"/api/doctors/hospital/{self.hosp_a.id}",
            json={"full_name": "Dr. Hacker", "specialty": "General Medicine"},
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res_pat.status_code, 403)

        # Doctor attempting doctor creation -> 403
        res_doc = self.client.post(
            f"/api/doctors/hospital/{self.hosp_a.id}",
            json={"full_name": "Dr. Hacker", "specialty": "General Medicine"},
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res_doc.status_code, 403)

        # Patient attempting to list patient registry -> 403
        res_list = self.client.get("/api/patients", headers=self._auth_header(self.token_pat_a))
        self.assertEqual(res_list.status_code, 403)

    # =========================================================================
    # 3. TENANT ISOLATION TESTS
    # =========================================================================

    def test_hospital_admin_a_accesses_hospital_a_data(self):
        """Hospital Admin A can access Hospital A profile."""
        res = self.client.get(
            f"/api/hospitals/{self.hosp_a.id}",
            headers=self._auth_header(self.token_admin_a)
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], self.hosp_a.id)

    def test_hospital_admin_a_cannot_access_hospital_b_data(self):
        """Hospital Admin A accessing Hospital B data is rejected with HTTP 403."""
        res = self.client.get(
            f"/api/hospitals/{self.hosp_b.id}",
            headers=self._auth_header(self.token_admin_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Tenant Isolation Violation", res.json()["detail"])

    def test_hospital_admin_b_cannot_access_hospital_a_data(self):
        """Hospital Admin B accessing Hospital A data is rejected with HTTP 403."""
        res = self.client.get(
            f"/api/hospitals/{self.hosp_a.id}",
            headers=self._auth_header(self.token_admin_b)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Tenant Isolation Violation", res.json()["detail"])

    def test_cross_hospital_doctor_creation_rejected(self):
        """Hospital Admin A attempting to create a doctor in Hospital B is rejected with HTTP 403."""
        res = self.client.post(
            f"/api/doctors/hospital/{self.hosp_b.id}",
            json={"full_name": "Dr. Cross Hospital", "specialty": "Orthopedics"},
            headers=self._auth_header(self.token_admin_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Tenant Isolation Violation", res.json()["detail"])

    # =========================================================================
    # 4. DOCTOR OWNERSHIP TESTS
    # =========================================================================

    def test_doctor_a_can_manage_own_availability(self):
        """Doctor A successfully configures their own availability schedule."""
        res = self.client.post(
            f"/api/doctors/{self.doc_a.id}/schedules",
            json={"schedules": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00", "is_active": True}]},
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res.status_code, 200)

    def test_doctor_a_cannot_manage_doctor_b_availability(self):
        """Doctor A attempting to modify Doctor B's schedule is rejected with HTTP 403."""
        res = self.client.post(
            f"/api/doctors/{self.doc_b.id}/schedules",
            json={"schedules": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00", "is_active": True}]},
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Doctor Ownership Violation", res.json()["detail"])

    def test_doctor_b_cannot_manage_doctor_a_availability(self):
        """Doctor B attempting to modify Doctor A's schedule is rejected with HTTP 403."""
        res = self.client.post(
            f"/api/doctors/{self.doc_a.id}/schedules",
            json={"schedules": [{"day_of_week": 2, "start_time": "10:00", "end_time": "18:00", "is_active": True}]},
            headers=self._auth_header(self.token_doc_b)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Doctor Ownership Violation", res.json()["detail"])

    def test_doctor_a_can_view_own_queue_but_not_doctor_b_queue(self):
        """Doctor A can view their own appointment queue but is rejected from Doctor B's queue."""
        # Own queue -> 200
        res_own = self.client.get(
            f"/api/appointments/doctor/{self.doc_a.id}/queue",
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res_own.status_code, 200)

        # Other doctor's queue -> 403
        res_other = self.client.get(
            f"/api/appointments/doctor/{self.doc_b.id}/queue",
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res_other.status_code, 403)

    # =========================================================================
    # 5. PATIENT OWNERSHIP TESTS
    # =========================================================================

    def test_patient_a_can_access_own_profile(self):
        """Patient A can read their own profile."""
        res = self.client.get(
            f"/api/patients/{self.pat_a.id}",
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["email"], self.pat_a.email)

    def test_patient_a_cannot_access_patient_b_profile(self):
        """Patient A requesting Patient B's profile via ID spoofing is rejected with HTTP 403."""
        res = self.client.get(
            f"/api/patients/{self.pat_b.id}",
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Patient Ownership Violation", res.json()["detail"])

    def test_patient_a_can_update_own_profile(self):
        """Patient A can update their own permitted profile fields."""
        new_phone = f"+1-555-{uuid.uuid4().hex[:4]}"
        res = self.client.patch(
            f"/api/patients/{self.pat_a.id}",
            json={"phone": new_phone, "communication_preference": "EMAIL"},
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["phone"], new_phone)

    def test_patient_a_cannot_update_patient_b_profile(self):
        """Patient A attempting to update Patient B's profile is rejected with HTTP 403."""
        res = self.client.patch(
            f"/api/patients/{self.pat_b.id}",
            json={"phone": "+1-555-000-9999"},
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Patient Ownership Violation", res.json()["detail"])

    def test_patient_a_can_access_own_appointment(self):
        """Patient A can access their own appointment."""
        res = self.client.get(
            f"/api/appointments/{self.appt_a.id}",
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], self.appt_a.id)

    def test_patient_a_cannot_access_patient_b_appointment(self):
        """Patient A attempting to view Patient B's appointment is rejected with HTTP 403."""
        res = self.client.get(
            f"/api/appointments/{self.appt_b.id}",
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Patient Ownership Violation", res.json()["detail"])

if __name__ == "__main__":
    unittest.main()
