import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.database import SessionLocal, engine, Base
from backend import models, schemas
from backend.services.mock_ehr_service import MockEhrService
from backend.services.appointment_service import AppointmentService
from backend.services.slot_service import SlotService
from backend.ehr.connector import mock_ehr_connector
from backend.main import app

class TestEhrTimeoutRecovery(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        self.test_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.test_engine)
        TestSession = sessionmaker(autocommit=False, autoflush=False, bind=self.test_engine)
        self.db = TestSession()

        # Create isolated test hospital & doctor
        self.hospital = models.Hospital(
            name=f"Test Hospital {uuid.uuid4().hex[:6]}",
            address="100 Test St",
            license_number=f"TEST-LIC-{uuid.uuid4().hex[:8]}",
            contact_email="test@hospital.com",
            phone="+1-555-999-0000",
            status="APPROVED"
        )
        self.db.add(self.hospital)
        self.db.commit()

        self.doctor = models.Doctor(
            hospital_id=self.hospital.id,
            full_name="Dr. Test Ortho",
            specialty="Orthopedics",
            consultation_fee=150.0,
            slot_duration_min=30,
            is_active=True
        )
        self.db.add(self.doctor)
        self.db.commit()

        # Create test slot
        slot_time = datetime.utcnow() + timedelta(days=2)
        self.slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        # Create test patient
        self.patient = models.Patient(
            full_name="Bob Test",
            phone="+1-555-123-4567",
            email=f"bob.{uuid.uuid4().hex[:6]}@test.com"
        )
        self.db.add(self.patient)
        self.db.commit()

    def tearDown(self):
        # Reset chaos config to normal
        MockEhrService.update_chaos_setting(
            self.db,
            schemas.ChaosConfigUpdate(
                mode="NORMAL",
                simulated_delay_ms=2500,
                failure_active=False,
                one_shot=False
            )
        )
        self.db.close()

    def test_mock_ehr_timeout_and_idempotent_recovery(self):
        """
        Scenario A:
        1. Mock EHR is configured with mode='TIMEOUT_AFTER_SAVE'.
           (EHR records the appointment in external DB, but network socket drops/times out).
        2. Main application catches timeout and classifies outcome as UNKNOWN_OUTCOME.
        3. Main app does NOT blindly retry creation.
        4. Main app queries GET/POST /mock-ehr/verify?idempotency_key=...
        5. Verification determines actual external state: record exists!
        6. Internal appointment status is reconciled to CONFIRMED with the external EHR ID.
        7. Audit events are recorded for each step.
        8. EXACTLY ONE record exists in external EHR (zero duplicates).
        """
        # Configure Chaos: TIMEOUT_AFTER_SAVE
        MockEhrService.update_chaos_setting(
            self.db,
            schemas.ChaosConfigUpdate(
                mode="TIMEOUT_AFTER_SAVE",
                simulated_delay_ms=100,  # Fast for unit test
                failure_active=True,
                one_shot=False
            )
        )

        test_idempotency_key = f"TEST-KEY-{uuid.uuid4()}"
        test_correlation_id = f"CORR-{uuid.uuid4().hex[:8]}"

        confirm_req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot.id,
            patient_id=self.patient.id,
            chief_complaint="Twisted ankle while hiking",
            urgency_level="ROUTINE",
            ai_triage_notes="Suspected ankle sprain; Orthopedic review needed",
            idempotency_key=test_idempotency_key,
            correlation_id=test_correlation_id
        )

        # Execute booking
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        appt = loop.run_until_complete(
            AppointmentService.confirm_and_sync_appointment(self.db, confirm_req)
        )
        loop.close()

        # Assertions on final appointment and slot
        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "CONFIRMED", "Appointment should be confirmed after recovery reconciliation!")
        self.assertIsNotNone(appt.ehr_appointment_id, "EHR appointment ID should be populated from external verification!")
        self.assertTrue(appt.ehr_appointment_id.startswith("EHR-"), "EHR ID format check")

        refreshed_slot = self.db.query(models.TimeSlot).filter(models.TimeSlot.id == self.slot.id).first()
        self.assertEqual(refreshed_slot.status, "BOOKED")

        # Verify Zero Duplicates in External EHR
        verif = mock_ehr_connector.verify_appointment_sync(test_idempotency_key)
        self.assertTrue(verif.found, "External EHR must have verified record")
        self.assertEqual(verif.ehr_appointment_id, appt.ehr_appointment_id)

        all_records = [r for r in mock_ehr_connector.list_records() if r.get("idempotency_key") == test_idempotency_key]
        self.assertEqual(len(all_records), 1, "Duplicate bookings must NOT be created in external EHR!")

        # Verify Immutable Audit Events Chain
        audit_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == test_correlation_id
        ).order_by(models.AuditEvent.created_at.asc()).all()

        actions = [a.action for a in audit_events]
        self.assertIn("EXTERNAL_TIMEOUT_DETECTED", actions)
        self.assertIn("QUERY_EXTERNAL_STATE", actions)
        self.assertIn("DETERMINE_EXTERNAL_STATE", actions)
        self.assertIn("APPOINTMENT_CONFIRMED", actions)

        timeout_event = next(a for a in audit_events if a.action == "EXTERNAL_TIMEOUT_DETECTED")
        self.assertEqual(timeout_event.status, "UNKNOWN_OUTCOME", "Application must classify timeout as UNKNOWN outcome!")

        state_event = next(a for a in audit_events if a.action == "DETERMINE_EXTERNAL_STATE")
        self.assertEqual(state_event.status, "RECORD_EXISTS")

        confirm_event = next(a for a in audit_events if a.action == "APPOINTMENT_CONFIRMED")
        self.assertEqual(confirm_event.status, "RECONCILED_WITHOUT_DUPLICATE")

    def test_mock_ehr_timeout_before_save_safe_retry(self):
        """
        Scenario B:
        1. Mock EHR is configured with mode='TIMEOUT_BEFORE_SAVE' and one_shot=True.
           (Network drops before record is saved in EHR).
        2. Main app catches timeout and classifies outcome as UNKNOWN_OUTCOME.
        3. Main app does NOT blindly retry.
        4. Main app queries EHR: determines record does NOT exist (RECORD_NOT_FOUND).
        5. Main app executes safe retry with idempotency key.
        6. Retry succeeds, result is verified, and internal state is synchronized.
        7. Exactly one record in external EHR.
        """
        MockEhrService.update_chaos_setting(
            self.db,
            schemas.ChaosConfigUpdate(
                mode="TIMEOUT_BEFORE_SAVE",
                simulated_delay_ms=100,
                failure_active=True,
                one_shot=True  # Allows retry to succeed deterministically
            )
        )

        test_idempotency_key = f"TEST-RETRY-{uuid.uuid4()}"
        test_correlation_id = f"CORR-RETRY-{uuid.uuid4().hex[:8]}"

        # Create fresh slot
        slot_time = datetime.utcnow() + timedelta(days=3)
        new_slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(new_slot)
        self.db.commit()

        confirm_req = schemas.AppointmentConfirmRequest(
            slot_id=new_slot.id,
            patient_id=self.patient.id,
            chief_complaint="Persistent lower back pain",
            urgency_level="ROUTINE",
            ai_triage_notes="Retry resiliency test",
            idempotency_key=test_idempotency_key,
            correlation_id=test_correlation_id
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        appt = loop.run_until_complete(
            AppointmentService.confirm_and_sync_appointment(self.db, confirm_req)
        )
        loop.close()

        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "CONFIRMED")
        self.assertEqual(appt.retry_count, 1, "Appointment should have executed exactly 1 safe retry")
        self.assertIsNotNone(appt.ehr_appointment_id)

        # Audit events check
        audit_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == test_correlation_id
        ).order_by(models.AuditEvent.created_at.asc()).all()

        actions = [a.action for a in audit_events]
        self.assertIn("EXTERNAL_TIMEOUT_DETECTED", actions)
        self.assertIn("QUERY_EXTERNAL_STATE", actions)
        self.assertIn("DETERMINE_EXTERNAL_STATE", actions)
        self.assertIn("RETRY_APPOINTMENT_CREATION", actions)
        self.assertIn("APPOINTMENT_CONFIRMED", actions)

        state_event = next(a for a in audit_events if a.action == "DETERMINE_EXTERNAL_STATE")
        self.assertEqual(state_event.status, "RECORD_NOT_FOUND")

        confirm_event = next(a for a in audit_events if a.action == "APPOINTMENT_CONFIRMED")
        self.assertEqual(confirm_event.status, "RETRY_VERIFIED_SUCCESS")

        # Zero duplicate check
        all_records = [r for r in mock_ehr_connector.list_records() if r.get("idempotency_key") == test_idempotency_key]
        self.assertEqual(len(all_records), 1)

    def test_mock_ehr_unresolved_outage_creates_reconciliation(self):
        """
        Scenario C:
        1. Mock EHR is in persistent outage (mode='OUTAGE', failure_active=True).
        2. Outcome remains unresolved after attempt and retry.
        3. Main app creates a ReconciliationRecord.
        4. Main app marks appointment as RECONCILIATION_REQUIRED.
        5. Main app releases the slot hold so calendar is not locked.
        6. Main app does NOT confirm appointment to patient.
        7. Escalation audit event is recorded.
        """
        MockEhrService.update_chaos_setting(
            self.db,
            schemas.ChaosConfigUpdate(
                mode="OUTAGE",
                simulated_delay_ms=100,
                failure_active=True,
                one_shot=False
            )
        )

        test_idempotency_key = f"TEST-OUTAGE-{uuid.uuid4()}"
        test_correlation_id = f"CORR-OUTAGE-{uuid.uuid4().hex[:8]}"

        slot_time = datetime.utcnow() + timedelta(days=4)
        outage_slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(outage_slot)
        self.db.commit()

        confirm_req = schemas.AppointmentConfirmRequest(
            slot_id=outage_slot.id,
            patient_id=self.patient.id,
            chief_complaint="Shoulder impingement",
            urgency_level="ROUTINE",
            ai_triage_notes="Persistent failure test",
            idempotency_key=test_idempotency_key,
            correlation_id=test_correlation_id
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        appt = loop.run_until_complete(
            AppointmentService.confirm_and_sync_appointment(self.db, confirm_req)
        )
        loop.close()

        # Must not be confirmed!
        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "RECONCILIATION_REQUIRED")
        self.assertIsNone(appt.ehr_appointment_id)

        # Slot must be released to prevent calendar lock
        refreshed_slot = self.db.query(models.TimeSlot).filter(models.TimeSlot.id == outage_slot.id).first()
        self.assertEqual(refreshed_slot.status, "AVAILABLE")

        # ReconciliationRecord must be created
        recon = self.db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.appointment_id == appt.id
        ).first()
        self.assertIsNotNone(recon, "ReconciliationRecord must be created for unresolved failures")
        self.assertEqual(recon.status, "RECONCILIATION_REQUIRED")

        # Audit Event for escalation
        audit_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == test_correlation_id
        ).all()
        recon_event = next((a for a in audit_events if a.action == "CREATE_RECONCILIATION_RECORD"), None)
        self.assertIsNotNone(recon_event)
        self.assertEqual(recon_event.status, "ESCALATION_TRIGGERED")

    def test_demo_endpoint_simulate_timeout_recovery(self):
        """
        Tests the HTTP demo endpoint POST /api/v1/mock-ehr/demo/simulate-timeout-recovery
        """
        client = TestClient(app)

        # 1. Test TIMEOUT_AFTER_SAVE demo
        res = client.post("/api/v1/mock-ehr/demo/simulate-timeout-recovery", json={
            "scenario": "TIMEOUT_AFTER_SAVE",
            "simulated_delay_ms": 100
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["scenario"], "TIMEOUT_AFTER_SAVE")
        self.assertEqual(data["classification"], "UNKNOWN_OUTCOME")
        self.assertEqual(data["final_appointment_status"], "CONFIRMED")
        self.assertEqual(data["final_slot_status"], "BOOKED")
        self.assertEqual(data["external_duplicate_count"], 1)
        self.assertTrue(data["duplicate_prevented"])
        self.assertGreaterEqual(len(data["timeline"]), 6)
        self.assertGreaterEqual(len(data["audit_events"]), 4)

        # 2. Test UNRESOLVED_OUTAGE demo
        res2 = client.post("/api/v1/mock-ehr/demo/simulate-timeout-recovery", json={
            "scenario": "UNRESOLVED_OUTAGE",
            "simulated_delay_ms": 100
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["scenario"], "UNRESOLVED_OUTAGE")
        self.assertEqual(data2["final_appointment_status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(data2["final_slot_status"], "AVAILABLE")
        self.assertIsNotNone(data2["reconciliation_record"])

if __name__ == "__main__":
    unittest.main()
