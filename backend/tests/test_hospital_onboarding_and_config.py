import uuid
import unittest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import SessionLocal
from backend import models

client = TestClient(app)

class TestHospitalOnboardingAndConfig(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

        # Login as Platform Admin
        resp = client.post("/api/auth/login", json={
            "email": "platform.admin@aegiscare.io",
            "password": "PlatformAdmin123!"
        })
        self.assertEqual(resp.status_code, 200)
        self.platform_token = resp.json()["access_token"]
        self.platform_headers = {"Authorization": f"Bearer {self.platform_token}"}

    def tearDown(self):
        self.db.close()

    def test_01_self_service_registration_and_admin_provisioning(self):
        """Verify hospital self-service registration with departments, specialties, and admin user creation."""
        unique_lic = f"LIC-TEST-{uuid.uuid4().hex[:6].upper()}"
        admin_email = f"admin.{uuid.uuid4().hex[:6]}@cedarssinai.org"
        reg_payload = {
            "name": "Cedars Sinai Regional Medical Center",
            "address": "8700 Beverly Blvd, West Hollywood, CA",
            "license_number": unique_lic,
            "contact_email": admin_email,
            "phone": "+1-310-555-0199",
            "departments": ["Emergency Medicine", "Cardiac Surgery", "Sports Medicine"],
            "specialties": ["Cardiology", "Orthopedics", "Pulmonology"],
            "services": ["Trauma Center Level 1", "Robotic Joint Surgery", "Telehealth Urgent Care"],
            "operating_hours": {
                "Monday-Friday": "07:00-20:00",
                "Saturday": "08:00-16:00",
                "Sunday": "Emergency Only"
            },
            "admin_name": "Dr. Brenda Vance",
            "admin_email": admin_email,
            "admin_phone": "+1-310-555-0198",
            "admin_role": "Chief Medical Officer & Hospital Admin",
            "admin_password": "HospitalAdminSecret123!",
            "supported_healthcare_systems": ["EPIC", "MOCK_EHR"],
            "integration_config": {
                "environment": "sandbox",
                "endpoint": "https://epic-sandbox.cedarssinai.org/fhir",
                "timeout_ms": 2500
            },
            "status": "DRAFT"
        }

        resp = client.post("/api/hospitals/register", json=reg_payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        hosp_data = resp.json()
        hosp_id = hosp_data["id"]
        self.assertEqual(hosp_data["status"], "DRAFT")
        self.assertEqual(hosp_data["name"], "Cedars Sinai Regional Medical Center")

        # Verify auto-created departments in DB
        depts = self.db.query(models.Department).filter(models.Department.hospital_id == hosp_id).all()
        dept_names = [d.name for d in depts]
        self.assertIn("Emergency Medicine", dept_names)
        self.assertIn("Cardiac Surgery", dept_names)
        self.assertIn("Sports Medicine", dept_names)

        # Verify auto-created specialties
        for s_name in ["Cardiology", "Orthopedics", "Pulmonology"]:
            spec = self.db.query(models.Specialty).filter(models.Specialty.name == s_name).first()
            self.assertIsNotNone(spec)

        # Verify auto-created Hospital Admin user and login
        login_resp = client.post("/api/auth/login", json={
            "email": admin_email,
            "password": "HospitalAdminSecret123!"
        })
        self.assertEqual(login_resp.status_code, 200, "Auto-created hospital admin must be able to log in.")
        admin_auth = login_resp.json()
        self.assertEqual(admin_auth["role"], "HOSPITAL_ADMIN")
        self.assertEqual(admin_auth["hospital_id"], hosp_id)

    def test_02_hospital_lifecycle_state_machine(self):
        """Verify full lifecycle: Draft -> Submitted -> Under Review (Corrections) -> Approved -> Suspended -> Reactivated -> Rejected."""
        unique_lic = f"LIC-LIFE-{uuid.uuid4().hex[:6].upper()}"
        reg_payload = {
            "name": "St. Luke's Community Hospital",
            "address": "100 Health Way",
            "license_number": unique_lic,
            "contact_email": f"contact.{uuid.uuid4().hex[:6]}@stlukes.org",
            "phone": "+1-555-900-1122",
            "status": "DRAFT"
        }
        create_res = client.post("/api/hospitals/register", json=reg_payload)
        self.assertEqual(create_res.status_code, 200)
        hosp_id = create_res.json()["id"]
        self.assertEqual(create_res.json()["status"], "DRAFT")

        # 1. Submit Application
        sub_res = client.post(f"/api/hospitals/{hosp_id}/submit")
        self.assertEqual(sub_res.status_code, 200)
        self.assertEqual(sub_res.json()["status"], "SUBMITTED")

        # 2. Platform Admin requests corrections -> UNDER_REVIEW
        corr_res = client.post(
            f"/api/admin/hospitals/{hosp_id}/request-corrections",
            headers=self.platform_headers,
            json={"correction_notes": "Please verify fire safety certification and DEA registration."}
        )
        self.assertEqual(corr_res.status_code, 200)
        self.assertEqual(corr_res.json()["status"], "UNDER_REVIEW")
        self.assertIn("fire safety", corr_res.json()["correction_notes"])

        # 3. Platform Admin approves hospital -> APPROVED
        app_res = client.post(f"/api/admin/hospitals/{hosp_id}/approve", headers=self.platform_headers)
        self.assertEqual(app_res.status_code, 200)
        self.assertEqual(app_res.json()["status"], "APPROVED")

        # 4. Platform Admin suspends hospital -> SUSPENDED
        susp_res = client.post(
            f"/api/admin/hospitals/{hosp_id}/suspend",
            headers=self.platform_headers,
            json={"reason": "Routine clinical audit review pending."}
        )
        self.assertEqual(susp_res.status_code, 200)
        self.assertEqual(susp_res.json()["status"], "SUSPENDED")
        self.assertEqual(susp_res.json()["rejection_reason"], "Routine clinical audit review pending.")

        # 5. Platform Admin reactivates hospital -> APPROVED
        react_res = client.post(f"/api/admin/hospitals/{hosp_id}/reactivate", headers=self.platform_headers)
        self.assertEqual(react_res.status_code, 200)
        self.assertEqual(react_res.json()["status"], "APPROVED")

        # 6. Verify Platform Admin review list reflects hospital
        list_res = client.get("/api/admin/hospitals/applications?status=APPROVED", headers=self.platform_headers)
        self.assertEqual(list_res.status_code, 200)
        ids = [h["id"] for h in list_res.json()]
        self.assertIn(hosp_id, ids)

        # 7. Reject hospital
        rej_res = client.post(
            f"/api/admin/hospitals/{hosp_id}/reject",
            headers=self.platform_headers,
            json={"status": "REJECTED", "rejection_reason": "Failed regulatory safety standards."}
        )
        self.assertEqual(rej_res.status_code, 200)
        self.assertEqual(rej_res.json()["status"], "REJECTED")

        # Verify lifecycle history audit trail
        history_check = client.get(f"/api/hospitals/{hosp_id}")
        self.assertEqual(history_check.status_code, 200)
        history = history_check.json().get("lifecycle_history", [])
        self.assertGreaterEqual(len(history), 6)

    def test_03_guardrails_unapproved_hospital_restrictions(self):
        """Only approved hospitals may create active doctors, publish availability, and receive appointments."""
        unique_lic = f"LIC-GUARD-{uuid.uuid4().hex[:6].upper()}"
        create_res = client.post("/api/hospitals/register", json={
            "name": "Draft Clinic Network",
            "address": "400 Unapproved St",
            "license_number": unique_lic,
            "contact_email": "draft@clinic.org",
            "phone": "+1-555-444-1111",
            "status": "DRAFT"
        })
        self.assertEqual(create_res.status_code, 200)
        hosp_id = create_res.json()["id"]

        # 1. Attempt to create doctor in unapproved hospital -> Expect 400
        doc_create_res = client.post(
            f"/api/doctors/hospital/{hosp_id}",
            headers=self.platform_headers,
            json={
                "full_name": "Dr. Unapproved Doctor",
                "specialty": "Cardiology"
            }
        )
        self.assertEqual(doc_create_res.status_code, 400)
        self.assertIn("unapproved hospital", doc_create_res.text.lower())

        # 2. Attempt to configure production integrations on unapproved hospital -> Expect 400
        integ_res = client.put(
            f"/api/hospitals/{hosp_id}/integration",
            headers=self.platform_headers,
            json={
                "integration_config": {"endpoint": "https://live.ehr.org"}
            }
        )
        self.assertEqual(integ_res.status_code, 400)
        self.assertIn("unapproved hospital", integ_res.text.lower())

        # 3. Even if a doctor record was manually inserted into an unapproved hospital:
        test_doc = models.Doctor(
            hospital_id=hosp_id,
            full_name="Dr. Force Inserted",
            specialty="Cardiology",
            status="ACTIVE",
            is_active=True
        )
        self.db.add(test_doc)
        self.db.commit()
        self.db.refresh(test_doc)

        # Generating slots must be rejected
        slot_res = client.post(
            f"/api/doctors/{test_doc.id}/schedules",
            headers=self.platform_headers,
            json={"schedules": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00", "is_active": True}]}
        )
        self.assertEqual(slot_res.status_code, 400)
        self.assertIn("unapproved hospital", slot_res.text.lower())

        # Booking validation must reject receiving appointments
        now = datetime.utcnow() + timedelta(days=2)
        from backend.scheduling.service import SchedulingService
        val = SchedulingService.validate_slot(
            db=self.db,
            doctor_id=test_doc.id,
            start_time=now,
            end_time=now + timedelta(minutes=30)
        )
        self.assertFalse(val.is_bookable)
        self.assertIn("approved", val.reason.lower())

    def test_04_tenant_isolation_cross_hospital_access(self):
        """Hospital administrators can only manage their own hospital. Cross-hospital access is rejected with 403."""
        # Create Hospital A with Admin A
        admin_a_email = f"admin.a.{uuid.uuid4().hex[:6]}@hospa.org"
        hosp_a_res = client.post("/api/hospitals/register", json={
            "name": "Hospital Alpha",
            "address": "100 Alpha Blvd",
            "license_number": f"LIC-A-{uuid.uuid4().hex[:6].upper()}",
            "contact_email": admin_a_email,
            "phone": "+1-555-111-0001",
            "admin_name": "Admin Alpha",
            "admin_email": admin_a_email,
            "admin_password": "AlphaPassword123!",
            "status": "APPROVED"
        })
        hosp_a_id = hosp_a_res.json()["id"]

        # Create Hospital B with Admin B
        admin_b_email = f"admin.b.{uuid.uuid4().hex[:6]}@hospb.org"
        hosp_b_res = client.post("/api/hospitals/register", json={
            "name": "Hospital Beta",
            "address": "200 Beta Blvd",
            "license_number": f"LIC-B-{uuid.uuid4().hex[:6].upper()}",
            "contact_email": admin_b_email,
            "phone": "+1-555-222-0002",
            "admin_name": "Admin Beta",
            "admin_email": admin_b_email,
            "admin_password": "BetaPassword123!",
            "status": "APPROVED"
        })
        hosp_b_id = hosp_b_res.json()["id"]

        # Ensure both are approved
        client.post(f"/api/admin/hospitals/{hosp_a_id}/approve", headers=self.platform_headers)
        client.post(f"/api/admin/hospitals/{hosp_b_id}/approve", headers=self.platform_headers)

        # Login as Admin A
        auth_a = client.post("/api/auth/login", json={"email": admin_a_email, "password": "AlphaPassword123!"}).json()
        headers_a = {"Authorization": f"Bearer {auth_a['access_token']}"}

        # 1. Admin A attempts to read Hospital B private profile -> Expect 403
        resp = client.get(f"/api/hospitals/{hosp_b_id}", headers=headers_a)
        self.assertEqual(resp.status_code, 403)

        # 2. Admin A attempts to update Hospital B profile -> Expect 403
        resp = client.patch(f"/api/hospitals/{hosp_b_id}", headers=headers_a, json={"name": "Hacked Name"})
        self.assertEqual(resp.status_code, 403)

        # 3. Admin A attempts to create doctor in Hospital B -> Expect 403
        resp = client.post(
            f"/api/doctors/hospital/{hosp_b_id}",
            headers=headers_a,
            json={"full_name": "Dr. Rogue", "specialty": "Dermatology"}
        )
        self.assertEqual(resp.status_code, 403)

        # 4. Admin A attempts to view Hospital B activity -> Expect 403
        resp = client.get(f"/api/hospitals/{hosp_b_id}/activity", headers=headers_a)
        self.assertEqual(resp.status_code, 403)

        # 5. Admin A reading own hospital -> Expect 200
        resp = client.get(f"/api/hospitals/{hosp_a_id}", headers=headers_a)
        self.assertEqual(resp.status_code, 200)

    def test_05_doctor_configuration_and_lifecycle(self):
        """Verify full doctor attributes and lifecycle: Invited -> Active -> Inactive / Suspended."""
        # Use existing approved hospital
        hosp = self.db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").first()
        self.assertIsNotNone(hosp)

        # Create doctor with full attributes
        doc_payload = {
            "full_name": "Dr. Alexander Wright, MD",
            "specialty": "Neurology",
            "photo_url": "https://images.unsplash.com/photo-wright.jpg",
            "qualifications": "MD, PhD in Neurobiology, Board Certified Neurologist",
            "experience_years": 15,
            "languages": "English, French",
            "bio": "Specialist in neurodegenerative movement disorders and cognitive preservation.",
            "consultation_fee": 225.0,
            "slot_duration_min": 45,
            "supported_appointment_types": "IN_PERSON,VIDEO_CONSULT,URGENT",
            "external_provider_id": "NPI-7718902",
            "status": "ACTIVE"
        }
        res = client.post(f"/api/doctors/hospital/{hosp.id}", headers=self.platform_headers, json=doc_payload)
        self.assertEqual(res.status_code, 200)
        doc = res.json()
        doc_id = doc["id"]
        self.assertEqual(doc["status"], "ACTIVE")
        self.assertEqual(doc["consultation_fee"], 225.0)
        self.assertEqual(doc["slot_duration_min"], 45)
        self.assertEqual(doc["external_provider_id"], "NPI-7718902")

        # Update doctor credentials
        patch_res = client.patch(
            f"/api/doctors/{doc_id}",
            headers=self.platform_headers,
            json={"consultation_fee": 250.0, "languages": "English, French, German"}
        )
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.json()["consultation_fee"], 250.0)
        self.assertEqual(patch_res.json()["languages"], "English, French, German")

        # Lifecycle: Transition to INACTIVE
        stat_res = client.patch(f"/api/doctors/{doc_id}/status", headers=self.platform_headers, json={"status": "INACTIVE"})
        self.assertEqual(stat_res.status_code, 200)
        self.assertEqual(stat_res.json()["status"], "INACTIVE")
        self.assertFalse(stat_res.json()["is_active"])

        # Guardrail: Inactive doctor cannot receive appointments
        future_slot = datetime.utcnow() + timedelta(days=3)
        from backend.scheduling.service import SchedulingService
        val = SchedulingService.validate_slot(
            db=self.db,
            doctor_id=doc_id,
            start_time=future_slot,
            end_time=future_slot + timedelta(minutes=45)
        )
        self.assertFalse(val.is_bookable)
        self.assertIn("inactive", val.reason.lower())

        # Reactivate doctor
        stat_res2 = client.patch(f"/api/doctors/{doc_id}/status", headers=self.platform_headers, json={"status": "ACTIVE"})
        self.assertEqual(stat_res2.status_code, 200)
        self.assertEqual(stat_res2.json()["status"], "ACTIVE")
        self.assertTrue(stat_res2.json()["is_active"])

    def test_06_doctor_ownership_schedules_and_blocked_slots(self):
        """Doctors can manage their own calendar, availability schedules, and blocked slots; cross-doctor access is blocked."""
        # Setup doctor user
        hosp = self.db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").first()
        doc_email = f"doc.{uuid.uuid4().hex[:6]}@stjudehealth.org"

        # Create doctor via API
        doc_res = client.post(f"/api/doctors/hospital/{hosp.id}", headers=self.platform_headers, json={
            "full_name": "Dr. Sarah Jenkins",
            "specialty": "Orthopedics"
        })
        doc_id = doc_res.json()["id"]

        # Link doctor to user
        from backend.auth.security import hash_password
        doc_user = models.User(
            email=doc_email,
            full_name="Dr. Sarah Jenkins",
            role="DOCTOR",
            password_hash=hash_password("DoctorPass123!"),
            hospital_id=hosp.id,
            is_active=True
        )
        self.db.add(doc_user)
        self.db.commit()
        self.db.refresh(doc_user)

        doc_record = self.db.query(models.Doctor).filter(models.Doctor.id == doc_id).first()
        doc_record.user_id = doc_user.id
        self.db.commit()

        # Login as Doctor
        doc_auth = client.post("/api/auth/login", json={"email": doc_email, "password": "DoctorPass123!"}).json()
        doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

        # 1. Doctor sets own schedule (cover all days 0-6 so block_start is on a working day)
        sched_res = client.post(
            f"/api/doctors/{doc_id}/schedules",
            headers=doc_headers,
            json={"schedules": [
                {"day_of_week": d, "start_time": "08:00", "end_time": "20:00", "is_active": True}
                for d in range(7)
            ]}
        )
        self.assertEqual(sched_res.status_code, 200)

        # 2. Doctor blocks surgical window
        block_start = (datetime.utcnow() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        block_end = block_start + timedelta(hours=3)
        block_res = client.post(
            f"/api/doctors/{doc_id}/blocked-slots",
            headers=doc_headers,
            json={
                "start_time": block_start.isoformat(),
                "end_time": block_end.isoformat(),
                "reason": "Emergency Knee Replacement Surgery"
            }
        )
        self.assertEqual(block_res.status_code, 200)
        block_id = block_res.json()["id"]

        # 3. Verify blocked slot prevents appointment booking
        from backend.scheduling.service import SchedulingService
        val = SchedulingService.validate_slot(
            db=self.db,
            doctor_id=doc_id,
            start_time=block_start + timedelta(minutes=30),
            end_time=block_start + timedelta(minutes=60)
        )
        self.assertFalse(val.is_bookable)
        self.assertIn("blocked", val.reason.lower())

        # 4. Doctor deletes blocked slot
        del_res = client.delete(f"/api/doctors/{doc_id}/blocked-slots/{block_id}", headers=doc_headers)
        self.assertEqual(del_res.status_code, 200)

    def test_07_hospital_admin_resource_configuration(self):
        """Hospital Admin manages departments, calendars, communication preferences, and views activity."""
        hosp = self.db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").first()
        admin_email = f"admin.cfg.{uuid.uuid4().hex[:6]}@hosp.org"

        # Create admin user
        from backend.auth.security import hash_password
        admin_user = models.User(
            email=admin_email,
            full_name="Config Admin",
            role="HOSPITAL_ADMIN",
            password_hash=hash_password("AdminPass123!"),
            hospital_id=hosp.id,
            is_active=True
        )
        self.db.add(admin_user)
        self.db.commit()

        admin_auth = client.post("/api/auth/login", json={"email": admin_email, "password": "AdminPass123!"}).json()
        headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

        # 1. Manage Department
        dept_res = client.post(
            f"/api/hospitals/{hosp.id}/departments",
            headers=headers,
            json={"name": "Pediatric Cardiology", "description": "Pediatric heart clinic"}
        )
        self.assertEqual(dept_res.status_code, 200)
        dept_id = dept_res.json()["id"]

        update_dept = client.patch(
            f"/api/hospitals/{hosp.id}/departments/{dept_id}",
            headers=headers,
            json={"description": "Updated pediatric heart clinic"}
        )
        self.assertEqual(update_dept.status_code, 200)

        # 2. Update Communication Preferences
        pref_res = client.put(
            f"/api/hospitals/{hosp.id}/communication-preferences",
            headers=headers,
            json={"preferred_channels": ["SMS", "WHATSAPP", "EMAIL"], "reminder_window_hours": 48}
        )
        self.assertEqual(pref_res.status_code, 200)

        # 3. View Hospital Activity Metrics
        act_res = client.get(f"/api/hospitals/{hosp.id}/activity", headers=headers)
        self.assertEqual(act_res.status_code, 200)
        act_data = act_res.json()
        self.assertIn("total_doctors", act_data)
        self.assertIn("total_departments", act_data)
        self.assertIn("total_appointments", act_data)

if __name__ == "__main__":
    unittest.main()
