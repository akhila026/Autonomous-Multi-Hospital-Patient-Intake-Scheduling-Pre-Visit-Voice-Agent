import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend import models, schemas
from backend.services.slot_service import SlotService
from backend.services.appointment_service import AppointmentService
from backend.services.mock_ehr_service import MockEhrService
from backend.ehr.connector import MockEHRConnector, EHRAppointmentPayload
from backend.ehr.service import EHRIntegrationService
from backend.workflows.engine import WorkflowEngine
from backend.workflows.events import event_dispatcher, DomainEvent, EventType
from backend.ai.agent import AIPatientAccessAgent
from backend.capabilities.registry import capability_registry


class TestComprehensiveWorkflowIntegration(unittest.TestCase):
    """
    Complete Integration Test Suite verifying data flow and transitions across all 7 stages:
    1. AI -> Scheduling
    2. Scheduling -> Appointment
    3. Appointment -> EHR
    4. EHR -> Verification
    5. Verification -> Synchronization
    6. Booking -> Workflow
    7. Workflow -> Notification
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

    def setUp(self):
        self.db = self.SessionLocal()

        # 1. Seed Hospital
        self.hospital = models.Hospital(
            id=f"hosp-integ-{uuid.uuid4().hex[:6]}",
            name="AegisCare Integration Hospital",
            address="500 Medical Center Dr",
            license_number=f"LIC-INT-{uuid.uuid4().hex[:6]}",
            contact_email="admin@aegisinteg.org",
            phone="+1-555-0100",
            status="APPROVED"
        )
        self.db.add(self.hospital)

        # 2. Seed Doctor
        self.doctor = models.Doctor(
            id=f"doc-integ-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            full_name="Dr. Amanda Cross",
            specialty="Cardiology",
            consultation_fee=175.0,
            is_active=True
        )
        self.db.add(self.doctor)

        # 3. Seed Calendar
        self.calendar = models.Calendar(
            id=f"cal-integ-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            name="Dr. Cross Outpatient Calendar",
            timezone="UTC"
        )
        self.db.add(self.calendar)

        # 4. Seed Slots
        self.slot = models.TimeSlot(
            id=f"slot-integ-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=datetime.utcnow() + timedelta(days=2, hours=9),
            end_time=datetime.utcnow() + timedelta(days=2, hours=9, minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        # 5. Seed Patient
        self.patient = models.Patient(
            id=f"pat-integ-{uuid.uuid4().hex[:6]}",
            full_name="Jonathan Miller",
            phone="+1-555-0199",
            email="jonathan.miller@example.com",
            primary_hospital_id=self.hospital.id
        )
        self.db.add(self.patient)

        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # =========================================================================
    # 1. AI -> SCHEDULING INTEGRATION
    # =========================================================================

    def test_ai_to_scheduling_integration(self):
        """Verifies AI Agent processes natural language inquiry and checks availability."""
        session_id = f"sess-ai-sched-{uuid.uuid4().hex[:6]}"

        turn_response = asyncio.run(AIPatientAccessAgent.process_turn(
            db=self.db,
            session_id=session_id,
            message=f"I need to book a cardiology appointment with Dr. Cross",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))

        self.assertIsNotNone(turn_response)
        self.assertIn(turn_response.intent, ["BOOK_APPOINTMENT", "CHECK_AVAILABILITY", "SYMPTOM_TRIAGE"])
        self.assertFalse(turn_response.is_emergency)

    # =========================================================================
    # 2. SCHEDULING -> APPOINTMENT INTEGRATION
    # =========================================================================

    def test_scheduling_to_appointment_integration(self):
        """Verifies holding a slot transitions slot to HELD and creates provisional record."""
        # 1. Hold slot
        held_slot = SlotService.hold_slot(self.db, self.slot.id, hold_minutes=5)
        self.assertEqual(held_slot.status, "HELD")
        self.assertIsNotNone(held_slot.hold_expires_at)

        # 2. Create Appointment in provisional state
        idemp = f"IDEMP-SCHED-APPT-{uuid.uuid4().hex[:8]}"
        appt = models.Appointment(
            id=str(uuid.uuid4()),
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            slot_id=held_slot.id,
            start_time=held_slot.start_time,
            end_time=held_slot.end_time,
            status="PENDING_EHR_SYNC",
            idempotency_key=idemp
        )
        self.db.add(appt)
        self.db.commit()

        # Verify linked relationship
        db_appt = self.db.query(models.Appointment).filter(models.Appointment.id == appt.id).first()
        self.assertIsNotNone(db_appt)
        self.assertEqual(db_appt.slot_id, self.slot.id)
        self.assertEqual(db_appt.status, "PENDING_EHR_SYNC")

    # =========================================================================
    # 3. APPOINTMENT -> EHR INTEGRATION
    # =========================================================================

    def test_appointment_to_ehr_integration(self):
        """Verifies outbound dispatch of appointment payload to Mock EHR connector."""
        connector = MockEHRConnector()
        idemp = f"IDEMP-EHR-DISP-{uuid.uuid4().hex[:8]}"

        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-101",
            external_provider_id="EXT-DOC-202",
            patient_name=self.patient.full_name,
            doctor_name=self.doctor.full_name,
            hospital_name=self.hospital.name,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp,
            chief_complaint="Routine cardiology follow-up"
        )

        res = asyncio.run(connector.create_appointment(payload))
        self.assertIsNotNone(res)
        appt_id = res.get("id") or res.get("appointment_id")
        self.assertIsNotNone(appt_id)
        self.assertIn("CONFIRMED", res.get("status", ""))

    # =========================================================================
    # 4. EHR -> VERIFICATION INTEGRATION
    # =========================================================================

    def test_ehr_to_verification_integration(self):
        """Verifies explicit post-sync verification check against external EHR."""
        connector = MockEHRConnector()
        idemp = f"IDEMP-VERIFY-{uuid.uuid4().hex[:8]}"

        # 1. Create on EHR
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-101",
            external_provider_id="EXT-DOC-202",
            patient_name=self.patient.full_name,
            doctor_name=self.doctor.full_name,
            hospital_name=self.hospital.name,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )
        create_res = asyncio.run(connector.create_appointment(payload))
        ext_id = create_res.get("id") or create_res.get("appointment_id")

        # 2. Explicit Verification check
        verify_res = asyncio.run(connector.verify_appointment(idempotency_key=idemp))
        self.assertTrue(verify_res.found)
        self.assertEqual(verify_res.external_appointment_id, ext_id)
        self.assertIn("CONFIRMED", verify_res.status or "")

    # =========================================================================
    # 5. VERIFICATION -> SYNCHRONIZATION INTEGRATION
    # =========================================================================

    def test_verification_to_synchronization_integration(self):
        """Verifies external verification commits local record to CONFIRMED and BOOKED."""
        idemp = f"IDEMP-SYNC-{uuid.uuid4().hex[:8]}"
        req = schemas.AppointmentConfirmRequest(
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            idempotency_key=idemp,
            chief_complaint="Chest pressure check",
            urgency_level="ROUTINE"
        )

        confirmed_appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(
            db=self.db,
            req=req
        ))

        self.assertIsNotNone(confirmed_appt)
        self.assertEqual(confirmed_appt.status, "CONFIRMED")
        self.assertIsNotNone(confirmed_appt.ehr_appointment_id)

        # Verify Slot updated to BOOKED
        refreshed_slot = self.db.query(models.TimeSlot).filter(models.TimeSlot.id == self.slot.id).first()
        self.assertEqual(refreshed_slot.status, "BOOKED")

    # =========================================================================
    # 6. BOOKING -> WORKFLOW INTEGRATION
    # =========================================================================

    def test_booking_to_workflow_integration(self):
        """Verifies appointment booking triggers post-booking event workflow."""
        appt_id = str(uuid.uuid4())
        appt = models.Appointment(
            id=appt_id,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            start_time=self.slot.start_time,
            end_time=self.slot.end_time,
            status="CONFIRMED",
            idempotency_key=f"IDEMP-WF-{uuid.uuid4().hex[:8]}"
        )
        self.db.add(appt)
        self.db.commit()

        # Execute Post-Booking Workflow directly with database session
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital.id,
            appointment_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "patient_id": self.patient.id,
                "doctor_id": self.doctor.id,
                "hospital_id": self.hospital.id,
                "start_time": str(self.slot.start_time)
            },
            idempotency_key=f"WF-EXEC-{appt.id}"
        ))

        self.assertIsNotNone(wf)
        self.assertIn(wf.status, ["COMPLETED", "RUNNING"])

    # =========================================================================
    # 7. WORKFLOW -> NOTIFICATION INTEGRATION
    # =========================================================================

    def test_workflow_to_notification_integration(self):
        """Verifies workflow generates and dispatches multi-party notifications."""
        appt_id = str(uuid.uuid4())
        appt = models.Appointment(
            id=appt_id,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            start_time=self.slot.start_time,
            end_time=self.slot.end_time,
            status="CONFIRMED",
            idempotency_key=f"IDEMP-NOTIF-{uuid.uuid4().hex[:8]}"
        )
        self.db.add(appt)
        self.db.commit()

        # Run Post-Booking Workflow which dispatches notifications
        asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital.id,
            appointment_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "patient_id": self.patient.id,
                "doctor_id": self.doctor.id,
                "hospital_id": self.hospital.id,
                "start_time": str(self.slot.start_time)
            },
            idempotency_key=f"WF-NOTIF-{appt.id}"
        ))

        # Check notifications generated for the appointment
        notifications = self.db.query(models.Notification).filter(
            models.Notification.appointment_id == appt.id
        ).all()

        self.assertGreater(len(notifications), 0)
        recipient_types = [n.recipient_type for n in notifications]
        self.assertIn("PATIENT", recipient_types)


if __name__ == "__main__":
    unittest.main()
