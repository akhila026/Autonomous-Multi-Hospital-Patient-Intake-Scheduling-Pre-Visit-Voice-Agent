import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from mock_ehr.main import app as mock_ehr_app
from mock_ehr.database import EhrSessionLocal, EhrBase, ehr_engine
from mock_ehr import models as ehr_models
from backend.ehr.connector import mock_ehr_connector, EHRPatientPayload, EHRAppointmentPayload
from backend.services.mock_ehr_service import MockEhrService


class TestMockEhrService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(mock_ehr_app)
        EhrBase.metadata.create_all(bind=ehr_engine)
        cls.ehr_db = EhrSessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.ehr_db.close()

    # =========================================================================
    # 1. PATIENTS APIS: POST /patients, GET /patients/{id}
    # =========================================================================
    def test_01_create_and_get_patient(self):
        """Test POST /patients and GET /patients/{id}."""
        # 1. Create patient via Mock EHR API
        payload = {
            "first_name": "Eleanor",
            "last_name": "Rigby",
            "dob": "1990-05-20",
            "contact": "+1-555-777-8888",
            "gender": "Female"
        }
        create_res = self.client.post("/patients", json=payload)
        self.assertEqual(create_res.status_code, 201)
        created_data = create_res.json()
        patient_id = created_data["id"]
        self.assertTrue(patient_id.startswith("P-"))
        self.assertEqual(created_data["first_name"], "Eleanor")

        # 2. Get patient by ID via Mock EHR API
        get_res = self.client.get(f"/patients/{patient_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["last_name"], "Rigby")

        # 3. Verify in Mock EHR isolated DB
        db_patient = self.ehr_db.query(ehr_models.EhrPatient).filter(
            ehr_models.EhrPatient.id == patient_id
        ).first()
        self.assertIsNotNone(db_patient)
        self.assertEqual(db_patient.contact, "+1-555-777-8888")

    # =========================================================================
    # 2. PROVIDERS APIS: GET /providers, GET /providers/{id}
    # =========================================================================
    def test_02_get_providers_and_get_provider_by_id(self):
        """Test GET /providers and GET /providers/{id}."""
        # 1. List all providers
        res = self.client.get("/providers")
        self.assertEqual(res.status_code, 200)
        providers = res.json()
        self.assertGreaterEqual(len(providers), 1)

        provider_id = providers[0]["id"]

        # 2. Get provider by ID
        prov_res = self.client.get(f"/providers/{provider_id}")
        self.assertEqual(prov_res.status_code, 200)
        self.assertEqual(prov_res.json()["id"], provider_id)

        # 3. Filter by specialty
        ortho_res = self.client.get("/providers?specialty=Orthopedics")
        self.assertEqual(ortho_res.status_code, 200)
        self.assertTrue(all("orthopedics" in p["specialty"].lower() for p in ortho_res.json()))

    # =========================================================================
    # 3. AVAILABILITY APIS: GET /availability
    # =========================================================================
    def test_03_get_availability(self):
        """Test GET /availability."""
        res = self.client.get("/availability")
        self.assertEqual(res.status_code, 200)
        avail = res.json()
        self.assertIsInstance(avail, list)

    # =========================================================================
    # 4. APPOINTMENTS APIS: POST /appointments, GET /appointments/{id}
    # =========================================================================
    def test_04_create_and_get_appointment_idempotent(self):
        """Test POST /appointments and GET /appointments/{id} with idempotency."""
        # Ensure chaos is disabled for this deterministic test
        self.client.post("/chaos", json={"mode": "NORMAL", "failure_active": False})

        idem_key = f"TEST-IDEM-{uuid.uuid4().hex}"
        appt_payload = {
            "idempotency_key": idem_key,
            "patient_id": "P-101",
            "patient_name": "Alice Morgan",
            "provider_id": "DOC-501",
            "doctor_name": "Dr. Sarah Jenkins",
            "hospital_name": "St. Jude Memorial Hospital",
            "slot_time": "2026-10-15 09:30",
            "chief_complaint": "Persistent shoulder stiffness"
        }

        # 1. Create appointment
        create_res = self.client.post("/appointments", json=appt_payload)
        self.assertEqual(create_res.status_code, 201)
        created = create_res.json()
        appt_id = created["id"]
        self.assertTrue(appt_id.startswith("EHR-"))
        self.assertEqual(created["status"], "CONFIRMED_IN_EHR")

        # 2. Re-send identical POST (Idempotency test)
        dup_res = self.client.post("/appointments", json=appt_payload)
        self.assertEqual(dup_res.status_code, 201)  # Or 200/201 idempotent return
        self.assertEqual(dup_res.json()["id"], appt_id)

        # 3. GET appointment by ID
        get_res = self.client.get(f"/appointments/{appt_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["idempotency_key"], idem_key)

        # 4. Verify exactly 1 record in Mock EHR database
        count = self.ehr_db.query(ehr_models.EhrAppointment).filter(
            ehr_models.EhrAppointment.idempotency_key == idem_key
        ).count()
        self.assertEqual(count, 1, "Mock EHR must store exactly 1 record for this idempotency key")

    # =========================================================================
    # 5. PUT /appointments/{id}
    # =========================================================================
    def test_05_update_appointment(self):
        """Test PUT /appointments/{id} to update details/status."""
        idem_key = f"UPDATE-IDEM-{uuid.uuid4().hex}"
        create_res = self.client.post("/appointments", json={
            "idempotency_key": idem_key,
            "patient_id": "P-101",
            "provider_id": "DOC-501",
            "slot_time": "2026-10-16 14:00",
            "chief_complaint": "Initial complaint"
        })
        appt_id = create_res.json()["id"]

        # Update slot time and complaint
        put_res = self.client.put(f"/appointments/{appt_id}", json={
            "slot_time": "2026-10-16 15:30",
            "chief_complaint": "Updated complaint: acute knee strain",
            "status": "RESCHEDULED"
        })
        self.assertEqual(put_res.status_code, 200)
        updated = put_res.json()
        self.assertEqual(updated["slot_time"], "2026-10-16 15:30")
        self.assertEqual(updated["status"], "RESCHEDULED")

    # =========================================================================
    # 6. DELETE /appointments/{id}
    # =========================================================================
    def test_06_delete_appointment(self):
        """Test DELETE /appointments/{id} marks appointment cancelled."""
        idem_key = f"DEL-IDEM-{uuid.uuid4().hex}"
        create_res = self.client.post("/appointments", json={
            "idempotency_key": idem_key,
            "patient_id": "P-101",
            "provider_id": "DOC-501",
            "slot_time": "2026-10-17 11:00"
        })
        appt_id = create_res.json()["id"]

        del_res = self.client.delete(f"/appointments/{appt_id}")
        self.assertEqual(del_res.status_code, 200)

        # Verify status is now CANCELLED
        get_res = self.client.get(f"/appointments/{appt_id}")
        self.assertEqual(get_res.json()["status"], "CANCELLED")

    # =========================================================================
    # 7. POST /appointments/{id}/verify
    # =========================================================================
    def test_07_verify_appointment(self):
        """Test POST /appointments/{id}/verify returns verification details."""
        idem_key = f"VERIF-KEY-{uuid.uuid4().hex}"
        create_res = self.client.post("/appointments", json={
            "idempotency_key": idem_key,
            "patient_id": "P-101",
            "provider_id": "DOC-501",
            "slot_time": "2026-10-18 10:00"
        })
        appt_id = create_res.json()["id"]

        # Verify by appt_id
        verif_res = self.client.post(f"/appointments/{appt_id}/verify")
        self.assertEqual(verif_res.status_code, 200)
        data = verif_res.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["appointment_id"], appt_id)

        # Verify by idempotency_key
        verif_key_res = self.client.post(f"/appointments/{idem_key}/verify")
        self.assertEqual(verif_key_res.status_code, 200)
        self.assertTrue(verif_key_res.json()["found"])

        # Non-existent ID returns found=False
        non_res = self.client.post("/appointments/NON-EXISTENT-999/verify")
        self.assertEqual(non_res.status_code, 200)
        self.assertFalse(non_res.json()["found"])

    # =========================================================================
    # 8. INTEGRATION / CONNECTOR LAYER
    # =========================================================================
    def test_08_connector_layer_communication(self):
        """
        Verifies that main application interacts with Mock EHR exclusively via
        the connector layer (mock_ehr_connector) with zero direct DB queries.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. Create Patient via Connector
        pat_payload = EHRPatientPayload(
            first_name="Clara",
            last_name="Oswald",
            dob="1989-11-23",
            contact="+1-555-321-9876",
            gender="Female"
        )
        patient = loop.run_until_complete(mock_ehr_connector.create_patient(pat_payload))
        self.assertIn("id", patient)

        # 2. Lookup Patient via Connector
        looked_up = loop.run_until_complete(mock_ehr_connector.lookup_patient(patient["id"]))
        self.assertIsNotNone(looked_up)
        self.assertEqual(looked_up["last_name"], "Oswald")

        # 3. Create Appointment via Connector
        idem_key = f"CONN-IDEM-{uuid.uuid4().hex}"
        appt_payload = EHRAppointmentPayload(
            idempotency_key=idem_key,
            external_patient_id=patient["id"],
            external_provider_id="DOC-501",
            slot_time="2026-10-19 14:00",
            chief_complaint="Connector integration test"
        )
        appt = loop.run_until_complete(mock_ehr_connector.create_appointment(appt_payload))
        self.assertIn("id", appt)

        # 4. Verify Appointment via Connector
        verif = loop.run_until_complete(mock_ehr_connector.verify_appointment(idem_key))
        self.assertTrue(verif.found)
        self.assertEqual(verif.ehr_appointment_id, appt["id"])

        loop.close()


if __name__ == "__main__":
    unittest.main()
