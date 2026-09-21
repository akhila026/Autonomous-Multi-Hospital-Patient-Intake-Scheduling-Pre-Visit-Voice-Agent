import unittest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend import models

class TestDatabaseModels(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = Session()

    def tearDown(self):
        self.db.close()

    def test_hospital_hierarchy_and_tenant_isolation(self):
        """Test Hospital, HospitalAdmin, Department, Specialty, Doctor, Calendar, Availability, BlockedSlot."""
        # 1. Hospital
        hosp = models.Hospital(
            name="General Health Center",
            address="100 Main St",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="admin@generalhealth.org",
            phone="+1-555-111-2222",
            status="APPROVED"
        )
        self.db.add(hosp)
        self.db.commit()

        # 2. HospitalAdmin
        admin = models.HospitalAdmin(
            hospital_id=hosp.id,
            full_name="Jane Doe",
            email="jane.doe@generalhealth.org",
            phone="+1-555-111-2223"
        )
        self.db.add(admin)

        # 3. Department
        dept = models.Department(
            hospital_id=hosp.id,
            name="Orthopedics & Joint Care",
            code="ORTHO-01"
        )
        self.db.add(dept)

        # 4. Specialty
        spec = models.Specialty(
            name="Orthopedics",
            code="ORTHO",
            description="Bone and joint health"
        )
        self.db.add(spec)
        self.db.commit()

        # 5. Doctor
        doc = models.Doctor(
            hospital_id=hosp.id,
            department_id=dept.id,
            specialty_id=spec.id,
            full_name="Dr. Gregory House",
            specialty="Orthopedics",
            consultation_fee=190.0
        )
        self.db.add(doc)
        self.db.commit()

        # 6. Calendar
        cal = models.Calendar(
            hospital_id=hosp.id,
            doctor_id=doc.id,
            name="Dr. House Schedule"
        )
        self.db.add(cal)
        self.db.commit()

        # 7. Availability
        avail = models.Availability(
            hospital_id=hosp.id,
            doctor_id=doc.id,
            calendar_id=cal.id,
            day_of_week=1,
            start_time="09:00",
            end_time="17:00"
        )
        self.db.add(avail)

        # 8. BlockedSlot
        now = datetime.utcnow()
        blocked = models.BlockedSlot(
            hospital_id=hosp.id,
            doctor_id=doc.id,
            calendar_id=cal.id,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            reason="Diagnostic rounds"
        )
        self.db.add(blocked)
        self.db.commit()

        # Assertions on relationships & tenant keys
        self.assertEqual(len(hosp.admins), 1)
        self.assertEqual(len(hosp.departments), 1)
        self.assertEqual(len(hosp.doctors), 1)
        self.assertEqual(len(hosp.calendars), 1)
        self.assertEqual(doc.hospital.id, hosp.id)
        self.assertEqual(doc.department.name, "Orthopedics & Joint Care")
        self.assertEqual(doc.specialty_rel.name, "Orthopedics")
        self.assertEqual(len(doc.calendars), 1)
        self.assertEqual(len(doc.availabilities), 1)
        self.assertEqual(len(doc.blocked_slots), 1)
        self.assertEqual(blocked.hospital_id, hosp.id)

    def test_appointment_lifecycle_and_integration_entities(self):
        """Test Patient, Appointment, IntegrationOperation, Verification, ReconciliationRecord."""
        hosp = models.Hospital(
            name="Mercy Hospital",
            address="200 Oak St",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="mercy@health.org",
            phone="+1-555-333-4444"
        )
        self.db.add(hosp)
        self.db.commit()

        doc = models.Doctor(
            hospital_id=hosp.id,
            full_name="Dr. John Watson",
            specialty="General Medicine"
        )
        self.db.add(doc)

        patient = models.Patient(
            primary_hospital_id=hosp.id,
            patient_mrn="MRN-112233",
            full_name="Sherlock Holmes",
            phone="+1-555-999-8888",
            email="sherlock@bakerstreet.org"
        )
        self.db.add(patient)
        self.db.commit()

        # Appointment
        idempotency_key = f"IDEM-{uuid.uuid4().hex}"
        start_time = datetime.utcnow() + timedelta(days=3)
        appt = models.Appointment(
            hospital_id=hosp.id,
            patient_id=patient.id,
            doctor_id=doc.id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            status="CONFIRMED",
            idempotency_key=idempotency_key,
            chief_complaint="Persistent headache and fatigue",
            ehr_appointment_id="EHR-998877"
        )
        self.db.add(appt)
        self.db.commit()

        # IntegrationOperation
        integ_op = models.IntegrationOperation(
            hospital_id=hosp.id,
            appointment_id=appt.id,
            operation_type="CREATE_APPOINTMENT",
            idempotency_key=idempotency_key,
            status="SUCCESS",
            response_time_ms=180
        )
        self.db.add(integ_op)

        # Verification
        verif = models.Verification(
            hospital_id=hosp.id,
            appointment_id=appt.id,
            idempotency_key=idempotency_key,
            external_appointment_id="EHR-998877",
            status="FOUND"
        )
        self.db.add(verif)

        # ReconciliationRecord
        recon = models.ReconciliationRecord(
            hospital_id=hosp.id,
            appointment_id=appt.id,
            idempotency_key=idempotency_key,
            failure_reason="Simulated network socket timeout recovered via verification",
            status="RECONCILED"
        )
        self.db.add(recon)

        # ExternalIdentifierMapping
        mapping = models.ExternalIdentifierMapping(
            hospital_id=hosp.id,
            system_name="MOCK_EHR",
            entity_type="APPOINTMENT",
            internal_id=appt.id,
            external_id="EHR-998877"
        )
        self.db.add(mapping)
        self.db.commit()

        # Assertions
        self.assertEqual(appt.patient.full_name, "Sherlock Holmes")
        self.assertEqual(appt.doctor.full_name, "Dr. John Watson")
        self.assertEqual(len(appt.verifications), 1)
        self.assertEqual(appt.verifications[0].status, "FOUND")
        self.assertEqual(len(appt.reconciliation_records), 1)
        self.assertEqual(len(appt.integration_operations), 1)
        self.assertEqual(mapping.external_id, "EHR-998877")

    def test_ai_questionnaire_workflow_and_audit(self):
        """Test Questionnaire, QuestionnaireResponse, AIConversation, AIContext, Workflow, Notification, AuditEvent."""
        hosp = models.Hospital(
            name="Metro Care",
            address="300 Pine St",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="care@metro.org",
            phone="+1-555-777-6666"
        )
        self.db.add(hosp)
        self.db.commit()

        patient = models.Patient(
            full_name="Diana Prince",
            phone="+1-555-444-3333",
            email="diana@amazon.org"
        )
        self.db.add(patient)

        doc = models.Doctor(
            hospital_id=hosp.id,
            full_name="Dr. Clark Kent",
            specialty="Cardiology"
        )
        self.db.add(doc)
        self.db.commit()

        appt = models.Appointment(
            hospital_id=hosp.id,
            patient_id=patient.id,
            doctor_id=doc.id,
            idempotency_key=f"IDEM-{uuid.uuid4().hex}",
            status="CONFIRMED"
        )
        self.db.add(appt)
        self.db.commit()

        # Questionnaire & QuestionnaireResponse
        quest = models.Questionnaire(
            hospital_id=hosp.id,
            title="Cardiac Health Survey",
            questions=[{"id": "q1", "question": "Any palpitations?", "type": "choice", "options": ["Yes", "No"]}]
        )
        self.db.add(quest)
        self.db.commit()

        resp = models.QuestionnaireResponse(
            questionnaire_id=quest.id,
            appointment_id=appt.id,
            patient_id=patient.id,
            hospital_id=hosp.id,
            answers={"q1": "No"},
            status="SUBMITTED"
        )
        self.db.add(resp)

        # AIConversation & AIContext
        session_id = f"SES-{uuid.uuid4().hex}"
        conv = models.AIConversation(
            session_id=session_id,
            patient_id=patient.id,
            hospital_id=hosp.id,
            channel="web_voice",
            transcript=[{"role": "user", "content": "I need a cardiologist appointment"}]
        )
        self.db.add(conv)
        self.db.commit()

        ctx = models.AIContext(
            conversation_id=conv.id,
            hospital_id=hosp.id,
            current_intent="BOOK_APPOINTMENT",
            selected_doctor_id=doc.id,
            urgency_level="ROUTINE"
        )
        self.db.add(ctx)

        # Workflow & Notification
        wf = models.Workflow(
            hospital_id=hosp.id,
            appointment_id=appt.id,
            workflow_type="POST_BOOKING_REMINDER",
            status="RUNNING"
        )
        self.db.add(wf)

        notif = models.Notification(
            hospital_id=hosp.id,
            appointment_id=appt.id,
            recipient_type="PATIENT",
            recipient_contact="diana@amazon.org",
            channel="SMS",
            template="CONFIRMATION_SMS",
            scheduled_for=datetime.utcnow()
        )
        self.db.add(notif)

        # AuditEvent
        correlation_id = f"CORR-{uuid.uuid4().hex}"
        audit = models.AuditEvent(
            correlation_id=correlation_id,
            hospital_id=hosp.id,
            actor_id=patient.id,
            actor_role="PATIENT",
            action="APPOINTMENT_BOOKED",
            resource_type="Appointment",
            resource_id=appt.id,
            status="SUCCESS",
            details={"slot_held": True}
        )
        self.db.add(audit)
        self.db.commit()

        # Assertions
        self.assertEqual(len(quest.responses), 1)
        self.assertEqual(quest.responses[0].status, "SUBMITTED")
        self.assertEqual(conv.ai_context.current_intent, "BOOK_APPOINTMENT")
        self.assertEqual(len(hosp.workflows), 1)
        self.assertEqual(len(hosp.notifications), 1)
        self.assertEqual(audit.action, "APPOINTMENT_BOOKED")
        self.assertEqual(audit.correlation_id, correlation_id)

    def test_questionnaire_response_patient_intake_summary(self):
        """
        Test that QuestionnaireResponse uses patient_intake_summary containing strictly
        patient-reported facts, with no clinical impressions, diagnoses, or treatment recommendations.
        """
        from backend.services.questionnaire_service import QuestionnaireService
        from backend import schemas

        hosp = models.Hospital(
            name="Saint Mary Medical",
            address="500 Health Park",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="admin@stmary.org",
            phone="+1-555-001-9988"
        )
        self.db.add(hosp)
        self.db.commit()

        doc = models.Doctor(
            hospital_id=hosp.id,
            full_name="Dr. Gregory House",
            specialty="Diagnostic Medicine"
        )
        self.db.add(doc)

        patient = models.Patient(
            primary_hospital_id=hosp.id,
            full_name="Jane Doe",
            phone="+1-555-432-1000",
            email="jane@example.com",
            gender="Female",
            date_of_birth="1988-03-12"
        )
        self.db.add(patient)
        self.db.commit()

        appt = models.Appointment(
            hospital_id=hosp.id,
            doctor_id=doc.id,
            patient_id=patient.id,
            idempotency_key=f"IDEM-{uuid.uuid4().hex}",
            chief_complaint="Sharp lower back pain after lifting boxes",
            urgency_level="ROUTINE",
            status="CONFIRMED"
        )
        self.db.add(appt)
        self.db.commit()

        # Create PreVisitQuestionnaire
        pvq = models.PreVisitQuestionnaire(
            appointment_id=appt.id,
            patient_id=patient.id,
            questions=[
                {"id": "duration", "question": "How long have you had this pain?"},
                {"id": "severity", "question": "Pain level 1-10?"}
            ],
            status="PENDING"
        )
        self.db.add(pvq)
        self.db.commit()

        # Submit answers via QuestionnaireService
        submit_req = schemas.QuestionnaireSubmitRequest(
            answers={
                "duration": "3 days",
                "severity": "7/10",
                "medications": "Ibuprofen 400mg taken once"
            }
        )
        updated_pvq = QuestionnaireService.submit_answers(self.db, appt.id, submit_req)

        # 1. Verify patient_intake_summary attribute is set
        self.assertIsNotNone(updated_pvq.patient_intake_summary)
        summary = updated_pvq.patient_intake_summary

        # 2. Verify backward compatible ai_clinical_summary alias reads patient_intake_summary
        self.assertEqual(updated_pvq.ai_clinical_summary, summary)

        # 3. Verify it contains purely patient-reported facts
        self.assertIn("Patient-Reported Intake Summary", summary)
        self.assertIn("Jane Doe", summary)
        self.assertIn("Sharp lower back pain", summary)
        self.assertIn("3 days", summary)
        self.assertIn("7/10", summary)

        # 4. Verify strict absence of clinical conclusions, diagnoses, differential diagnoses, or treatment recommendations
        forbidden_clinical_terms = [
            "differential diagnosis",
            "clinical impression",
            "treatment recommendation",
            "prognosis",
            "pathology",
            "prescribe",
            "conclude",
            "rule out"
        ]
        for term in forbidden_clinical_terms:
            self.assertNotIn(term, summary.lower(), f"Forbidden clinical term '{term}' found in summary!")

        # 5. Verify QuestionnaireResponse entity
        qr = models.QuestionnaireResponse(
            appointment_id=appt.id,
            patient_id=patient.id,
            hospital_id=hosp.id,
            answers=submit_req.answers,
            patient_intake_summary=summary,
            status="SUBMITTED"
        )
        self.db.add(qr)
        self.db.commit()
        self.assertEqual(qr.patient_intake_summary, summary)
        self.assertEqual(qr.ai_clinical_summary, summary)

    def test_service_layer_tenant_isolation_rejections(self):
        """
        Test service-layer tenant isolation validation:
        - appointment.hospital_id == doctor.hospital_id
        - appointment.hospital_id == patient.hospital_id (where applicable)
        - appointment.hospital_id == calendar.hospital_id (where applicable)
        Rejects cross-hospital assignments with clear TenantMismatchError.
        """
        from backend.services.appointment_service import AppointmentService
        from backend.auth.tenant import TenantMismatchError

        # Hospital A & B
        hosp_a = models.Hospital(
            name="Hospital Alpha",
            address="1 Alpha Way",
            license_number=f"LIC-A-{uuid.uuid4().hex[:6]}",
            contact_email="admin@alpha.org",
            phone="+1-555-111-0000"
        )
        hosp_b = models.Hospital(
            name="Hospital Beta",
            address="2 Beta Blvd",
            license_number=f"LIC-B-{uuid.uuid4().hex[:6]}",
            contact_email="admin@beta.org",
            phone="+1-555-222-0000"
        )
        self.db.add_all([hosp_a, hosp_b])
        self.db.commit()

        # Doctor A (Hospital A) and Doctor B (Hospital B)
        doc_a = models.Doctor(hospital_id=hosp_a.id, full_name="Dr. Alpha", specialty="Cardiology")
        doc_b = models.Doctor(hospital_id=hosp_b.id, full_name="Dr. Beta", specialty="Cardiology")
        self.db.add_all([doc_a, doc_b])
        self.db.commit()

        # Calendar A (Hospital A) and Calendar B (Hospital B)
        cal_a = models.Calendar(hospital_id=hosp_a.id, doctor_id=doc_a.id, name="Cal Alpha")
        cal_b = models.Calendar(hospital_id=hosp_b.id, doctor_id=doc_b.id, name="Cal Beta")
        self.db.add_all([cal_a, cal_b])
        self.db.commit()

        # Patient A (Hospital A) and Patient B (Hospital B)
        pat_a = models.Patient(primary_hospital_id=hosp_a.id, full_name="Patient Alpha", phone="+1-555-111", email="a@test.com")
        pat_b = models.Patient(primary_hospital_id=hosp_b.id, full_name="Patient Beta", phone="+1-555-222", email="b@test.com")
        self.db.add_all([pat_a, pat_b])
        self.db.commit()

        # CASE 1: Cross-hospital doctor assignment
        # Appointment for Hospital A with Doctor from Hospital B -> REJECT
        with self.assertRaises(TenantMismatchError) as ctx:
            AppointmentService.create_appointment(
                db=self.db,
                hospital_id=hosp_a.id,
                doctor_id=doc_b.id,
                patient_id=pat_a.id,
                calendar_id=cal_a.id
            )
        self.assertIn("Doctor belongs to hospital", str(ctx.exception))

        # CASE 2: Cross-hospital patient assignment
        # Appointment for Hospital A with Patient scoped to Hospital B -> REJECT
        with self.assertRaises(TenantMismatchError) as ctx:
            AppointmentService.create_appointment(
                db=self.db,
                hospital_id=hosp_a.id,
                doctor_id=doc_a.id,
                patient_id=pat_b.id,
                calendar_id=cal_a.id
            )
        self.assertIn("Patient is registered with hospital", str(ctx.exception))

        # CASE 3: Cross-hospital calendar assignment
        # Appointment for Hospital A with Calendar from Hospital B -> REJECT
        with self.assertRaises(TenantMismatchError) as ctx:
            AppointmentService.create_appointment(
                db=self.db,
                hospital_id=hosp_a.id,
                doctor_id=doc_a.id,
                patient_id=pat_a.id,
                calendar_id=cal_b.id
            )
        self.assertIn("Calendar belongs to hospital", str(ctx.exception))

        # CASE 4: Valid creation where all resources match hospital A -> SUCCESS
        appt_valid = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=hosp_a.id,
            doctor_id=doc_a.id,
            patient_id=pat_a.id,
            calendar_id=cal_a.id,
            chief_complaint="Chest pressure",
            urgency_level="URGENT"
        )
        self.assertIsNotNone(appt_valid.id)
        self.assertEqual(appt_valid.hospital_id, hosp_a.id)

        # CASE 5: Updating appointment to cross-hospital doctor -> REJECT
        with self.assertRaises(TenantMismatchError) as ctx:
            AppointmentService.update_appointment(
                db=self.db,
                appointment_id=appt_valid.id,
                doctor_id=doc_b.id
            )
        self.assertIn("Doctor belongs to hospital", str(ctx.exception))

        # CASE 6: Updating appointment to cross-hospital calendar -> REJECT
        with self.assertRaises(TenantMismatchError) as ctx:
            AppointmentService.update_appointment(
                db=self.db,
                appointment_id=appt_valid.id,
                calendar_id=cal_b.id
            )
        self.assertIn("Calendar belongs to hospital", str(ctx.exception))

        # CASE 7: Updating appointment with same-hospital resource -> SUCCESS
        appt_updated = AppointmentService.update_appointment(
            db=self.db,
            appointment_id=appt_valid.id,
            chief_complaint="Updated complaint: Mild chest flutter"
        )
        self.assertEqual(appt_updated.chief_complaint, "Updated complaint: Mild chest flutter")

if __name__ == "__main__":
    unittest.main()
