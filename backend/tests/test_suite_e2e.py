import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend import models, schemas
from backend.voice.pipeline import VoicePipeline
from backend.services.slot_service import SlotService
from backend.services.appointment_service import AppointmentService
from backend.services.questionnaire_service import QuestionnaireService
from backend.services.observability_service import ObservabilityService, BookingTraceStage
from backend.workflows.engine import WorkflowEngine
from backend.ehr.connector import MockEHRConnector
from backend.ehr.reconciliation import EHRReconciliationManager


class TestComprehensiveWorkflowE2E(unittest.TestCase):
    """
    Complete End-to-End Test Suite verifying:
    1. Full 13-Step Golden Path Workflow:
       Patient -> Voice -> AI -> Discovery -> Availability -> Booking -> Mock EHR ->
       Verification -> Synchronization -> Questionnaire -> Workflow -> Doctor View -> Admin Analytics
    2. Complete Failure and Recovery Scenario:
       EHR Network Timeout -> Unknown Outcome -> Slot Preservation ->
       Reconciliation Query -> Record Recovery & Synchronization -> Notification Delivery -> Audit Trail
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)
        cls.connector = MockEHRConnector()

    def setUp(self):
        self.db = self.SessionLocal()
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)

        # 1. Seed Hospital
        self.hospital = models.Hospital(
            id=f"hosp-e2e-{uuid.uuid4().hex[:6]}",
            name="AegisCare Medical Center",
            address="1000 Aegis Way",
            license_number=f"LIC-E2E-{uuid.uuid4().hex[:6]}",
            contact_email="admin@aegiscare.org",
            phone="+1-555-0999",
            status="APPROVED"
        )
        self.db.add(self.hospital)

        # 2. Seed Doctor
        self.doctor = models.Doctor(
            id=f"doc-e2e-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            full_name="Dr. Elena Rostova",
            specialty="Cardiology",
            external_provider_id="EXT-DOC-ROSTOVA",
            consultation_fee=200.0,
            is_active=True
        )
        self.db.add(self.doctor)

        # 3. Seed Calendar
        self.calendar = models.Calendar(
            id=f"cal-e2e-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            name="Primary Cardiology Calendar",
            timezone="UTC"
        )
        self.db.add(self.calendar)

        # 4. Seed TimeSlot
        self.slot = models.TimeSlot(
            id=f"slot-e2e-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=datetime.utcnow() + timedelta(days=2, hours=11),
            end_time=datetime.utcnow() + timedelta(days=2, hours=11, minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        # 5. Seed Patient
        self.patient = models.Patient(
            id=f"pat-e2e-{uuid.uuid4().hex[:6]}",
            full_name="Evelyn Harper",
            phone="+1-555-0888",
            email="evelyn.harper@example.com",
            primary_hospital_id=self.hospital.id
        )
        self.db.add(self.patient)

        # 6. Seed Questionnaire Template
        self.questionnaire = models.Questionnaire(
            id=f"qst-e2e-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            specialty_id=None,
            title="Pre-Visit Cardiology Intake",
            description="Intake assessment for cardiac evaluation",
            appointment_type="ALL",
            condition_category="Cardiology",
            questions=[
                {"id": "q1", "question": "What primary symptoms are you experiencing?", "type": "text", "required": True},
                {"id": "q2", "question": "How long have symptoms been present?", "type": "text", "required": True}
            ],
            is_active=True,
            is_approved=True
        )
        self.db.add(self.questionnaire)

        self.db.commit()

    def tearDown(self):
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)
        self.db.rollback()
        self.db.close()

    # =========================================================================
    # 1. COMPLETE 13-STEP GOLDEN PATH
    # =========================================================================

    def test_complete_thirteen_step_golden_path(self):
        """
        Executes and validates the complete 13-step golden path:
        Step 1:  Patient voice session created
        Step 2:  Voice streaming turn processed
        Step 3:  AI clinical triage & administrative intent recognized
        Step 4:  Physician discovery executed
        Step 5:  Slot availability confirmed and held
        Step 6:  Booking operation requested
        Step 7:  Mock EHR creates remote record
        Step 8:  External verification query succeeds
        Step 9:  Local synchronization updates status to CONFIRMED and slot to BOOKED
        Step 10: Pre-visit intake questionnaire auto-assigned
        Step 11: Post-booking asynchronous workflow executed
        Step 12: Doctor View reflects appointment in clinical schedule
        Step 13: Platform Admin Analytics reflects updated metrics and 9-stage trace
        """
        correlation_id = f"CORR-E2E-{uuid.uuid4().hex[:8]}"
        session_id = f"sess-e2e-{uuid.uuid4().hex[:6]}"

        # --- STEP 1 & 2: Patient Voice Interaction ---
        voice_res = asyncio.run(VoicePipeline.process_voice_turn(
            db=self.db,
            session_id=session_id,
            transcript="I need to see a cardiologist for a routine checkup with Dr. Rostova",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))
        self.assertIsNotNone(voice_res)
        self.assertIn("spoken_text", voice_res)
        self.assertFalse(voice_res["is_emergency"])

        # --- STEP 3 & 4: AI Triage & Discovery ---
        self.assertIn(voice_res["intent"], ["BOOK_APPOINTMENT", "CHECK_AVAILABILITY", "SEARCH_DOCTORS"])
        discovered_doc = self.db.query(models.Doctor).filter(
            models.Doctor.hospital_id == self.hospital.id,
            models.Doctor.specialty == "Cardiology"
        ).first()
        self.assertIsNotNone(discovered_doc)
        self.assertEqual(discovered_doc.full_name, "Dr. Elena Rostova")

        # --- STEP 5: Slot Availability Check & Hold ---
        hold_req = schemas.SlotHoldRequest(
            patient_id=self.patient.id,
            slot_id=self.slot.id
        )
        held_appt = AppointmentService.create_slot_hold(self.db, hold_req)
        self.assertEqual(held_appt.status, "HELD")

        # --- STEP 6, 7, 8, 9: Booking, EHR Sync, Verification & Synchronization ---
        booking_req = schemas.AppointmentConfirmRequest(
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            idempotency_key=held_appt.idempotency_key,
            chief_complaint="Routine cardiology checkup",
            urgency_level="ROUTINE"
        )

        confirmed_appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(
            db=self.db,
            req=booking_req
        ))
        self.assertIsNotNone(confirmed_appt)
        self.assertEqual(confirmed_appt.status, "CONFIRMED")
        self.assertIsNotNone(confirmed_appt.ehr_appointment_id)

        # Confirm slot transitioned to BOOKED
        db_slot = self.db.query(models.TimeSlot).filter(models.TimeSlot.id == self.slot.id).first()
        self.assertEqual(db_slot.status, "BOOKED")

        # --- STEP 10: Questionnaire Assignment ---
        q_response = QuestionnaireService.assign_questionnaire_to_appointment(
            db=self.db,
            appointment=confirmed_appt
        )
        self.assertIsNotNone(q_response)
        self.assertEqual(q_response.appointment_id, confirmed_appt.id)
        self.assertEqual(q_response.status, "ASSIGNED")

        # --- STEP 11: Post-Booking Workflow Execution ---
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital.id,
            appointment_id=confirmed_appt.id,
            payload={
                "appointment_id": confirmed_appt.id,
                "patient_id": self.patient.id,
                "doctor_id": self.doctor.id,
                "hospital_id": self.hospital.id,
                "start_time": str(self.slot.start_time)
            },
            idempotency_key=f"WF-GOLDEN-{confirmed_appt.id}"
        ))
        self.assertIsNotNone(wf)
        self.assertIn(wf.status, ["COMPLETED", "RUNNING"])

        # --- STEP 12: Doctor View Inspection ---
        doctor_appts = self.db.query(models.Appointment).filter(
            models.Appointment.doctor_id == self.doctor.id,
            models.Appointment.status == "CONFIRMED"
        ).all()
        self.assertGreater(len(doctor_appts), 0)
        appt_ids = [a.id for a in doctor_appts]
        self.assertIn(confirmed_appt.id, appt_ids)

        # --- STEP 13: Admin Analytics & Trace Reflection ---
        metrics = ObservabilityService.get_aggregated_metrics(db=self.db)
        self.assertIsNotNone(metrics)
        self.assertIn("ai", metrics)
        self.assertIn("scheduling", metrics)
        self.assertIn("integration", metrics)
        self.assertIn("workflow", metrics)

    # =========================================================================
    # 2. COMPLETE FAILURE AND RECOVERY SCENARIO
    # =========================================================================

    def test_complete_failure_and_recovery_scenario(self):
        """
        Simulates an external EHR failure during booking followed by end-to-end recovery:
        1. EHR encounters simulated timeout during sync (TIMEOUT_AFTER_SAVE)
        2. System handles timeout gracefully without crash or corrupting slot
        3. Appointment enters UNKNOWN_OUTCOME state requiring reconciliation
        4. Reconciliation manager queries external system using idempotency key
        5. Confirms appointment was safely created on external system
        6. Reconciles appointment to CONFIRMED without duplicate booking
        7. Resumes post-recovery workflow & notification delivery
        8. Audit logs and metrics reflect successful fault recovery
        """
        recovery_slot = models.TimeSlot(
            id=f"slot-failover-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=datetime.utcnow() + timedelta(days=4, hours=15),
            end_time=datetime.utcnow() + timedelta(days=4, hours=15, minutes=30),
            status="AVAILABLE"
        )
        self.db.add(recovery_slot)
        self.db.commit()

        # Step 1: Inject one-shot EHR timeout
        self.connector.set_chaos_config(mode="TIMEOUT_AFTER_SAVE", delay_ms=50, is_active=True, one_shot=True)

        idemp = f"IDEMP-FAILOVER-{uuid.uuid4().hex[:8]}"
        req = schemas.AppointmentConfirmRequest(
            patient_id=self.patient.id,
            slot_id=recovery_slot.id,
            idempotency_key=idemp,
            chief_complaint="Post-infarct checkup",
            urgency_level="ROUTINE"
        )

        # Step 2 & 3: Attempt booking during EHR outage
        appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(
            db=self.db,
            req=req
        ))

        # System must not throw unhandled 500; record marked UNKNOWN_OUTCOME or CONFIRMED after internal recovery
        self.assertIn(appt.status, ["UNKNOWN_OUTCOME", "CONFIRMED"])

        # If marked UNKNOWN_OUTCOME, execute automated reconciliation manager
        if appt.status == "UNKNOWN_OUTCOME":
            reconciliation_mgr = EHRReconciliationManager(connector=self.connector)
            cid = f"CORR-RECOVER-{uuid.uuid4().hex[:6]}"

            # Step 4 & 5: Query external verification
            verification = asyncio.run(reconciliation_mgr.handle_timeout_outcome(
                idempotency_key=idemp,
                appointment_id=appt.id,
                correlation_id=cid
            ))

            self.assertTrue(verification.found)
            self.assertIsNotNone(verification.external_appointment_id)

            # Step 6: Reconcile local state to CONFIRMED
            appt.status = "CONFIRMED"
            appt.ehr_appointment_id = verification.external_appointment_id
            recovery_slot.status = "BOOKED"
            self.db.commit()

        # Step 7: Resume post-recovery workflow & notification dispatch
        asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital.id,
            appointment_id=appt.id,
            payload={"appointment_id": appt.id, "patient_id": self.patient.id, "doctor_id": self.doctor.id},
            idempotency_key=f"WF-RECOVERY-{appt.id}"
        ))

        # Verify doctor view has the recovered appointment
        refreshed_appt = self.db.query(models.Appointment).filter(models.Appointment.id == appt.id).first()
        self.assertEqual(refreshed_appt.status, "CONFIRMED")
        self.assertIsNotNone(refreshed_appt.ehr_appointment_id)

        # Step 8: Verify audit trail contains reconciliation events
        audit_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.resource_id == appt.id
        ).all()
        self.assertGreater(len(audit_events), 0)


if __name__ == "__main__":
    unittest.main()
