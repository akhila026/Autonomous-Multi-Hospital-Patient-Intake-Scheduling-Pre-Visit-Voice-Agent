import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend import models, schemas
from backend.ehr.connector import (
    HealthcareSystemConnector,
    MockEHRConnector,
    HealthcareSystemConnectorRegistry,
    EHRAppointmentPayload,
    EHRVerificationResult,
    mock_ehr_connector,
    connector_registry
)
from backend.ehr.mapping import mapping_registry, IdentifierMappingRegistry
from backend.ehr.service import EHRIntegrationService
from backend.services.appointment_service import AppointmentService
from backend.capabilities.registry import capability_registry


class DummyEpicConnector(HealthcareSystemConnector):
    """Demonstration connector showing how Epic/Cerner/FHIR can plug in seamlessly."""
    async def lookup_patient(self, identifier: str, **kwargs):
        return {"id": "EPIC-PAT-999", "mrn": identifier, "system": "EPIC"}

    async def lookup_provider(self, provider_id: str, **kwargs):
        return {"id": provider_id, "npi": "1234567890", "system": "EPIC"}

    async def lookup_facility(self, facility_id: str, **kwargs):
        return {"id": facility_id, "facility_code": "EPIC-FAC-01"}

    async def lookup_calendar(self, calendar_id: str, **kwargs):
        return {"id": calendar_id, "active": True}

    async def check_availability(self, provider_id: str, start_date: str = None, days_ahead: int = 7, **kwargs):
        return [{"slot_time": "2026-11-01 10:00", "available": True}]

    async def create_appointment(self, payload: EHRAppointmentPayload, **kwargs):
        return {"id": f"EPIC-APPT-{uuid.uuid4().hex[:6]}", "status": "CONFIRMED", "system": "EPIC"}

    async def update_appointment(self, appointment_id: str, updates: dict, **kwargs):
        return {"id": appointment_id, "status": "UPDATED"}

    async def cancel_appointment(self, appointment_id: str, reason: str = None, **kwargs):
        return {"id": appointment_id, "status": "CANCELLED"}

    async def reschedule_appointment(self, appointment_id: str, new_slot_time: str, **kwargs):
        return {"id": appointment_id, "new_slot_time": new_slot_time, "status": "RESCHEDULED"}

    async def get_appointment(self, appointment_id: str, **kwargs):
        return {"id": appointment_id, "status": "CONFIRMED"}

    async def verify_appointment(self, idempotency_key: str, **kwargs):
        return EHRVerificationResult(found=True, external_appointment_id="EPIC-APPT-VERIFIED", status="VERIFIED")


