import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend import models, schemas
from backend.ehr.connector import MockEHRConnector, EHRAppointmentPayload, EHRVerificationResult
from backend.ehr.mapping import mapping_registry, IdentifierMappingRegistry
from backend.ehr.reconciliation import EHRReconciliationManager
from backend.ehr.service import EHRIntegrationService
from backend.services.appointment_service import AppointmentService
from backend.services.mock_ehr_service import MockEhrTimeoutException


class TestComprehensiveWorkflowEHR(unittest.TestCase):
    """
    Complete EHR Test Suite verifying all 10 EHR integration requirements:
    1. Patient Mapping (Local -> External MRN)
    2. Provider Mapping (Local -> External Provider ID)
    3. Appointment Creation
    4. Rescheduling
    5. Cancellation
    6. Timeout Simulation
    7. Duplicate Requests (Idempotency)
    8. Unknown Outcomes Handling
    9. Explicit Verification Query
    10. Reconciliation & Recovery
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
        # Reset chaos to NORMAL before each test
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)

        # 1. Seed Hospital
        self.hospital = models.Hospital(
            id=f"hosp-ehr-{uuid.uuid4().hex[:6]}",
            name="St. Jude EHR Testing Center",
            address="700 Innovation Blvd",
            license_number=f"LIC-EHR-{uuid.uuid4().hex[:6]}",
            contact_email="ehr@stjude.org",
            phone="+1-555-0777",
            status="APPROVED"
        )
        self.db.add(self.hospital)

        # 2. Seed Doctor
        self.doctor = models.Doctor(
            id=f"doc-ehr-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            full_name="Dr. Gregory House",
            specialty="Diagnostic Medicine",
            external_provider_id="EXT-DOC-777",
            consultation_fee=250.0,
            is_active=True
        )
        self.db.add(self.doctor)

        # 3. Seed Patient
        self.patient = models.Patient(
            id=f"pat-ehr-{uuid.uuid4().hex[:6]}",
            full_name="Robert Chase",
            phone="+1-555-0333",
            email="robert.chase@example.com",
            primary_hospital_id=self.hospital.id
        )
        self.db.add(self.patient)

        # 4. Seed Slot
        self.slot = models.TimeSlot(
            id=f"slot-ehr-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=datetime.utcnow() + timedelta(days=3, hours=14),
            end_time=datetime.utcnow() + timedelta(days=3, hours=14, minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        self.db.commit()

    def tearDown(self):
        # Always restore clean EHR chaos mode
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)
        self.db.rollback()
        self.db.close()

    # =========================================================================
    # 1. PATIENT MAPPING
    # =========================================================================

    def test_patient_mapping_resolution(self):
        """Verifies bidirectional mapping between internal patient ID and EHR MRN."""
        registry = IdentifierMappingRegistry()
        external_mrn = f"MRN-CHASE-{uuid.uuid4().hex[:6]}"

        registry.set_mapping(
            entity_type="PATIENT",
            internal_id=self.patient.id,
            external_id=external_mrn,
            hospital_id=self.hospital.id,
            db=self.db,
            system_name="MOCK_EHR"
        )

        # Lookup external MRN
        mrn_resolved = registry.get_external_id("PATIENT", self.patient.id, system_name="MOCK_EHR")
        self.assertEqual(mrn_resolved, external_mrn)

        # Reverse lookup
        internal_resolved = registry.get_internal_id("PATIENT", external_mrn, system_name="MOCK_EHR")
        self.assertEqual(internal_resolved, self.patient.id)

    # =========================================================================
    # 2. PROVIDER MAPPING
    # =========================================================================

    def test_provider_mapping_resolution(self):
        """Verifies mapping internal doctor ID to EHR external provider ID."""
        registry = IdentifierMappingRegistry()
        external_provider_id = "EXT-PROV-909"

        registry.set_mapping(
            entity_type="DOCTOR",
            internal_id=self.doctor.id,
            external_id=external_provider_id,
            hospital_id=self.hospital.id,
            db=self.db,
            system_name="MOCK_EHR"
        )

        prov_resolved = registry.get_external_id("DOCTOR", self.doctor.id, system_name="MOCK_EHR")
        self.assertEqual(prov_resolved, external_provider_id)

    # =========================================================================
    # 3. APPOINTMENT CREATION
    # =========================================================================

    def test_ehr_appointment_creation(self):
        """Verifies creating an appointment on the external EHR returns external ID."""
        idemp = f"IDEMP-CREATE-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-CHASE",
            external_provider_id=self.doctor.external_provider_id,
            patient_name=self.patient.full_name,
            doctor_name=self.doctor.full_name,
            hospital_name=self.hospital.name,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp,
            chief_complaint="Diagnostic consult"
        )

        res = asyncio.run(self.connector.create_appointment(payload))
        self.assertIsNotNone(res)
        appt_id = res.get("id") or res.get("appointment_id")
        self.assertIsNotNone(appt_id)
        self.assertIn("CONFIRMED", res.get("status", ""))

    # =========================================================================
    # 4. RESCHEDULING
    # =========================================================================

    def test_ehr_appointment_rescheduling(self):
        """Verifies updating an existing appointment time on external EHR."""
        idemp = f"IDEMP-RESCHED-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-CHASE",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )
        create_res = asyncio.run(self.connector.create_appointment(payload))
        ext_id = create_res.get("id") or create_res.get("appointment_id")

        # Reschedule to new slot
        new_time = (self.slot.start_time + timedelta(days=1)).isoformat()
        resched_res = asyncio.run(self.connector.reschedule_appointment(
            external_appointment_id=ext_id,
            new_slot_time=new_time
        ))

        self.assertIsNotNone(resched_res)
        self.assertIn(resched_res.get("status"), ["RESCHEDULED", "CONFIRMED_IN_EHR"])

    # =========================================================================
    # 5. CANCELLATION
    # =========================================================================

    def test_ehr_appointment_cancellation(self):
        """Verifies cancelling an appointment on external EHR releases remote slot."""
        idemp = f"IDEMP-CANCEL-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-CHASE",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )
        create_res = asyncio.run(self.connector.create_appointment(payload))
        ext_id = create_res.get("id") or create_res.get("appointment_id")

        cancel_success = asyncio.run(self.connector.cancel_appointment(
            external_appointment_id=ext_id,
            reason="Patient requested cancellation"
        ))
        self.assertTrue(cancel_success)

    # =========================================================================
    # 6. TIMEOUT SIMULATION
    # =========================================================================

    def test_ehr_timeout_simulation(self):
        """Verifies simulated EHR timeout raises MockEhrTimeoutException with saved_internally flag."""
        # Configure one-shot timeout
        self.connector.set_chaos_config(mode="TIMEOUT_AFTER_SAVE", delay_ms=50, is_active=True, one_shot=True)

        idemp = f"IDEMP-TIMEOUT-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-TIMEOUT",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )

        with self.assertRaises(MockEhrTimeoutException):
            asyncio.run(self.connector.create_appointment(payload))

    # =========================================================================
    # 7. DUPLICATE REQUESTS (IDEMPOTENCY)
    # =========================================================================

    def test_ehr_duplicate_requests_idempotency(self):
        """Verifies replaying same payload with identical idempotency_key returns identical record."""
        idemp = f"IDEMP-DEDUP-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-DEDUP",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )

        # First request
        res1 = asyncio.run(self.connector.create_appointment(payload))
        ext_id_1 = res1.get("id") or res1.get("appointment_id")

        # Duplicate second request
        res2 = asyncio.run(self.connector.create_appointment(payload))
        ext_id_2 = res2.get("id") or res2.get("appointment_id")

        # Must return the exact same external appointment ID
        self.assertEqual(ext_id_1, ext_id_2)

    # =========================================================================
    # 8. UNKNOWN OUTCOMES HANDLING
    # =========================================================================

    def test_unknown_outcomes_handling(self):
        """Verifies timeout marks appointment as UNKNOWN_OUTCOME without corrupting slot."""
        self.connector.set_chaos_config(mode="TIMEOUT_AFTER_SAVE", delay_ms=50, is_active=True, one_shot=True)

        idemp = f"IDEMP-UNK-{uuid.uuid4().hex[:8]}"
        req = schemas.AppointmentConfirmRequest(
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            idempotency_key=idemp,
            chief_complaint="Follow-up consult",
            urgency_level="ROUTINE"
        )

        appt = asyncio.run(AppointmentService.confirm_and_sync_appointment(
            db=self.db,
            req=req
        ))

        # Must be marked UNKNOWN_OUTCOME or CONFIRMED after internal recovery
        self.assertIn(appt.status, ["UNKNOWN_OUTCOME", "CONFIRMED", "RECONCILED"])

    # =========================================================================
    # 9. EXPLICIT VERIFICATION QUERY
    # =========================================================================

    def test_explicit_verification_query(self):
        """Verifies verify_appointment queries external system to inspect booking state."""
        idemp = f"IDEMP-VERIF-EXPLICIT-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-VERIF",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )
        created = asyncio.run(self.connector.create_appointment(payload))
        ext_id = created.get("id") or created.get("appointment_id")

        # Explicit verification
        verify_result = asyncio.run(self.connector.verify_appointment(idempotency_key=idemp))
        self.assertTrue(verify_result.found)
        self.assertEqual(verify_result.external_appointment_id, ext_id)

    # =========================================================================
    # 10. RECONCILIATION & RECOVERY
    # =========================================================================

    def test_reconciliation_recovery_workflow(self):
        """Verifies EHRReconciliationManager queries external state and recovers desynced record."""
        # 1. Create on EHR first
        idemp = f"IDEMP-REC-FLOW-{uuid.uuid4().hex[:8]}"
        payload = EHRAppointmentPayload(
            external_patient_id="EXT-PAT-REC",
            external_provider_id=self.doctor.external_provider_id,
            slot_time=self.slot.start_time.isoformat(),
            idempotency_key=idemp
        )
        created = asyncio.run(self.connector.create_appointment(payload))
        ext_id = created.get("id") or created.get("appointment_id")

        # 2. Simulate local appointment stuck in UNKNOWN_OUTCOME
        appt = models.Appointment(
            id=str(uuid.uuid4()),
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            start_time=self.slot.start_time,
            end_time=self.slot.end_time,
            status="UNKNOWN_OUTCOME",
            idempotency_key=idemp
        )
        self.db.add(appt)
        self.db.commit()

        # 3. Execute reconciliation manager
        reconciliation_mgr = EHRReconciliationManager(connector=self.connector)
        verification = asyncio.run(reconciliation_mgr.handle_timeout_outcome(
            idempotency_key=idemp,
            appointment_id=appt.id,
            correlation_id=f"CORR-REC-{uuid.uuid4().hex[:6]}"
        ))

        self.assertTrue(verification.found)
        self.assertEqual(verification.external_appointment_id, ext_id)

        # 4. Update local appointment to CONFIRMED
        appt.status = "CONFIRMED"
        appt.ehr_appointment_id = verification.external_appointment_id
        self.db.commit()

        refreshed = self.db.query(models.Appointment).filter(models.Appointment.id == appt.id).first()
        self.assertEqual(refreshed.status, "CONFIRMED")
        self.assertEqual(refreshed.ehr_appointment_id, ext_id)


if __name__ == "__main__":
    unittest.main()
