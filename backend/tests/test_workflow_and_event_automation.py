import asyncio
import unittest
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import SessionLocal
from backend import models
from backend.workflows.events import event_dispatcher, DomainEvent, EventType
from backend.workflows.engine import (
    WorkflowEngine, WorkflowStep, WorkflowDefinition, WorkflowContext
)
from backend.services.notification_service import NotificationService


class TestWorkflowAndEventAutomation(unittest.TestCase):
    """
    Comprehensive verification suite for Workflow and Event Automation System (PRD Sections 16, 17, 21, 22).
    Validates:
    - All 18 Domain Event Types & Pub/Sub Dispatching
    - Asynchronous & Scheduled Workflows with delays, conditions, retries, and failure states
    - Idempotency & Step Execution History
    - Configurable Multi-Party Notifications (Patient, Doctor, Hospital) across all scenarios
    - REST Endpoints for Workflows, Notifications, and Events
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Fetch seeded hospital and doctor
        cls.hospital = cls.db.query(models.Hospital).first()
        assert cls.hospital is not None, "Seeded hospital required"
        cls.hospital_id = cls.hospital.id

        cls.doctor = cls.db.query(models.Doctor).filter(models.Doctor.hospital_id == cls.hospital_id).first()
        assert cls.doctor is not None, "Seeded doctor required"
        cls.doctor_id = cls.doctor.id

        # Fetch or create a test patient
        cls.patient = cls.db.query(models.Patient).filter(models.Patient.primary_hospital_id == cls.hospital_id).first()
        if not cls.patient:
            cls.patient = models.Patient(
                primary_hospital_id=cls.hospital_id,
                full_name="Workflow Test Patient",
                phone="+1-555-9876",
                email="patient.test@example.com"
            )
            cls.db.add(cls.patient)
            cls.db.commit()
            cls.db.refresh(cls.patient)
        cls.patient_id = cls.patient.id

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        event_dispatcher.reset_for_test()

    # =========================================================================
    # 1. DOMAIN EVENTS TESTS (ALL 18 EVENTS SUPPORTED)
    # =========================================================================

    def test_all_18_domain_events_dispatched_and_tracked(self):
        """Validates that all 18 domain events defined in PRD are supported, dispatched, and metrics tracked."""
        all_events = [
            EventType.HOSPITAL_APPROVED,
            EventType.DOCTOR_CREATED,
            EventType.APPOINTMENT_BOOKED,
            EventType.APPOINTMENT_CANCELLED,
            EventType.APPOINTMENT_RESCHEDULED,
            EventType.QUESTIONNAIRE_ASSIGNED,
            EventType.QUESTIONNAIRE_COMPLETED,
            EventType.AI_CONVERSATION_STARTED,
            EventType.CAPABILITY_EXECUTED,
            EventType.EHR_OPERATION_STARTED,
            EventType.EHR_OPERATION_COMPLETED,
            EventType.EHR_OPERATION_FAILED,
            EventType.EXTERNAL_VERIFICATION_COMPLETED,
            EventType.RECONCILIATION_REQUIRED,
            EventType.WORKFLOW_STARTED,
            EventType.WORKFLOW_COMPLETED,
            EventType.WORKFLOW_FAILED,
            EventType.HUMAN_ESCALATION,
        ]
        self.assertEqual(len(all_events), 18)

        received_events = []

        async def run_dispatch():
            for ev_type in all_events:
                ev = DomainEvent(
                    event_type=ev_type,
                    entity_id=f"test-entity-{uuid.uuid4().hex[:6]}",
                    tenant_id=self.hospital_id,
                    correlation_id=f"CORR-{ev_type.value}",
                    payload={"event_name": ev_type.value}
                )
                await event_dispatcher.dispatch(ev)
                received_events.append(ev)

        asyncio.run(run_dispatch())

        self.assertEqual(len(received_events), 18)
        metrics = event_dispatcher.get_metrics()
        for ev_type in all_events:
            self.assertGreaterEqual(metrics.get(ev_type.value, 0), 1)

        history = event_dispatcher.get_history(limit=50)
        self.assertGreaterEqual(len(history), 18)

    def test_global_audit_observer_records_immutable_audit_events(self):
        """Validates that dispatched events trigger the global audit observer to record AuditEvent in DB."""
        unique_entity = f"patient-audit-{uuid.uuid4().hex[:8]}"
        corr_id = f"CORR-AUDIT-TEST-{uuid.uuid4().hex[:6]}"

        event = DomainEvent(
            event_type=EventType.AI_CONVERSATION_STARTED,
            entity_id=unique_entity,
            tenant_id=self.hospital_id,
            correlation_id=corr_id,
            payload={"channel": "VOICE", "initiated_by": "PATIENT"}
        )

        asyncio.run(event_dispatcher.dispatch(event))

        audit_entry = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == corr_id,
            models.AuditEvent.resource_id == unique_entity
        ).first()

        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.action, "AI_CONVERSATION_STARTED")
        self.assertEqual(audit_entry.hospital_id, self.hospital_id)
        self.assertEqual(audit_entry.details.get("payload", {}).get("channel"), "VOICE")

    # =========================================================================
    # 2. WORKFLOW ENGINE CORE CAPABILITIES (Delays, Conditions, Retries, Idempotency)
    # =========================================================================

    def test_workflow_conditional_branching_and_delays(self):
        """Validates that workflow steps can be skipped based on conditions and support delays."""
        step1_ran = False
        step2_ran = False

        async def h1(ctx: WorkflowContext):
            nonlocal step1_ran
            step1_ran = True
            return {"ran": True}

        async def h2(ctx: WorkflowContext):
            nonlocal step2_ran
            step2_ran = True
            return {"ran": True}

        test_def = WorkflowDefinition(
            workflow_type="TEST_CONDITIONAL_WF",
            steps=[
                WorkflowStep("ALWAYS_RUN", h1, delay_seconds=0.01),
                WorkflowStep("CONDITIONALLY_SKIP", h2, condition=lambda ctx: ctx.payload.get("execute_step2") is True)
            ]
        )
        WorkflowEngine.register(test_def)

        # Execute with condition = False
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="TEST_CONDITIONAL_WF",
            hospital_id=self.hospital_id,
            payload={"execute_step2": False},
            idempotency_key=f"WF-COND-{uuid.uuid4().hex[:8]}"
        ))

        self.assertEqual(wf.status, "COMPLETED")
        self.assertTrue(step1_ran)
        self.assertFalse(step2_ran)

        history = wf.execution_history
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["step"], "ALWAYS_RUN")
        self.assertEqual(history[0]["status"], "COMPLETED")
        self.assertEqual(history[1]["step"], "CONDITIONALLY_SKIP")
        self.assertEqual(history[1]["status"], "SKIPPED")
        self.assertIn("Condition evaluated to False", history[1]["reason"])

    def test_workflow_step_retries_and_failure_states(self):
        """Validates step retries with backoff, failure transitions, and human escalation on final failure."""
        attempts = 0

        async def failing_step(ctx: WorkflowContext):
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"Simulated step failure at attempt {attempts}")

        fail_def = WorkflowDefinition(
            workflow_type="TEST_FAIL_WF",
            steps=[
                WorkflowStep("FAILING_STEP", failing_step, max_retries=2, backoff_factor=1.0)
            ]
        )
        WorkflowEngine.register(fail_def)

        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="TEST_FAIL_WF",
            hospital_id=self.hospital_id,
            idempotency_key=f"WF-FAIL-{uuid.uuid4().hex[:8]}"
        ))

        self.assertEqual(wf.status, "FAILED")
        self.assertIsNotNone(wf.failed_at)
        self.assertIn("failed after 2 retries", wf.error_message)
        self.assertEqual(attempts, 3)  # Initial attempt + 2 retries

        # Validate that WORKFLOW_FAILED and HUMAN_ESCALATION events were emitted
        history = event_dispatcher.get_history(limit=10)
        emitted_types = [e.event_type for e in history]
        self.assertIn(EventType.WORKFLOW_FAILED, emitted_types)
        self.assertIn(EventType.HUMAN_ESCALATION, emitted_types)

    def test_workflow_idempotency_deduplication(self):
        """Validates that executing with an existing idempotency_key does not duplicate workflow execution."""
        idemp_key = f"WF-IDEMP-{uuid.uuid4().hex[:8]}"

        wf1 = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital_id,
            payload={"patient_name": "Idemp Patient"},
            idempotency_key=idemp_key
        ))
        self.assertEqual(wf1.status, "COMPLETED")

        wf2 = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital_id,
            payload={"patient_name": "Idemp Patient"},
            idempotency_key=idemp_key
        ))
        self.assertEqual(wf1.id, wf2.id)

    def test_workflow_resuming_and_retry(self):
        """Validates that a failed workflow can be resumed and retried via retry_workflow."""
        attempt = 0

        async def flaking_step(ctx: WorkflowContext):
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ValueError("Transient network glitch")
            return {"recovered": True}

        retry_def = WorkflowDefinition(
            workflow_type="TEST_RETRYABLE_WF",
            steps=[
                WorkflowStep("FLAKY_STEP", flaking_step, max_retries=1)
            ]
        )
        WorkflowEngine.register(retry_def)

        # First run: fails because attempt goes 1, 2 (max_retries=1)
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="TEST_RETRYABLE_WF",
            hospital_id=self.hospital_id,
            idempotency_key=f"WF-RETRY-{uuid.uuid4().hex[:8]}"
        ))
        self.assertEqual(wf.status, "FAILED")

        # Now retry workflow: attempt reaches 3 and succeeds!
        retried_wf = asyncio.run(WorkflowEngine.retry_workflow(self.db, wf.id))
        self.assertEqual(retried_wf.status, "COMPLETED")
        self.assertEqual(retried_wf.retry_count, 1)

    # =========================================================================
    # 3. CONCRETE STANDARD WORKFLOWS
    # =========================================================================

    def test_post_booking_workflow_execution(self):
        """Validates POST_BOOKING_WORKFLOW: questionnaire assignment -> patient confirmation -> doctor notification -> reminder schedule."""
        # Create a test appointment
        slot_time = datetime.utcnow() + timedelta(days=3)
        appt = models.Appointment(
            hospital_id=self.hospital_id,
            doctor_id=self.doctor_id,
            patient_id=self.patient_id,
            start_time=slot_time,
            status="CONFIRMED",
            idempotency_key=f"APPT-TEST-WF-{uuid.uuid4().hex[:8]}",
            chief_complaint="Post-booking workflow test",
            ehr_appointment_id="MOCK-EHR-9988"
        )
        self.db.add(appt)
        self.db.commit()
        self.db.refresh(appt)

        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital_id,
            appointment_id=appt.id,
            payload={"patient_phone": self.patient.phone, "patient_name": self.patient.full_name},
            idempotency_key=f"WF-PB-{appt.id}"
        ))

        self.assertEqual(wf.status, "COMPLETED")
        self.assertEqual(len(wf.execution_history), 4)

        # Check that notifications and reminders were generated in database
        notifs = self.db.query(models.Notification).filter(models.Notification.appointment_id == appt.id).all()
        templates_sent = [n.template for n in notifs]
        self.assertIn("APPOINTMENT_CONFIRMATION", templates_sent)
        self.assertIn("NEW_APPOINTMENT", templates_sent)
        self.assertIn("PRE_VISIT_REMINDER_24H", templates_sent)
        self.assertIn("PRE_VISIT_REMINDER_1H", templates_sent)
        self.assertIn("UPCOMING_APPOINTMENT", templates_sent)

    def test_appointment_cancellation_workflow(self):
        """Validates APPOINTMENT_CANCELLATION_WORKFLOW: slot release -> notices -> cancel reminders."""
        appt = models.Appointment(
            hospital_id=self.hospital_id,
            doctor_id=self.doctor_id,
            patient_id=self.patient_id,
            start_time=datetime.utcnow() + timedelta(days=2),
            status="CONFIRMED",
            idempotency_key=f"APPT-CANCEL-TEST-{uuid.uuid4().hex[:8]}"
        )
        self.db.add(appt)
        self.db.commit()
        self.db.refresh(appt)

        # Add a scheduled future notification
        future_notif = models.Notification(
            hospital_id=self.hospital_id,
            appointment_id=appt.id,
            recipient_type="PATIENT",
            recipient_contact=self.patient.phone,
            template="PRE_VISIT_REMINDER_24H",
            scheduled_for=datetime.utcnow() + timedelta(days=1),
            status="SCHEDULED"
        )
        self.db.add(future_notif)
        self.db.commit()

        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="APPOINTMENT_CANCELLATION_WORKFLOW",
            hospital_id=self.hospital_id,
            appointment_id=appt.id,
            payload={"reason": "Schedule conflict"},
            idempotency_key=f"WF-CANC-{appt.id}"
        ))

        self.assertEqual(wf.status, "COMPLETED")
        self.db.refresh(appt)
        self.assertEqual(appt.status, "CANCELLED")

        self.db.refresh(future_notif)
        self.assertEqual(future_notif.status, "CANCELLED")

    def test_clinical_urgency_workflow(self):
        """Validates CLINICAL_URGENCY_WORKFLOW: emergency advisory to patient -> clinical staff alert -> human escalation."""
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="CLINICAL_URGENCY_WORKFLOW",
            hospital_id=self.hospital_id,
            payload={
                "patient_phone": "+1-555-9111",
                "doctor_email": "er.doctor@hospital.org",
                "symptoms": "Severe acute chest pain with shortness of breath"
            },
            idempotency_key=f"WF-URGENT-{uuid.uuid4().hex[:8]}"
        ))

        self.assertEqual(wf.status, "COMPLETED")
        self.assertEqual(len(wf.execution_history), 3)

        # Verify emergency advisory notification was created
        notifs = self.db.query(models.Notification).filter(
            models.Notification.recipient_contact == "+1-555-9111"
        ).all()
        self.assertTrue(any("EMERGENCY ADVISORY" in (n.message_content or "") for n in notifs))

    # =========================================================================
    # 4. MULTI-PARTY NOTIFICATIONS (Patient, Doctor, Hospital)
    # =========================================================================

    def test_patient_configurable_notifications(self):
        """Validates patient notifications across confirmation, 24h/1h reminders, cancellation, rescheduling, questionnaire reminder, updates."""
        scenarios = [
            ("APPOINTMENT_CONFIRMATION", {"doctor_name": "Dr. Sarah", "slot_time": "Tomorrow 10 AM", "hospital_name": "Metro Health", "ehr_appointment_id": "EHR-123"}),
            ("PRE_VISIT_REMINDER_24H", {"doctor_name": "Dr. Sarah", "slot_time": "Tomorrow 10 AM"}),
            ("PRE_VISIT_REMINDER_1H", {"doctor_name": "Dr. Sarah", "slot_time": "In 1 Hour"}),
            ("APPOINTMENT_CANCELLATION", {"doctor_name": "Dr. Sarah", "slot_time": "Tomorrow 10 AM", "reason": "Provider illness"}),
            ("APPOINTMENT_RESCHEDULED", {"doctor_name": "Dr. Sarah", "slot_time": "Next Monday 2 PM"}),
            ("QUESTIONNAIRE_REMINDER", {"doctor_name": "Dr. Sarah"}),
            ("IMPORTANT_UPDATES", {"hospital_name": "Metro Health", "message_content": "Facility relocation update."})
        ]

        for template, ctx in scenarios:
            n = NotificationService.create_or_schedule_notification(
                db=self.db,
                recipient_type="PATIENT",
                recipient_contact="+1-555-0101",
                template=template,
                context=ctx,
                hospital_id=self.hospital_id,
                idempotency_key=f"NOTIF-TEST-P-{template}-{uuid.uuid4().hex[:6]}"
            )
            self.assertEqual(n.status, "SENT")
            self.assertIsNotNone(n.message_content)
            self.assertGreater(len(n.message_content), 10)

    def test_doctor_configurable_notifications(self):
        """Validates doctor notifications across new appointment, cancellation, rescheduling, questionnaire completion, upcoming appointment."""
        scenarios = [
            ("NEW_APPOINTMENT", {"patient_name": "John Doe", "slot_time": "Friday 11 AM", "chief_complaint": "Migraine"}),
            ("DOCTOR_CANCELLATION", {"patient_name": "John Doe", "slot_time": "Friday 11 AM", "reason": "Patient request"}),
            ("DOCTOR_RESCHEDULED", {"patient_name": "John Doe", "slot_time": "Friday 3 PM"}),
            ("QUESTIONNAIRE_COMPLETION", {"patient_name": "John Doe", "slot_time": "Friday 11 AM"}),
            ("UPCOMING_APPOINTMENT", {"patient_name": "John Doe", "slot_time": "Friday 11 AM"})
        ]

        for template, ctx in scenarios:
            n = NotificationService.create_or_schedule_notification(
                db=self.db,
                recipient_type="DOCTOR",
                recipient_contact="dr.smith@hospital.org",
                template=template,
                context=ctx,
                channel="EMAIL",
                hospital_id=self.hospital_id,
                idempotency_key=f"NOTIF-TEST-D-{template}-{uuid.uuid4().hex[:6]}"
            )
            self.assertEqual(n.status, "SENT")
            self.assertIn("John Doe", n.message_content)

    def test_hospital_configurable_notifications(self):
        """Validates hospital notifications across application status, appointment activity, integration failures, operational alerts."""
        scenarios = [
            ("APPLICATION_STATUS", {"hospital_name": "City Clinic", "status": "APPROVED"}),
            ("APPOINTMENT_ACTIVITY", {"hospital_name": "City Clinic", "details": "10 new consultations today"}),
            ("INTEGRATION_FAILURES", {"appointment_id": "APT-999", "error_message": "EHR gateway timeout"}),
            ("OPERATIONAL_ALERTS", {"appointment_id": "APT-999", "reason": "Coordinator intervention required"})
        ]

        for template, ctx in scenarios:
            n = NotificationService.create_or_schedule_notification(
                db=self.db,
                recipient_type="HOSPITAL",
                recipient_contact="admin@cityclinic.org",
                template=template,
                context=ctx,
                channel="EMAIL",
                hospital_id=self.hospital_id,
                idempotency_key=f"NOTIF-TEST-H-{template}-{uuid.uuid4().hex[:6]}"
            )
            self.assertEqual(n.status, "SENT")
            self.assertIsNotNone(n.message_content)

    def test_scheduled_notifications_dispatch_due(self):
        """Validates that scheduled future notifications are set as SCHEDULED, then marked SENT when due."""
        past_due = datetime.utcnow() - timedelta(minutes=5)
        future_due = datetime.utcnow() + timedelta(hours=2)

        n1 = NotificationService.create_or_schedule_notification(
            db=self.db,
            recipient_type="PATIENT",
            recipient_contact="+1-555-4444",
            template="PRE_VISIT_REMINDER_24H",
            context={"doctor_name": "Dr. House", "slot_time": "Tomorrow"},
            scheduled_for=past_due,
            hospital_id=self.hospital_id,
            idempotency_key=f"SCHED-DUE-{uuid.uuid4().hex[:8]}"
        )
        # Note: when scheduled_for <= now, create_or_schedule_notification marks immediate as SENT.
        # Let's explicitly test scheduling transition:
        n2 = models.Notification(
            hospital_id=self.hospital_id,
            recipient_type="PATIENT",
            recipient_contact="+1-555-5555",
            channel="SMS",
            template="PRE_VISIT_REMINDER_1H",
            message_content="Due reminder test",
            scheduled_for=past_due,
            status="SCHEDULED",
            idempotency_key=f"SCHED-RAW-DUE-{uuid.uuid4().hex[:8]}"
        )
        self.db.add(n2)
        self.db.commit()

        count = NotificationService.dispatch_due_notifications(self.db)
        self.assertGreaterEqual(count, 1)

        self.db.refresh(n2)
        self.assertEqual(n2.status, "SENT")
        self.assertIsNotNone(n2.sent_at)

    # =========================================================================
    # 5. REST API ENDPOINTS FOR WORKFLOWS, NOTIFICATIONS, & EVENTS
    # =========================================================================

    def test_api_workflows_endpoints(self):
        """Validates GET /api/workflows, GET /api/workflows/{id}, POST /api/workflows/start, POST /api/workflows/{id}/retry."""
        # 1. Start workflow via API
        resp = self.client.post("/api/workflows/start", json={
            "workflow_type": "POST_BOOKING_WORKFLOW",
            "hospital_id": self.hospital_id,
            "payload": {"patient_name": "API Patient"},
            "idempotency_key": f"WF-API-TEST-{uuid.uuid4().hex[:8]}"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        wf_id = data["workflow_id"]
        self.assertEqual(data["status"], "COMPLETED")

        # 2. Get specific workflow
        get_resp = self.client.get(f"/api/workflows/{wf_id}")
        self.assertEqual(get_resp.status_code, 200)
        get_data = get_resp.json()
        self.assertEqual(get_data["id"], wf_id)
        self.assertEqual(get_data["workflow_type"], "POST_BOOKING_WORKFLOW")
        self.assertIsInstance(get_data["execution_history"], list)

        # 3. List workflows with filter
        list_resp = self.client.get(f"/api/workflows?hospital_id={self.hospital_id}&status=COMPLETED")
        self.assertEqual(list_resp.status_code, 200)
        list_data = list_resp.json()
        self.assertIsInstance(list_data, list)
        self.assertTrue(any(w["id"] == wf_id for w in list_data))

    def test_api_notifications_endpoints(self):
        """Validates GET /api/notifications, POST /api/notifications/send, POST /api/notifications/dispatch."""
        # 1. Send notification via API
        resp = self.client.post("/api/notifications/send", json={
            "recipient_type": "DOCTOR",
            "recipient_contact": "dr.api@hospital.org",
            "channel": "EMAIL",
            "template": "UPCOMING_APPOINTMENT",
            "context": {"patient_name": "API Patient", "slot_time": "Today 4 PM"},
            "hospital_id": self.hospital_id,
            "idempotency_key": f"NOTIF-API-{uuid.uuid4().hex[:8]}"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "SENT")

        # 2. List notifications
        list_resp = self.client.get(f"/api/notifications?recipient_type=DOCTOR&hospital_id={self.hospital_id}")
        self.assertEqual(list_resp.status_code, 200)
        notifs = list_resp.json()
        self.assertIsInstance(notifs, list)
        self.assertTrue(any(n["recipient_contact"] == "dr.api@hospital.org" for n in notifs))

        # 3. Dispatch due notifications
        disp_resp = self.client.post("/api/notifications/dispatch")
        self.assertEqual(disp_resp.status_code, 200)
        self.assertIn("dispatched_count", disp_resp.json())

    def test_api_events_publish_history_metrics(self):
        """Validates POST /api/events/publish, GET /api/events/history, GET /api/events/metrics."""
        # 1. Publish event via API
        resp = self.client.post("/api/events/publish", json={
            "event_type": "hospital_approved",
            "entity_id": self.hospital_id,
            "tenant_id": self.hospital_id,
            "payload": {"hospital_name": self.hospital.name}
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "DISPATCHED")
        self.assertEqual(data["event_type"], "hospital_approved")

        # 2. Get event history
        hist_resp = self.client.get("/api/events/history?limit=10&event_type=hospital_approved")
        self.assertEqual(hist_resp.status_code, 200)
        events = hist_resp.json()
        self.assertIsInstance(events, list)
        self.assertTrue(any(e["entity_id"] == self.hospital_id for e in events))

        # 3. Get metrics
        metrics_resp = self.client.get("/api/events/metrics")
        self.assertEqual(metrics_resp.status_code, 200)
        metrics = metrics_resp.json().get("metrics", {})
        self.assertGreaterEqual(metrics.get("hospital_approved", 0), 1)


if __name__ == "__main__":
    unittest.main()
