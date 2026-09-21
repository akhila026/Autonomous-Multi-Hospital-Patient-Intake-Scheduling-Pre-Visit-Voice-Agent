import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.database import SessionLocal, Base, engine
from backend import models, schemas
from backend.services.appointment_service import AppointmentService
from backend.services.slot_service import SlotService
from backend.ehr.service import EHRIntegrationService
from backend.ehr.connector import mock_ehr_connector
from backend.capabilities.base import CapabilityContext
from backend.capabilities.registry import capability_registry
from backend.ai.agent import AIPatientAccessAgent
from mock_ehr.database import EhrSessionLocal
from mock_ehr import models as ehr_models


class TestAppointmentBookingFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        self.db: Session = SessionLocal()
        self.ehr_db: Session = EhrSessionLocal()
        self._reset_mock_ehr_chaos()
        self._setup_test_fixtures()

    def tearDown(self):
        self._reset_mock_ehr_chaos()
        self.db.close()
        self.ehr_db.close()

    def _reset_mock_ehr_chaos(self):
        chaos = self.ehr_db.query(ehr_models.EhrChaosConfig).first()
        if chaos:
            chaos.mode = "NORMAL"
            chaos.failure_active = False
            self.ehr_db.commit()

    def _setup_test_fixtures(self):
        # Create hospital tenant
        self.hospital = models.Hospital(
            id=f"hosp-flow-{uuid.uuid4().hex[:6]}",
            name="Aegis General Medical Center",
            address="100 Medical Center Way, Suite 400",
            license_number=f"LIC-FLOW-{uuid.uuid4().hex[:6]}",
            contact_email="admin@aegisgeneral.org",
            phone="+1-555-099-1122",
            status="APPROVED"
        )
        self.db.add(self.hospital)

        # Create doctor
        self.doctor = models.Doctor(
            id=f"doc-flow-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            full_name="Dr. Marcus Welby",
            specialty="Orthopedics",
            consultation_fee=175.0,
            status="ACTIVE"
        )
        self.db.add(self.doctor)

        # Create calendar
        self.calendar = models.Calendar(
            id=f"cal-flow-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            name="Primary Orthopedic Calendar",
            is_active=True
        )
        self.db.add(self.calendar)

        # Create doctor availability schedules for all 7 days
        for day in range(7):
            sched = models.AvailabilitySchedule(
                id=f"sched-flow-{day}-{uuid.uuid4().hex[:6]}",
                doctor_id=self.doctor.id,
                day_of_week=day,
                start_time="00:00",
                end_time="23:59",
                is_active=True
            )
            self.db.add(sched)

        # Create patient
        self.patient = models.Patient(
            id=f"pat-flow-{uuid.uuid4().hex[:6]}",
            full_name="Robert Langdon",
            phone="+1-555-443-8899",
            email="robert.langdon@example.com",
            date_of_birth="1978-06-22",
            primary_hospital_id=self.hospital.id
        )
        self.db.add(self.patient)

        # Create available time slots
        slot_time1 = datetime.utcnow() + timedelta(days=2, hours=1)
        self.slot1 = models.TimeSlot(
            id=f"slot-flow-1-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=slot_time1,
            end_time=slot_time1 + timedelta(minutes=30),
            status="AVAILABLE"
        )
        slot_time2 = datetime.utcnow() + timedelta(days=2, hours=2)
        self.slot2 = models.TimeSlot(
            id=f"slot-flow-2-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=slot_time2,
            end_time=slot_time2 + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot1)
        self.db.add(self.slot2)
        self.db.commit()

    def test_happy_path_booking_with_mandatory_external_verification(self):
        """
        Verify the complete 10-step flow:
        Create -> Send to EHR -> Verify External Record -> Synchronize State -> Confirm.
        """
        idempotency_key = f"TEST-BOOK-HAPPY-{uuid.uuid4()}"
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Severe shoulder pain after falling",
            urgency_level="ROUTINE",
            idempotency_key=idempotency_key
        )

        appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        # 1. Internal status must be CONFIRMED only after successful verification
        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "CONFIRMED")
        self.assertIsNotNone(appt.ehr_appointment_id)
        self.assertTrue(appt.ehr_appointment_id.startswith("EHR-"))

        # 2. Slot must be marked BOOKED
        self.db.refresh(self.slot1)
        self.assertEqual(self.slot1.status, "BOOKED")

        # 3. External verification record MUST exist in database
        verif = self.db.query(models.Verification).filter(
            models.Verification.idempotency_key == idempotency_key
        ).first()
        self.assertIsNotNone(verif, "Mandatory verification record must be logged")
        self.assertEqual(verif.status, "FOUND")
        self.assertEqual(verif.external_appointment_id, appt.ehr_appointment_id)

        # 4. Bidirectional mapping must exist
        mapping = self.db.query(models.ExternalIdentifierMapping).filter(
            models.ExternalIdentifierMapping.internal_id == appt.id
        ).first()
        self.assertIsNotNone(mapping, "Internal <-> External appointment mapping must exist")
        self.assertEqual(mapping.external_id, appt.ehr_appointment_id)

        # 5. Outbound integration operations must be logged
        ops = self.db.query(models.IntegrationOperation).filter(
            models.IntegrationOperation.idempotency_key == idempotency_key
        ).all()
        op_types = [op.operation_type for op in ops]
        self.assertIn("CREATE_APPOINTMENT", op_types)
        self.assertIn("VERIFY", op_types)

    def test_slot_collision_prevented(self):
        """
        Verify that when a slot is already booked, second booking attempt is rejected
        with 409 and slot is not double-booked.
        """
        key1 = f"COLLISION-1-{uuid.uuid4()}"
        req1 = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Knee sprain",
            idempotency_key=key1
        )
        appt1 = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req1))
        self.assertEqual(appt1.status, "CONFIRMED")

        # Second attempt with different patient/key on same slot
        key2 = f"COLLISION-2-{uuid.uuid4()}"
        req2 = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Knee second request",
            idempotency_key=key2
        )
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req2))
        self.assertEqual(cm.exception.status_code, 409)

    def test_timeout_after_save_recovery_without_duplicates(self):
        """
        Simulate PRD Option A / B: Socket timeout after EHR save.
        Verification query must find the saved record and reconcile to CONFIRMED without creating a duplicate.
        """
        chaos = self.ehr_db.query(ehr_models.EhrChaosConfig).first()
        chaos.mode = "TIMEOUT_AFTER_SAVE"
        chaos.failure_active = True
        chaos.simulated_delay_ms = 100
        self.ehr_db.commit()

        idempotency_key = f"TIMEOUT-RECOVERY-{uuid.uuid4()}"
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot2.id,
            patient_id=self.patient.id,
            chief_complaint="Lower back discomfort",
            idempotency_key=idempotency_key
        )

        appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        self.assertEqual(appt.status, "CONFIRMED", "Must recover to CONFIRMED via external verification")
        self.assertIsNotNone(appt.ehr_appointment_id)

        # Verify Mock EHR has EXACTLY 1 record for this key (no duplicate created)
        ehr_records = self.ehr_db.query(ehr_models.EhrAppointment).filter(
            ehr_models.EhrAppointment.idempotency_key == idempotency_key
        ).all()
        self.assertEqual(len(ehr_records), 1, "Zero duplicate guarantee violated!")

    def test_outage_creates_reconciliation_record_and_releases_slot(self):
        """
        Simulate PRD Option C: External service outage (HTTP 503).
        System must NOT confirm appointment, must release slot hold, and must create a ReconciliationRecord.
        """
        chaos = self.ehr_db.query(ehr_models.EhrChaosConfig).first()
        chaos.mode = "OUTAGE"
        chaos.failure_active = True
        self.ehr_db.commit()

        idempotency_key = f"OUTAGE-TEST-{uuid.uuid4()}"
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Annual checkup",
            idempotency_key=idempotency_key
        )

        appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        # Must NOT be confirmed!
        self.assertNotEqual(appt.status, "CONFIRMED")
        self.assertEqual(appt.status, "RECONCILIATION_REQUIRED")

        # Slot must be released back to AVAILABLE (not permanently held hostage)
        self.db.refresh(self.slot1)
        self.assertEqual(self.slot1.status, "AVAILABLE")

        # ReconciliationRecord must exist
        recon = self.db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.idempotency_key == idempotency_key
        ).first()
        self.assertIsNotNone(recon, "ReconciliationRecord must be created on outage")
        self.assertEqual(recon.status, "RECONCILIATION_REQUIRED")
        self.assertIn("EHROutageException", recon.failure_reason)

    def test_multi_turn_ai_agent_booking_journey(self):
        """
        Test end-to-end patient AI conversational turn sequence:
        Turn 1: "I need to see Dr. Marcus Welby for shoulder pain" -> AI asks for slot selection
        Turn 2: "Option 1" -> AI validates availability, books through capability, verifies, and confirms.
        """
        session_id = f"ai-sess-flow-{uuid.uuid4().hex[:8]}"

        # Turn 1: Request doctor & complaint
        res1 = asyncio.run(AIPatientAccessAgent.process_turn(
            db=self.db,
            session_id=session_id,
            message="I need to see Dr. Marcus Welby for my shoulder pain",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))

        self.assertFalse(res1.is_emergency)
        self.assertIn("check_availability", res1.capabilities_executed)
        self.assertTrue(res1.needs_clarification, "AI must ask patient to select from available slots")
        self.assertIn("Option 1", res1.reply)

        # Turn 2: Patient selects Option 1
        res2 = asyncio.run(AIPatientAccessAgent.process_turn(
            db=self.db,
            session_id=session_id,
            message="Option 1 please",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))

        self.assertIn("create_appointment", res2.capabilities_executed)
        self.assertIn("Confirmed & Verified", res2.reply)
        self.assertIn("EHR ID", res2.reply)

        # Verify internal database appointment is CONFIRMED and slot is BOOKED
        confirmed_appt = self.db.query(models.Appointment).filter(
            models.Appointment.patient_id == self.patient.id,
            models.Appointment.doctor_id == self.doctor.id
        ).first()
        self.assertIsNotNone(confirmed_appt)
        self.assertEqual(confirmed_appt.status, "CONFIRMED")

    def test_duplicate_request_returns_existing_confirmed_appointment(self):
        """
        Verify that submitting a duplicate booking request with the same idempotency key
        returns the existing confirmed appointment immediately without creating duplicates.
        """
        idempotency_key = f"IDEM-DUP-{uuid.uuid4()}"
        req1 = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Follow-up examination",
            idempotency_key=idempotency_key
        )
        appt1 = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req1))
        self.assertEqual(appt1.status, "CONFIRMED")

        # Second identical request (duplicate)
        req2 = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Follow-up examination",
            idempotency_key=idempotency_key
        )
        appt2 = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req2))

        # Must return the same internal appointment record
        self.assertEqual(appt1.id, appt2.id)
        self.assertEqual(appt2.status, "CONFIRMED")
        self.assertEqual(appt1.ehr_appointment_id, appt2.ehr_appointment_id)

        # Ensure total appointment count for this key in DB is exactly 1
        count = self.db.query(models.Appointment).filter(
            models.Appointment.idempotency_key == idempotency_key
        ).count()
        self.assertEqual(count, 1, "Duplicate request created extra database records!")

    def test_network_failure_creates_reconciliation_record_and_releases_slot(self):
        """
        Verify that external network failures (EHRNetworkException) are handled:
        - do not confirm appointment
        - status transitioned to RECONCILIATION_REQUIRED
        - slot released to AVAILABLE
        - ReconciliationRecord created
        """
        from unittest.mock import patch
        from backend.ehr.exceptions import EHRNetworkException

        idempotency_key = f"NET-FAIL-{uuid.uuid4()}"
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot2.id,
            patient_id=self.patient.id,
            chief_complaint="Acute wrist pain",
            idempotency_key=idempotency_key
        )

        with patch("backend.ehr.service.EHRIntegrationService.sync_appointment_to_ehr", side_effect=EHRNetworkException("Simulated socket disconnect")):
            appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        self.assertEqual(appt.status, "RECONCILIATION_REQUIRED")
        self.db.refresh(self.slot2)
        self.assertEqual(self.slot2.status, "AVAILABLE", "Slot must be released on network failure")

        recon = self.db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.idempotency_key == idempotency_key
        ).first()
        self.assertIsNotNone(recon)
        self.assertEqual(recon.status, "RECONCILIATION_REQUIRED")

    def test_unknown_external_outcome_never_reports_success_and_reconciles(self):
        """
        Verify that when an external API response is received but the mandatory external
        verification query returns NOT_FOUND (unknown external outcome), the system:
        - does NOT report success
        - does NOT mark appointment CONFIRMED
        - transitions to RECONCILIATION_REQUIRED
        - releases the slot hold
        """
        from unittest.mock import patch
        from backend.ehr.connector import EHRVerificationResult

        idempotency_key = f"UNKNOWN-OUTCOME-{uuid.uuid4()}"
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Post-op review",
            idempotency_key=idempotency_key
        )

        # Mock verify query returning found=False (unknown external outcome)
        unverified_result = EHRVerificationResult(
            found=False,
            status="NOT_FOUND",
            idempotency_key=idempotency_key,
            message="Record not verified in external system"
        )

        with patch("backend.ehr.service.EHRIntegrationService.verify_appointment_in_ehr", return_value=unverified_result):
            appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        self.assertNotEqual(appt.status, "CONFIRMED", "Must NEVER confirm without successful external verification!")
        self.assertEqual(appt.status, "RECONCILIATION_REQUIRED")
        self.db.refresh(self.slot1)
        self.assertEqual(self.slot1.status, "AVAILABLE", "Slot must be released back to AVAILABLE")

        recon = self.db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.idempotency_key == idempotency_key
        ).first()
        self.assertIsNotNone(recon)
        self.assertIn("NOT_FOUND", recon.failure_reason)

    def test_slot_validation_failure_scheduling_rules(self):
        """
        Verify that attempting to book a slot that violates scheduling rules
        (e.g., doctor inactive or slot blocked) is rejected with 409 conflict.
        """
        # Block the doctor during slot1
        blocked = models.BlockedSlot(
            id=f"block-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            calendar_id=self.calendar.id,
            start_time=self.slot1.start_time,
            end_time=self.slot1.end_time,
            reason="Emergency Surgery"
        )
        self.db.add(blocked)
        self.db.commit()

        from fastapi import HTTPException
        req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot1.id,
            patient_id=self.patient.id,
            chief_complaint="Routine check",
            idempotency_key=f"BLOCK-TEST-{uuid.uuid4()}"
        )

        with self.assertRaises(HTTPException) as cm:
            asyncio.run(AppointmentService.confirm_and_sync_appointment(self.db, req))

        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("blocked", cm.exception.detail.lower())

    def test_ai_agent_revalidates_slot_and_handles_unavailable_slot(self):
        """
        Verify AI conversational revalidation:
        When a slot is booked by someone else between recommendation and confirmation,
        the AI detects it immediately before booking, does NOT confirm, and offers alternate slots.
        """
        session_id = f"ai-sess-reval-{uuid.uuid4().hex[:8]}"

        # Turn 1: Discover slots
        res1 = asyncio.run(AIPatientAccessAgent.process_turn(
            db=self.db,
            session_id=session_id,
            message="Book an appointment with Dr. Marcus Welby",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))
        self.assertIn("check_availability", res1.capabilities_executed)
        self.assertIn("Option 1", res1.reply)

        # Simulate concurrent user booking slot1 before turn 2
        self.slot1.status = "BOOKED"
        self.db.commit()

        # Turn 2: Patient tries to pick Option 1 (now unavailable)
        res2 = asyncio.run(AIPatientAccessAgent.process_turn(
            db=self.db,
            session_id=session_id,
            message="Option 1 please",
            patient_id=self.patient.id,
            hospital_id=self.hospital.id
        ))

        # Must NOT book or confirm the unavailable slot
        self.assertNotIn("create_appointment", res2.capabilities_executed)
        self.assertTrue(res2.needs_clarification)
        self.assertTrue(
            "no longer available" in res2.reply or "just booked" in res2.reply,
            f"Expected unavailability notice in reply: {res2.reply}"
        )


if __name__ == "__main__":
    unittest.main()

