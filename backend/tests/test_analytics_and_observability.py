import unittest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.database import Base, get_db
from backend import models, schemas
from backend.auth.security import create_access_token, hash_password
from backend.auth.roles import UserRole
from backend.audit.privacy import redact_sensitive_data, mask_email, mask_phone, mask_name
from backend.audit.logger import AuditLogger, AuditCategory
from backend.services.observability_service import ObservabilityService, BookingTraceStage, ORDERED_STAGES


class TestAnalyticsAndObservability(unittest.TestCase):
    """
    Comprehensive test suite for Analytics, Observability, and Audit Tracking.
    Verifies:
    1. HIPAA/PHI Sensitive Healthcare Redaction & Masking
    2. End-to-End 9-Stage Distributed Booking Trace Engine
    3. 4-Pillar Operational Metrics (AI, Scheduling, Integration, Workflow)
    4. Audit Tracking across all 8 mandatory categories
    5. API Endpoints & Multi-Tenant RBAC Enforcement
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        cls.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.TestingSessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        # Seed Hospital A & B
        self.hospital_a = models.Hospital(
            id="hosp-obs-a",
            name="St. Jude Memorial Hospital",
            license_number="LIC-OBS-A",
            address="100 Care Ave",
            contact_email="admin@stjude.org",
            phone="+1-555-0100",
            status="APPROVED"
        )
        self.hospital_b = models.Hospital(
            id="hosp-obs-b",
            name="Metro General Hospital",
            license_number="LIC-OBS-B",
            address="200 Metro Blvd",
            contact_email="admin@metro.org",
            phone="+1-555-0200",
            status="APPROVED"
        )
        self.db.add_all([self.hospital_a, self.hospital_b])

        # Seed Doctors
        self.doctor_a = models.Doctor(
            id="doc-obs-a",
            hospital_id=self.hospital_a.id,
            full_name="Dr. Sarah Jenkins",
            specialty="Cardiology",
            email="sarah.jenkins@stjude.org",
            phone="+1-555-0101",
            consultation_fee=150.0,
            is_active=True
        )
        self.db.add(self.doctor_a)

        # Seed Patient
        self.patient = models.Patient(
            id="pat-obs-1",
            full_name="Alice Morgan",
            phone="+1-555-0199",
            email="alice.morgan@example.com",
            primary_hospital_id=self.hospital_a.id
        )
        self.db.add(self.patient)

        # Seed Users
        self.user_platform = models.User(
            id="usr-plat-admin",
            email="platform.admin@aegiscare.io",
            full_name="Platform Admin",
            password_hash=hash_password("PlatformAdmin123!"),
            role=UserRole.PLATFORM_ADMIN.value,
            is_active=True
        )
        self.user_hosp_a = models.User(
            id="usr-hosp-a",
            email="david.miller@stjudehealth.org",
            full_name="David Miller",
            password_hash=hash_password("AdminPass123!"),
            role=UserRole.HOSPITAL_ADMIN.value,
            hospital_id=self.hospital_a.id,
            is_active=True
        )
        self.user_hosp_b = models.User(
            id="usr-hosp-b",
            email="claire.vance@metrogeneral.org",
            full_name="Claire Vance",
            password_hash=hash_password("AdminPass123!"),
            role=UserRole.HOSPITAL_ADMIN.value,
            hospital_id=self.hospital_b.id,
            is_active=True
        )
        self.db.add_all([self.user_platform, self.user_hosp_a, self.user_hosp_b])
        self.db.commit()

        # Auth Tokens
        self.token_platform = create_access_token({
            "sub": self.user_platform.id,
            "role": self.user_platform.role,
            "email": self.user_platform.email
        })
        self.token_hosp_a = create_access_token({
            "sub": self.user_hosp_a.id,
            "role": self.user_hosp_a.role,
            "email": self.user_hosp_a.email,
            "hospital_id": self.hospital_a.id
        })
        self.token_hosp_b = create_access_token({
            "sub": self.user_hosp_b.id,
            "role": self.user_hosp_b.role,
            "email": self.user_hosp_b.email,
            "hospital_id": self.hospital_b.id
        })

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    # =========================================================================
    # 1. HIPAA/PHI SENSITIVE HEALTHCARE REDACTION TESTS
    # =========================================================================

    def test_hipaa_phi_redaction_and_masking(self):
        """Verifies that patient names, phones, emails, and sensitive keys are sanitized."""
        raw_payload = {
            "patient_name": "Alice Morgan",
            "phone": "+1-555-832-1920",
            "email": "alice.morgan@example.com",
            "password": "SuperSecretPassword123!",
            "token": "eyJhbGciOiJIUzI1NiIsIn...",
            "medical_notes": "Patient reports severe chest pain and palpitations",
            "doctor_name": "Dr. Sarah Jenkins",
            "slot_time": "2026-09-25T10:00:00Z"
        }

        sanitized = redact_sensitive_data(raw_payload)

        # 1. Direct identifiers masked
        self.assertEqual(sanitized["patient_name"], "A**** M****")
        self.assertTrue(sanitized["phone"].startswith("+1-***"))
        self.assertTrue(sanitized["email"].startswith("a****@"))

        # 2. Credentials totally redacted
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["token"], "[REDACTED]")

        # 3. Raw medical content masked
        self.assertEqual(sanitized["medical_notes"], "[SENSITIVE_CLINICAL_CONTENT_MASKED]")

        # 4. Safe metadata preserved
        self.assertEqual(sanitized["doctor_name"], "Dr. Sarah Jenkins")
        self.assertEqual(sanitized["slot_time"], "2026-09-25T10:00:00Z")

    # =========================================================================
    # 2. END-TO-END 9-STAGE DISTRIBUTED BOOKING TRACE TESTS
    # =========================================================================

    def test_record_and_query_complete_9_stage_booking_trace(self):
        """Verifies recording and retrieving the full 9-stage booking operation trace."""
        correlation_id = f"CORR-TEST-{uuid.uuid4().hex[:8]}"

        # Record all 9 stages in chronological order
        for stage in ORDERED_STAGES:
            ObservabilityService.record_stage(
                correlation_id=correlation_id,
                stage=stage,
                status="COMPLETED",
                details={"stage_name": stage.value, "step_info": f"Executing {stage.value}"},
                actor_id="TEST_RUNNER",
                actor_role="SYSTEM",
                tenant_id=self.hospital_a.id,
                duration_ms=45,
                db=self.db
            )

        # Retrieve trace
        trace = ObservabilityService.get_booking_trace(correlation_id=correlation_id, db=self.db)

        self.assertEqual(trace["correlation_id"], correlation_id)
        self.assertEqual(trace["overall_status"], "COMPLETED")
        self.assertEqual(trace["completed_stages_count"], 9)
        self.assertEqual(trace["total_stages"], 9)

        timeline = trace["timeline"]
        self.assertEqual(len(timeline), 9)

        stage_names = [t["stage"] for t in timeline]
        expected_names = [s.value for s in ORDERED_STAGES]
        self.assertEqual(stage_names, expected_names)

        # Check that each stage is completed
        for t in timeline:
            self.assertEqual(t["status"], "COMPLETED")
            self.assertIsNotNone(t["timestamp"])

    def test_partial_trace_handling(self):
        """Verifies that an in-flight booking trace correctly reflects incomplete stages."""
        correlation_id = f"CORR-INFLIGHT-{uuid.uuid4().hex[:8]}"

        # Record only stages 1 to 4
        for stage in ORDERED_STAGES[:4]:
            ObservabilityService.record_stage(
                correlation_id=correlation_id,
                stage=stage,
                status="COMPLETED",
                details={"step": stage.value},
                tenant_id=self.hospital_a.id,
                db=self.db
            )

        trace = ObservabilityService.get_booking_trace(correlation_id=correlation_id, db=self.db)
        self.assertEqual(trace["overall_status"], "IN_PROGRESS")
        self.assertEqual(trace["completed_stages_count"], 4)

        timeline = trace["timeline"]
        self.assertEqual(timeline[0]["status"], "COMPLETED")  # CONVERSATION
        self.assertEqual(timeline[3]["status"], "COMPLETED")  # SCHEDULING
        self.assertEqual(timeline[4]["status"], "NOT_STARTED")  # EHR_OPERATION

    # =========================================================================
    # 3. FOUR-PILLAR OPERATIONAL METRICS TESTS
    # =========================================================================

    def test_four_pillar_metrics_aggregation(self):
        """Verifies calculation of real-time metrics across AI, Scheduling, Integration, and Workflow."""
        # 1. Seed an AI Conversation
        conv = models.AIConversation(
            session_id=f"sess-{uuid.uuid4().hex[:6]}",
            patient_id=self.patient.id,
            hospital_id=self.hospital_a.id,
            channel="web_voice",
            status="COMPLETED",
            transcript=[
                {"role": "user", "content": "I need an appointment with cardiology."},
                {"role": "assistant", "content": "Certainly, Dr. Jenkins is available tomorrow at 10 AM."}
            ]
        )
        self.db.add(conv)

        # 2. Seed a Slot and Appointment
        slot = models.TimeSlot(
            doctor_id=self.doctor_a.id,
            start_time=datetime.utcnow() + timedelta(days=1),
            end_time=datetime.utcnow() + timedelta(days=1, minutes=30),
            status="BOOKED"
        )
        self.db.add(slot)
        self.db.commit()

        appt = models.Appointment(
            id=str(uuid.uuid4()),
            hospital_id=self.hospital_a.id,
            doctor_id=self.doctor_a.id,
            patient_id=self.patient.id,
            slot_id=slot.id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status="CONFIRMED",
            idempotency_key=f"IDEMP-{uuid.uuid4().hex[:8]}"
        )
        self.db.add(appt)

        # 3. Seed EHR Sync Log
        sync_log = models.EhrSyncLog(
            appointment_id=appt.id,
            idempotency_key="TEST-IDEMP-METRICS",
            action="CREATE_APPOINTMENT",
            status="SUCCESS",
            response_time_ms=145
        )
        self.db.add(sync_log)

        # 4. Seed Workflow and Notification
        wf = models.Workflow(
            hospital_id=self.hospital_a.id,
            appointment_id=appt.id,
            workflow_type="POST_BOOKING_WORKFLOW",
            status="COMPLETED",
            created_at=datetime.utcnow() - timedelta(seconds=2),
            completed_at=datetime.utcnow()
        )
        notif = models.Notification(
            hospital_id=self.hospital_a.id,
            appointment_id=appt.id,
            recipient_type="PATIENT",
            recipient_contact="+1-555-0199",
            template="APPOINTMENT_CONFIRMATION",
            channel="SMS",
            scheduled_for=datetime.utcnow(),
            status="SENT"
        )
        self.db.add_all([wf, notif])
        self.db.commit()

        # Query metrics
        metrics = ObservabilityService.get_aggregated_metrics(hospital_id=self.hospital_a.id, db=self.db)

        # Verify AI Pillar
        self.assertIn("ai", metrics)
        self.assertGreaterEqual(metrics["ai"]["conversations"]["total"], 1)
        self.assertIn("average_turn_latency_ms", metrics["ai"]["latency"])
        self.assertIn("approximate_cost_usd", metrics["ai"]["approximate_cost"])

        # Verify Scheduling Pillar
        self.assertIn("scheduling", metrics)
        self.assertGreaterEqual(metrics["scheduling"]["booking_success"]["confirmed_appointments"], 1)
        self.assertIn("booked_percentage", metrics["scheduling"]["utilization"])

        # Verify Integration Pillar
        self.assertIn("integration", metrics)
        self.assertGreaterEqual(metrics["integration"]["requests"]["total_outbound_requests"], 1)
        self.assertIn("unknown_outcomes", metrics["integration"])

        # Verify Workflow Pillar
        self.assertIn("workflow", metrics)
        self.assertGreaterEqual(metrics["workflow"]["completed"], 1)
        self.assertGreaterEqual(metrics["workflow"]["notifications"]["sent"], 1)

    # =========================================================================
    # 4. AUDIT TRACKING ACROSS ALL 8 CATEGORIES
    # =========================================================================

    def test_audit_tracking_eight_categories(self):
        """Verifies logging and querying across all 8 mandatory audit categories."""
        cid = f"CORR-AUDIT-{uuid.uuid4().hex[:6]}"

        # 1. LOGIN_ACCESS
        AuditLogger.log_login_access(
            action="USER_LOGIN_SUCCESS",
            actor_id=self.user_platform.id,
            email=self.user_platform.email,
            status="SUCCESS",
            correlation_id=cid,
            db=self.db
        )

        # 2. APPOINTMENT_OPERATIONS
        AuditLogger.log_appointment_operation(
            action="APPOINTMENT_CONFIRMED",
            appointment_id="appt-100",
            actor_id=self.patient.id,
            actor_role="PATIENT",
            tenant_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # 3. PATIENT_DATA_ACCESS
        AuditLogger.log_patient_data_access(
            action="VIEW_PATIENT_PROFILE",
            patient_id=self.patient.id,
            actor_id=self.doctor_a.id,
            actor_role="DOCTOR",
            tenant_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # 4. AI_ACTIONS
        AuditLogger.log_ai_action(
            action="TRIAGE_DECISION_ROUTINE",
            session_id="sess-200",
            actor_id="AI_AGENT",
            correlation_id=cid,
            db=self.db
        )

        # 5. CAPABILITY_EXECUTIONS
        AuditLogger.log_capability_execution(
            capability_name="check_availability",
            actor_id=self.patient.id,
            actor_role="PATIENT",
            tenant_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # 6. INTEGRATION_OPERATIONS
        AuditLogger.log_integration_operation(
            action="EHR_SYNC_CREATE_APPOINTMENT",
            system_name="MOCK_EHR",
            tenant_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # 7. CONFIGURATION_CHANGES
        AuditLogger.log_configuration_change(
            action="UPDATE_DOCTOR_AVAILABILITY",
            actor_id=self.user_hosp_a.id,
            actor_role="HOSPITAL_ADMIN",
            resource_type="AVAILABILITY_SCHEDULE",
            resource_id="sched-1",
            tenant_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # 8. ADMINISTRATIVE_ACTIONS
        AuditLogger.log_administrative_action(
            action="APPROVE_HOSPITAL_APPLICATION",
            actor_id=self.user_platform.id,
            actor_role="PLATFORM_ADMIN",
            resource_type="HOSPITAL",
            resource_id=self.hospital_a.id,
            correlation_id=cid,
            db=self.db
        )

        # Verify all 8 categories exist in DB
        events = self.db.query(models.AuditEvent).filter(models.AuditEvent.correlation_id == cid).all()
        self.assertEqual(len(events), 8)

        logged_categories = set()
        for ev in events:
            cat = (ev.details or {}).get("category") or ev.action.split(":")[0]
            logged_categories.add(cat)

        for required_cat in AuditCategory:
            self.assertIn(required_cat.value, logged_categories)

    # =========================================================================
    # 5. API ENDPOINTS & RBAC / TENANT ISOLATION TESTS
    # =========================================================================

    def test_analytics_metrics_api(self):
        """Platform Admin can query metrics globally; Hospital Admin can query own tenant."""
        # Platform Admin: OK
        res = self.client.get("/api/analytics/metrics", headers={"Authorization": f"Bearer {self.token_platform}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("ai", data)
        self.assertIn("scheduling", data)
        self.assertIn("integration", data)
        self.assertIn("workflow", data)

        # Hospital Admin St. Jude: OK for St. Jude
        res_hosp = self.client.get(
            f"/api/analytics/metrics?hospital_id={self.hospital_a.id}",
            headers={"Authorization": f"Bearer {self.token_hosp_a}"}
        )
        self.assertEqual(res_hosp.status_code, 200)

        # Hospital Admin St. Jude trying to access Metro General: 403 Forbidden
        res_forbidden = self.client.get(
            f"/api/analytics/metrics?hospital_id={self.hospital_b.id}",
            headers={"Authorization": f"Bearer {self.token_hosp_a}"}
        )
        self.assertEqual(res_forbidden.status_code, 403)

    def test_trace_api_and_demo_booking(self):
        """Verifies demo booking trigger and trace inspection via HTTP API."""
        # Trigger demo booking trace
        res = self.client.post(
            "/api/analytics/trace/demo-booking",
            headers={"Authorization": f"Bearer {self.token_platform}"}
        )
        self.assertEqual(res.status_code, 200)
        demo_data = res.json()
        self.assertEqual(demo_data["status"], "TRACE_DEMO_COMPLETED")
        self.assertIn("correlation_id", demo_data)
        cid = demo_data["correlation_id"]

        # Inspect trace by correlation ID
        trace_res = self.client.get(
            f"/api/analytics/trace/{cid}",
            headers={"Authorization": f"Bearer {self.token_platform}"}
        )
        self.assertEqual(trace_res.status_code, 200)
        trace = trace_res.json()
        self.assertEqual(trace["correlation_id"], cid)
        self.assertEqual(len(trace["timeline"]), 9)

        # List recent traces
        traces_list_res = self.client.get(
            "/api/analytics/traces",
            headers={"Authorization": f"Bearer {self.token_platform}"}
        )
        self.assertEqual(traces_list_res.status_code, 200)
        traces_list = traces_list_res.json()["traces"]
        self.assertTrue(any(t["correlation_id"] == cid for t in traces_list))

    def test_audit_logs_api_filtering(self):
        """Verifies filterable audit logs API by category."""
        # Seed a login access log
        AuditLogger.log_login_access(
            action="TEST_FILTER_LOGIN",
            actor_id="usr-test",
            email="test@example.com",
            status="SUCCESS",
            correlation_id="CORR-FILTER-1",
            db=self.db
        )

        res = self.client.get(
            "/api/analytics/audit-logs?category=LOGIN_ACCESS",
            headers={"Authorization": f"Bearer {self.token_platform}"}
        )
        self.assertEqual(res.status_code, 200)
        logs = res.json()
        self.assertIn("events", logs)
        self.assertTrue(len(logs["events"]) >= 1)
        self.assertTrue(all(e["category"] == "LOGIN_ACCESS" for e in logs["events"]))


if __name__ == "__main__":
    unittest.main()
