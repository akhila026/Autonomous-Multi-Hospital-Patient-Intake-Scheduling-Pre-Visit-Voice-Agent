import unittest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal
from backend import models
from backend.auth.security import hash_password, create_access_token
from backend.auth.roles import UserRole

class TestRoleSpecificDashboards(unittest.TestCase):
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

        # Ensure Patient B exists for tenant and ownership testing
        patient_b_email = f"patient.dash.b.{uuid.uuid4().hex[:6]}@example.com"
        cls.pat_b_user = models.User(
            email=patient_b_email,
            full_name="Patient Bob Dashboard",
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
            full_name="Patient Bob Dashboard",
            email=patient_b_email,
            phone="+1-555-888-7711"
        )
        cls.db.add(cls.pat_b)
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
    # 1. PERSONAS ENDPOINT
    # =========================================================================

    def test_get_personas_discovery(self):
        """Public endpoint returns seeded personas for 1-click persona switching."""
        res = self.client.get("/api/dashboard/personas")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("personas", data)
        personas = data["personas"]
        self.assertGreaterEqual(len(personas), 4)
        roles = [p["role"] for p in personas]
        self.assertIn("PLATFORM_ADMIN", roles)
        self.assertIn("HOSPITAL_ADMIN", roles)
        self.assertIn("DOCTOR", roles)
        self.assertIn("PATIENT", roles)

    # =========================================================================
    # 2. PLATFORM ADMIN DASHBOARD (12 SECTIONS)
    # =========================================================================

    def test_platform_admin_dashboard_authorized_12_sections(self):
        """Platform Admin successfully retrieves all 12 global platform sections."""
        res = self.client.get("/api/dashboard/platform-admin", headers=self._auth_header(self.token_p_admin))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Verify all 12 required sections are present
        self.assertIn("hospital_applications", data)
        self.assertIn("hospitals", data)
        self.assertIn("doctors", data)
        self.assertIn("patients", data)
        self.assertIn("appointments", data)
        self.assertIn("ai_activity", data)
        self.assertIn("integration_activity", data)
        self.assertIn("workflows", data)
        self.assertIn("analytics", data)
        self.assertIn("ai_evaluation", data)
        self.assertIn("operational_health", data)
        self.assertIn("audit_logs", data)

        # Spot-check structure
        self.assertIsInstance(data["hospitals"], list)
        self.assertIn("total", data["appointments"])
        self.assertIn("diagnostic_hallucination_count", data["ai_evaluation"])
        self.assertIn("database", data["operational_health"])

    def test_platform_admin_dashboard_unauthorized_for_other_roles(self):
        """Hospital admin, doctor, patient, and unauthenticated clients receive 401/403."""
        # Unauthenticated -> 401
        res_no_auth = self.client.get("/api/dashboard/platform-admin")
        self.assertEqual(res_no_auth.status_code, 401)

        # Hospital Admin -> 403 Forbidden
        res_hosp_admin = self.client.get("/api/dashboard/platform-admin", headers=self._auth_header(self.token_admin_a))
        self.assertEqual(res_hosp_admin.status_code, 403)

        # Doctor -> 403 Forbidden
        res_doc = self.client.get("/api/dashboard/platform-admin", headers=self._auth_header(self.token_doc_a))
        self.assertEqual(res_doc.status_code, 403)

        # Patient -> 403 Forbidden
        res_pat = self.client.get("/api/dashboard/platform-admin", headers=self._auth_header(self.token_pat_a))
        self.assertEqual(res_pat.status_code, 403)

    # =========================================================================
    # 3. HOSPITAL ADMIN DASHBOARD (12 SECTIONS & TENANT ISOLATION)
    # =========================================================================

    def test_hospital_admin_dashboard_authorized_12_sections(self):
        """Hospital Admin retrieves all 12 tenant-scoped sections for their hospital."""
        res = self.client.get("/api/dashboard/hospital-admin", headers=self._auth_header(self.token_admin_a))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Verify all 12 required sections
        self.assertIn("hospital_overview", data)
        self.assertIn("appointments", data)
        self.assertIn("doctors", data)
        self.assertIn("calendars", data)
        self.assertIn("availability", data)
        self.assertIn("questionnaires", data)
        self.assertIn("ai_activity", data)
        self.assertIn("integration_activity", data)
        self.assertIn("workflows", data)
        self.assertIn("analytics", data)
        self.assertIn("integrations", data)
        self.assertIn("staff_access_management", data)

        # Verify tenant scoping
        self.assertEqual(data["hospital_overview"]["id"], self.hosp_a.id)
        # All returned doctors must belong to Hospital A
        for d in data["doctors"]:
            self.assertEqual(d["hospital_id"], self.hosp_a.id)

    def test_hospital_admin_cross_tenant_access_blocked(self):
        """Hospital Admin A cannot access Hospital B's dashboard (HTTP 403 Forbidden)."""
        res = self.client.get(
            f"/api/dashboard/hospital-admin?hospital_id={self.hosp_b.id}",
            headers=self._auth_header(self.token_admin_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Tenant Isolation Violation", res.json()["detail"])

    def test_hospital_admin_dashboard_unauthorized_for_doctors_and_patients(self):
        """Doctors and patients receive 403 Forbidden on Hospital Admin dashboard."""
        res_doc = self.client.get("/api/dashboard/hospital-admin", headers=self._auth_header(self.token_doc_a))
        self.assertEqual(res_doc.status_code, 403)

        res_pat = self.client.get("/api/dashboard/hospital-admin", headers=self._auth_header(self.token_pat_a))
        self.assertEqual(res_pat.status_code, 403)

    # =========================================================================
    # 4. DOCTOR DASHBOARD (8 SECTIONS & OWNERSHIP)
    # =========================================================================

    def test_doctor_dashboard_authorized_8_sections(self):
        """Doctor retrieves all 8 clinical sections for their own practice."""
        res = self.client.get("/api/dashboard/doctor", headers=self._auth_header(self.token_doc_a))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Verify all 8 required sections
        self.assertIn("todays_appointments", data)
        self.assertIn("upcoming_appointments", data)
        self.assertIn("calendar", data)
        self.assertIn("availability", data)
        self.assertIn("blocked_time", data)
        self.assertIn("appointment_details", data)
        self.assertIn("questionnaires", data)
        self.assertIn("authorized_pre_visit_responses", data)

        self.assertEqual(data["doctor_profile"]["id"], self.doc_a.id)

    def test_doctor_cross_ownership_access_blocked(self):
        """Doctor A cannot access Doctor B's dashboard (HTTP 403 Forbidden)."""
        res = self.client.get(
            f"/api/dashboard/doctor?doctor_id={self.doc_b.id}",
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Doctor Ownership Violation", res.json()["detail"])

    def test_doctor_blocked_time_management(self):
        """Doctor can create and unblock personal blocked time."""
        start = datetime.utcnow() + timedelta(days=2)
        end = start + timedelta(hours=2)

        # 1. Doctor A blocks a slot
        res_block = self.client.post(
            "/api/dashboard/doctor/block-slot",
            headers=self._auth_header(self.token_doc_a),
            json={
                "doctor_id": self.doc_a.id,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "reason": "Surgical Grand Rounds"
            }
        )
        self.assertEqual(res_block.status_code, 200)
        blocked_slot = res_block.json()
        self.assertEqual(blocked_slot["status"], "BLOCKED")
        self.assertEqual(blocked_slot["blocked_reason"], "Surgical Grand Rounds")
        slot_id = blocked_slot["id"]

        # 2. Doctor B tries to delete Doctor A's blocked slot -> 403 Forbidden
        res_del_b = self.client.delete(
            f"/api/dashboard/doctor/unblock-slot/{slot_id}",
            headers=self._auth_header(self.token_doc_b)
        )
        self.assertEqual(res_del_b.status_code, 403)

        # 3. Doctor A unblocks their slot -> 200 OK
        res_del_a = self.client.delete(
            f"/api/dashboard/doctor/unblock-slot/{slot_id}",
            headers=self._auth_header(self.token_doc_a)
        )
        self.assertEqual(res_del_a.status_code, 200)

    # =========================================================================
    # 5. PATIENT DASHBOARD (7 SECTIONS, OWNERSHIP & PREFERENCES)
    # =========================================================================

    def test_patient_dashboard_authorized_7_sections(self):
        """Patient retrieves all 7 patient-portal sections."""
        res = self.client.get("/api/dashboard/patient", headers=self._auth_header(self.token_pat_a))
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Verify all 7 required sections
        self.assertIn("home", data)
        self.assertIn("ai_assistant", data)
        self.assertIn("upcoming_appointments", data)
        self.assertIn("historical_appointments", data)
        self.assertIn("questionnaires", data)
        self.assertIn("preferences", data)
        self.assertIn("profile_management", data)

        self.assertEqual(data["profile_management"]["id"], self.pat_a.id)

    def test_patient_cross_ownership_access_blocked(self):
        """Patient A cannot access Patient B's dashboard (HTTP 403 Forbidden)."""
        res = self.client.get(
            f"/api/dashboard/patient?patient_id={self.pat_b.id}",
            headers=self._auth_header(self.token_pat_a)
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Patient Ownership Violation", res.json()["detail"])

    def test_patient_update_preferences(self):
        """Patient can update communication preference and contact details."""
        res = self.client.put(
            "/api/dashboard/patient/preferences",
            headers=self._auth_header(self.token_pat_a),
            json={
                "communication_preference": "WHATSAPP",
                "phone": "+1-555-909-1234",
                "emergency_contact": "Bob Morgan (+1-555-909-5678)",
                "date_of_birth": "1992-05-14"
            }
        )
        self.assertEqual(res.status_code, 200)
        updated = res.json()
        self.assertEqual(updated["communication_preference"], "WHATSAPP")
        self.assertEqual(updated["emergency_contact"], "Bob Morgan (+1-555-909-5678)")

if __name__ == "__main__":
    unittest.main()
