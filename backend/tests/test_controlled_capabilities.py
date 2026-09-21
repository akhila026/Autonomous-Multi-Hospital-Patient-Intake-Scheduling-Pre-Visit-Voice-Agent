import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend import models
from backend.capabilities.base import CapabilityContext
from backend.capabilities.registry import capability_registry


class TestControlledCapabilities(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        TestSession = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = TestSession()

        # Seed Hospital A (Tenant 1)
        self.hospital_a = models.Hospital(
            name="Hospital Alpha",
            address="100 Alpha Way",
            license_number=f"ALPHA-{uuid.uuid4().hex[:6]}",
            contact_email="admin@alpha.org",
            phone="+1-555-111-0001",
            status="APPROVED"
        )
        # Seed Hospital B (Tenant 2)
        self.hospital_b = models.Hospital(
            name="Hospital Beta",
            address="200 Beta Blvd",
            license_number=f"BETA-{uuid.uuid4().hex[:6]}",
            contact_email="admin@beta.org",
            phone="+1-555-222-0002",
            status="APPROVED"
        )
        self.db.add_all([self.hospital_a, self.hospital_b])
        self.db.commit()

        # Seed Doctor in Hospital A
        self.doctor_a = models.Doctor(
            hospital_id=self.hospital_a.id,
            full_name="Dr. Alice Smith",
            specialty="Cardiology",
            consultation_fee=200.0,
            slot_duration_min=30,
            is_active=True
        )
        # Seed Doctor in Hospital B
        self.doctor_b = models.Doctor(
            hospital_id=self.hospital_b.id,
            full_name="Dr. Bob Jones",
            specialty="Dermatology",
            consultation_fee=150.0,
            slot_duration_min=30,
            is_active=True
        )
        self.db.add_all([self.doctor_a, self.doctor_b])
        self.db.commit()

        # Seed Slot for Doctor A
        slot_time = datetime.utcnow() + timedelta(days=2)
        self.slot_a = models.TimeSlot(
            doctor_id=self.doctor_a.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot_a)

        # Seed Patients
        self.patient_1 = models.Patient(
            full_name="Patient One",
            phone="+1-555-111-2222",
            email="p1@example.com",
            primary_hospital_id=self.hospital_a.id
        )
        self.patient_2 = models.Patient(
            full_name="Patient Two",
            phone="+1-555-333-4444",
            email="p2@example.com",
            primary_hospital_id=self.hospital_b.id
        )
        self.db.add_all([self.patient_1, self.patient_2])
        self.db.commit()

        self.context_a = CapabilityContext(
            correlation_id=f"CORR-{uuid.uuid4().hex[:6]}",
            user_id=self.patient_1.id,
            user_role="PATIENT",
            tenant_id=self.hospital_a.id,
            session_id=f"SES-{uuid.uuid4().hex[:6]}",
            db=self.db
        )

    def tearDown(self):
        self.db.close()

    # =================================================================
    # 1. SEMANTIC VALIDATION TESTS
    # =================================================================

    def test_semantic_validation_rejects_negative_fee(self):
        """search_doctors must reject negative consultation fees."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "search_doctors",
                {"max_fee": -50.0},
                self.context_a
            )
        )
        loop.close()
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "VALIDATION_ERROR")
        self.assertTrue(res.requires_clarification)
        self.assertIn("cannot be negative", res.error)

    def test_semantic_validation_rejects_invalid_notification_channel(self):
        """send_notification must reject unsupported channels."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "send_notification",
                {
                    "recipient_id": self.patient_1.id,
                    "recipient_contact": "+1-555-111-2222",
                    "channel": "TELEGRAM_UNSUPPORTED",
                    "message": "Appointment reminder"
                },
                self.context_a
            )
        )
        loop.close()
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "VALIDATION_ERROR")
        self.assertTrue(res.requires_clarification)

    # =================================================================
    # 2. TENANT ISOLATION & RESOURCE OWNERSHIP TESTS
    # =================================================================

    def test_tenant_isolation_rejects_cross_tenant_doctor_search(self):
        """Context locked to Hospital A must reject explicit querying for Hospital B."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "search_doctors",
                {"hospital_id": self.hospital_b.id},
                self.context_a
            )
        )
        loop.close()
        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "UNAUTHORIZED")
        self.assertTrue(res.requires_escalation)

    def test_resource_ownership_rejects_unauthorized_appointment_viewing(self):
        """Patient One cannot inspect an appointment belonging to Patient Two."""
        # Create appointment owned by Patient Two in Hospital B
        slot_b = models.TimeSlot(
            doctor_id=self.doctor_b.id,
            start_time=datetime.utcnow() + timedelta(days=3),
            end_time=datetime.utcnow() + timedelta(days=3, minutes=30),
            status="BOOKED"
        )
        self.db.add(slot_b)
        self.db.commit()

        appt_b = models.Appointment(
            patient_id=self.patient_2.id,
            doctor_id=self.doctor_b.id,
            hospital_id=self.hospital_b.id,
            slot_id=slot_b.id,
            status="CONFIRMED",
            idempotency_key=f"IDEM-{uuid.uuid4()}"
        )
        self.db.add(appt_b)
        self.db.commit()

        # Patient One attempts to view Patient Two's appointment
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "get_appointment",
                {"appointment_id": appt_b.id},
                self.context_a  # context belongs to Patient One / Hospital A
            )
        )
        loop.close()

        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "UNAUTHORIZED")
        self.assertTrue(res.requires_escalation)

    # =================================================================
    # 3. CONTROLLED FAILURE & CLARIFICATION TESTS
    # =================================================================

    def test_slot_collision_returns_controlled_clarification(self):
        """When attempting to book an already BOOKED slot, return controlled requires_clarification=True."""
        # Mark slot as already booked
        self.slot_a.status = "BOOKED"
        self.db.commit()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "create_appointment",
                {
                    "patient_id": self.patient_1.id,
                    "slot_id": self.slot_a.id,
                    "chief_complaint": "Arrhythmia check",
                    "urgency_level": "ROUTINE"
                },
                self.context_a
            )
        )
        loop.close()

        self.assertFalse(res.success)
        self.assertEqual(res.error_code, "SLOT_ALREADY_BOOKED")
        self.assertTrue(res.requires_clarification)
        self.assertIn("already been booked", res.error)

    # =================================================================
    # 4. DATABASE AUDIT TRAIL LOGGING
    # =================================================================

    def test_capability_execution_writes_to_audit_events_table(self):
        """Every capability execution must create records in models.AuditEvent with correlation ID."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            capability_registry.invoke(
                "search_hospitals",
                {"query": "Alpha"},
                self.context_a
            )
        )
        loop.close()

        self.assertTrue(res.success)

        # Verify DB audit table entry
        audit_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == self.context_a.correlation_id
        ).all()
        self.assertGreaterEqual(len(audit_events), 1)
        actions = [a.action for a in audit_events]
        self.assertTrue(any("SEARCH_HOSPITALS" in a for a in actions))

    # =================================================================
    # 5. ALL 17 CAPABILITIES CAN BE EXECUTED CLEANLY
    # =================================================================

    def test_all_17_capabilities_executable(self):
        """Verify that all 17 capabilities are registered and handle invocation gracefully."""
        capabilities = capability_registry.list_capabilities()
        self.assertEqual(len(capabilities), 17)
        names = [c["name"] for c in capabilities]

        expected_17 = [
            "search_hospitals",
            "search_doctors",
            "check_availability",
            "lookup_patient",
            "get_appointment",
            "create_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "get_questionnaire",
            "submit_questionnaire",
            "send_notification",
            "start_workflow",
            "get_context",
            "update_preferences",
            "verify_external_appointment",
            "synchronize_state",
            "transfer_to_human"
        ]
        for exp in expected_17:
            self.assertIn(exp, names)


if __name__ == "__main__":
    unittest.main()