class TestEHRAbstraction(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        TestSession = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = TestSession()

        # Seed test hospital
        self.hospital = models.Hospital(
            name="Metropolitan General Hospital",
            address="500 Healthcare Blvd",
            license_number=f"METRO-{uuid.uuid4().hex[:6]}",
            contact_email="admin@metrohealth.org",
            phone="+1-555-444-1111",
            status="APPROVED"
        )
        self.db.add(self.hospital)
        self.db.commit()

        # Seed test doctor
        self.doctor = models.Doctor(
            hospital_id=self.hospital.id,
            full_name="Dr. Sarah Jenkins",
            specialty="Cardiology",
            consultation_fee=250.0,
            slot_duration_min=30,
            is_active=True
        )
        self.db.add(self.doctor)
        self.db.commit()

        # Seed test slot
        slot_time = datetime.utcnow() + timedelta(days=3)
        self.slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        # Seed test patient
        self.patient = models.Patient(
            full_name="David Miller",
            phone="+1-555-888-2222",
            email=f"david.{uuid.uuid4().hex[:6]}@example.com"
        )
        self.db.add(self.patient)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_all_11_operations_on_mock_ehr_connector(self):
        """
        Verify that MockEHRConnector supports all 11 required interface operations:
        - lookup_patient()
        - lookup_provider()
        - lookup_facility()
        - lookup_calendar()
        - check_availability()
        - create_appointment()
        - update_appointment()
        - cancel_appointment()
        - reschedule_appointment()
        - get_appointment()
        - verify_appointment()
        """
        connector = MockEHRConnector()
        from mock_ehr.seed import seed_mock_ehr
        seed_mock_ehr()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. lookup_patient
        p = loop.run_until_complete(connector.lookup_patient("P-101"))
        self.assertIsNotNone(p)
        self.assertEqual(p.get("id"), "P-101")

        # 2. lookup_provider
        prov = loop.run_until_complete(connector.lookup_provider("DOC-501"))
        self.assertIsNotNone(prov)
        self.assertEqual(prov.get("id"), "DOC-501")

        # 3. lookup_facility
        fac = loop.run_until_complete(connector.lookup_facility("FAC-101"))
        self.assertIsNotNone(fac)
        self.assertEqual(fac.get("id"), "FAC-101")

        # 4. lookup_calendar
        cal = loop.run_until_complete(connector.lookup_calendar("CAL-DOC-501"))
        self.assertIsNotNone(cal)
        self.assertEqual(cal.get("id"), "CAL-DOC-501")

        # 5. check_availability
        avail = loop.run_until_complete(connector.check_availability("DOC-501"))
        self.assertIsInstance(avail, list)
        self.assertGreater(len(avail), 0)

        # 6. create_appointment
        idempotency_key = f"TEST-IDEM-{uuid.uuid4()}"
        payload = EHRAppointmentPayload(
            idempotency_key=idempotency_key,
            patient_name="David Miller",
            doctor_name="Dr. Sarah Jenkins",
            hospital_name="Metropolitan General Hospital",
            slot_time="2026-10-15 14:00",
            chief_complaint="Chest tightness"
        )
        created = loop.run_until_complete(connector.create_appointment(payload))
        self.assertIsNotNone(created)
        appt_id = created.get("id") or created.get("external_appointment_id")
        self.assertIsNotNone(appt_id)

        # 7. get_appointment
        fetched = loop.run_until_complete(connector.get_appointment(appt_id))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.get("id"), appt_id)

        # 8. update_appointment
        updated = loop.run_until_complete(connector.update_appointment(appt_id, {"chief_complaint": "Follow-up ECG"}))
        self.assertIsNotNone(updated)

        # 9. reschedule_appointment
        resched = loop.run_until_complete(connector.reschedule_appointment(appt_id, "2026-10-16 11:00"))
        self.assertIsNotNone(resched)

        # 10. verify_appointment
        verif = loop.run_until_complete(connector.verify_appointment(idempotency_key))
        self.assertTrue(verif.found)
        self.assertEqual(verif.external_appointment_id, appt_id)

        # 11. cancel_appointment
        cancelled = loop.run_until_complete(connector.cancel_appointment(appt_id, reason="Patient request"))
        self.assertTrue(cancelled)

        loop.close()

    def test_bidirectional_identifier_mapping(self):
        """
        Verify bidirectional mapping registry for:
        - internal patient <-> external patient
        - internal doctor <-> external provider
        - internal appointment <-> external appointment
        - internal facility <-> external facility
        And verify database persistence in models.ExternalIdentifierMapping.
        """
        int_pat_id = f"PAT-{uuid.uuid4().hex[:6]}"
        ext_pat_id = "EXT-PAT-9001"

        int_doc_id = f"DOC-{uuid.uuid4().hex[:6]}"
        ext_doc_id = "EXT-PROV-7001"

        int_appt_id = f"APPT-{uuid.uuid4().hex[:6]}"
        ext_appt_id = "EXT-APPT-5001"

        int_fac_id = self.hospital.id
        ext_fac_id = "EXT-FAC-3001"

        # Register mappings
        mapping_registry.map_patient(int_pat_id, ext_pat_id, hospital_id=self.hospital.id, db=self.db)
        mapping_registry.map_doctor(int_doc_id, ext_doc_id, hospital_id=self.hospital.id, db=self.db)
        mapping_registry.map_appointment(int_appt_id, ext_appt_id, hospital_id=self.hospital.id, db=self.db)
        mapping_registry.map_facility(int_fac_id, ext_fac_id, hospital_id=self.hospital.id, db=self.db)

        # 1. Forward lookups (Internal -> External)
        self.assertEqual(mapping_registry.get_external_patient_id(int_pat_id, hospital_id=self.hospital.id, db=self.db), ext_pat_id)
        self.assertEqual(mapping_registry.get_external_doctor_id(int_doc_id, hospital_id=self.hospital.id, db=self.db), ext_doc_id)
        self.assertEqual(mapping_registry.get_external_appointment_id(int_appt_id, hospital_id=self.hospital.id, db=self.db), ext_appt_id)
        self.assertEqual(mapping_registry.get_external_facility_id(int_fac_id, hospital_id=self.hospital.id, db=self.db), ext_fac_id)

        # 2. Reverse lookups (External -> Internal)
        self.assertEqual(mapping_registry.get_internal_patient_id(ext_pat_id, hospital_id=self.hospital.id, db=self.db), int_pat_id)
        self.assertEqual(mapping_registry.get_internal_doctor_id(ext_doc_id, hospital_id=self.hospital.id, db=self.db), int_doc_id)
        self.assertEqual(mapping_registry.get_internal_appointment_id(ext_appt_id, hospital_id=self.hospital.id, db=self.db), int_appt_id)
        self.assertEqual(mapping_registry.get_internal_facility_id(ext_fac_id, hospital_id=self.hospital.id, db=self.db), int_fac_id)

        # 3. Verify Database Records
        db_mappings = self.db.query(models.ExternalIdentifierMapping).filter(
            models.ExternalIdentifierMapping.hospital_id == self.hospital.id
        ).all()
        self.assertEqual(len(db_mappings), 4)
        entity_types = {m.entity_type for m in db_mappings}
        self.assertEqual(entity_types, {"PATIENT", "DOCTOR", "APPOINTMENT", "FACILITY"})

    def test_pluggable_connector_registry(self):
        """
        Verify that Mock EHR connector can be swapped or augmented with real connectors
        (e.g., Epic/Cerner/FHIR) through the HealthcareSystemConnectorRegistry.
        """
        registry = HealthcareSystemConnectorRegistry()
        mock_conn = MockEHRConnector()
        epic_conn = DummyEpicConnector()

        registry.register("MOCK_EHR", mock_conn)
        registry.register("EPIC", epic_conn)

        # Lookup Epic connector
        resolved_epic = registry.get("EPIC")
        self.assertIsInstance(resolved_epic, DummyEpicConnector)

        # Lookup Mock EHR connector
        resolved_mock = registry.get("MOCK_EHR")
        self.assertIsInstance(resolved_mock, MockEHRConnector)

        # Default fallback
        resolved_default = registry.get("NON_EXISTENT")
        self.assertIsNotNone(resolved_default)

    def test_integration_service_records_operation_and_mapping(self):
        """
        Verify that EHRIntegrationService coordinates connector dispatch,
        bidirectional mapping creation, and IntegrationOperation audit logging.
        """
        test_idempotency_key = f"TEST-IDEM-{uuid.uuid4()}"
        confirm_req = schemas.AppointmentConfirmRequest(
            slot_id=self.slot.id,
            patient_id=self.patient.id,
            chief_complaint="Hypertension checkup",
            urgency_level="ROUTINE",
            idempotency_key=test_idempotency_key
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        appt = loop.run_until_complete(
            AppointmentService.confirm_and_sync_appointment(self.db, confirm_req)
        )
        loop.close()

        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "CONFIRMED")
        self.assertIsNotNone(appt.ehr_appointment_id)

        # Check IntegrationOperation entity in DB
        op = self.db.query(models.IntegrationOperation).filter(
            models.IntegrationOperation.appointment_id == appt.id
        ).first()
        self.assertIsNotNone(op)
        self.assertEqual(op.operation_type, "CREATE_APPOINTMENT")
        self.assertEqual(op.status, "SUCCESS")
        self.assertEqual(op.system_name, "MOCK_EHR")

        # Check ExternalIdentifierMapping in DB
        mapping = self.db.query(models.ExternalIdentifierMapping).filter(
            models.ExternalIdentifierMapping.internal_id == appt.id,
            models.ExternalIdentifierMapping.entity_type == "APPOINTMENT"
        ).first()
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.external_id, appt.ehr_appointment_id)

    def test_capabilities_registered(self):
        """
        Verify that all 17 controlled capabilities are registered in the registry.
        """
        capabilities = capability_registry.list_capabilities()
        cap_names = {c["name"] for c in capabilities}
        self.assertEqual(len(cap_names), 17)
        self.assertIn("create_appointment", cap_names)
        self.assertIn("verify_external_appointment", cap_names)
        self.assertIn("synchronize_state", cap_names)
        self.assertIn("check_availability", cap_names)
        self.assertIn("lookup_patient", cap_names)


if __name__ == "__main__":
    unittest.main()
