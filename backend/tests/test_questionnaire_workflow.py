import unittest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from backend.database import Base
from backend import models, schemas
from backend.services.questionnaire_service import QuestionnaireService
from backend.services.appointment_service import AppointmentService
from backend.capabilities.registry import capability_registry
from backend.capabilities.base import CapabilityContext

class TestPreVisitQuestionnaireWorkflow(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed Hospital
        self.hospital = models.Hospital(
            name="Apex Metro Hospital",
            address="100 Medical Center Way",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="admin@apexmetro.org",
            phone="+1-555-019-9000",
            status="APPROVED"
        )
        self.db.add(self.hospital)
        self.db.commit()

        # Seed Specialty & Doctor
        self.specialty = models.Specialty(
            name="Orthopedics",
            code="ORTHO",
            description="Joints and musculoskeletal health"
        )
        self.db.add(self.specialty)
        self.db.commit()

        self.doctor = models.Doctor(
            hospital_id=self.hospital.id,
            specialty_id=self.specialty.id,
            full_name="Dr. Gregory House, MD",
            specialty="Orthopedics",
            consultation_fee=200.0,
            slot_duration_min=30,
            is_active=True
        )
        self.db.add(self.doctor)
        self.db.commit()

        # Seed Patient
        self.patient = models.Patient(
            primary_hospital_id=self.hospital.id,
            patient_mrn=f"MRN-{uuid.uuid4().hex[:6]}",
            full_name="Alice Walker",
            email="alice.walker@example.com",
            phone="+1-555-321-4455",
            date_of_birth="1985-06-20",
            gender="Female"
        )
        self.db.add(self.patient)
        self.db.commit()

        # Seed Time Slot
        self.slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=datetime(2026, 10, 15, 10, 0),
            end_time=datetime(2026, 10, 15, 10, 30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    # =========================================================================
    # 1. QUESTIONNAIRE CONFIGURATION & 8 QUESTION TYPES
    # =========================================================================

    def test_configure_template_with_all_8_question_types(self):
        """Verify administrators can configure approved templates with all 8 supported question types."""
        questions = [
            {"id": "q1", "question": "Do you have any drug allergies?", "type": "yes_no", "required": True},
            {"id": "q2", "question": "Duration of symptoms?", "type": "choice", "options": ["<24h", "2-3d", ">1w"], "required": True},
            {"id": "q3", "question": "Associated symptoms noticed?", "type": "multiple_choice", "options": ["Fever", "Nausea", "Headache"], "required": False},
            {"id": "q4", "question": "Discomfort level 1 to 10?", "type": "numeric", "min": 1, "max": 10, "required": True, "urgency_rules": {"min_threshold": 8, "reason": "Severe pain scale"}},
            {"id": "q5", "question": "Date of onset?", "type": "date", "required": False},
            {"id": "q6", "question": "Current medications?", "type": "short_text", "required": False},
            {"id": "q7", "question": "Narrative impact on daily activity?", "type": "long_text", "required": False},
            {"id": "q8", "question": "Emergency contact person?", "type": "structured_fields", "fields": [{"id": "name", "type": "short_text"}, {"id": "phone", "type": "short_text"}], "required": False},
        ]

        req = schemas.QuestionnaireTemplateCreate(
            hospital_id=self.hospital.id,
            specialty_id=self.specialty.id,
            doctor_id=self.doctor.id,
            title="Comprehensive Orthopedic Intake Protocol",
            description="Pre-visit clinical questionnaire covering all 8 question types.",
            appointment_type="IN_PERSON",
            condition_category="ORTHOPEDICS",
            questions=questions,
            is_approved=True,
            approved_by="Dr. Gregory House, MD"
        )

        template = QuestionnaireService.create_template(self.db, req)
        self.assertIsNotNone(template.id)
        self.assertEqual(template.hospital_id, self.hospital.id)
        self.assertEqual(template.doctor_id, self.doctor.id)
        self.assertEqual(len(template.questions), 8)
        self.assertTrue(template.is_approved)

        # Audit Event Verification
        audit = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.action == "CREATE_QUESTIONNAIRE_TEMPLATE",
            models.AuditEvent.resource_id == template.id
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.status, "SUCCESS")

    # =========================================================================
    # 2. CLINICAL SAFETY GUARDRAILS (Diagnosis & Treatment Prohibitions)
    # =========================================================================

    def test_clinical_boundary_guardrail_rejects_diagnostic_or_prescriptive_questions(self):
        """
        Verify clinical boundaries: System must NOT be used for diagnosis or prescribing.
        Attempts to configure diagnostic/treatment/prescriptive questions must be rejected.
        """
        # Diagnostic attempt
        bad_questions_diag = [
            {"id": "diag1", "question": "What medical diagnosis do you believe explains your condition?", "type": "short_text"}
        ]
        req_diag = schemas.QuestionnaireTemplateCreate(
            hospital_id=self.hospital.id,
            title="Unauthorized Diagnostic Screener",
            questions=bad_questions_diag
        )
        with self.assertRaises(HTTPException) as ctx:
            QuestionnaireService.create_template(self.db, req_diag)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Clinical Safety Boundary Violation", ctx.exception.detail)

        # Prescribing attempt
        bad_questions_rx = [
            {"id": "rx1", "question": "Should we prescribe an antibiotic for your infection?", "type": "yes_no"}
        ]
        req_rx = schemas.QuestionnaireTemplateCreate(
            hospital_id=self.hospital.id,
            title="Unauthorized Prescribing Questionnaire",
            questions=bad_questions_rx
        )
        with self.assertRaises(HTTPException) as ctx:
            QuestionnaireService.create_template(self.db, req_rx)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Clinical Safety Boundary Violation", ctx.exception.detail)

    # =========================================================================
    # 3. AUTO-ASSIGNMENT WORKFLOW POST-BOOKING
    # =========================================================================

    def test_post_booking_lifecycle_assigns_questionnaire(self):
        """
        Appointment -> Questionnaire Assigned -> AI Collects Responses -> Structured Responses -> Doctor Review
        Verify that creating/confirming an appointment automatically assigns the matching questionnaire.
        """
        # Configure doctor-specific template
        questions = [
            {"id": "pain", "question": "Rate joint discomfort 1-10", "type": "numeric", "required": True},
            {"id": "swelling", "question": "Is there visible joint swelling?", "type": "yes_no", "required": True}
        ]
        template = QuestionnaireService.create_template(
            self.db,
            schemas.QuestionnaireTemplateCreate(
                hospital_id=self.hospital.id,
                doctor_id=self.doctor.id,
                title="Dr. House Knee Protocol",
                questions=questions
            )
        )

        # Create confirmed appointment
        appt = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            slot_id=self.slot.id,
            chief_complaint="Twisted right knee while playing soccer",
            status="CONFIRMED"
        )

        # Assign questionnaire
        resp = QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.appointment_id, appt.id)
        self.assertEqual(resp.questionnaire_id, template.id)
        self.assertEqual(resp.status, "ASSIGNED")

        # Verify PreVisitQuestionnaire backward compatibility link
        pvq = QuestionnaireService.get_by_appointment(self.db, appt.id)
        self.assertIsNotNone(pvq)
        self.assertEqual(pvq.status, "ASSIGNED")
        self.assertEqual(len(pvq.questions), 2)

    # =========================================================================
    # 4. CONVERSATIONAL EXTRACTION INTO STRUCTURED DATA
    # =========================================================================

    def test_conversational_intake_extracts_structured_responses(self):
        """Verify the AI collects questionnaire responses conversationally and converts to structured data."""
        # Setup appointment & questionnaire
        appt = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            chief_complaint="Knee sprain"
        )
        QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)

        # Turn 1: Patient answers duration & pain scale conversationally
        turn1 = QuestionnaireService.process_conversational_turn(
            db=self.db,
            appointment_id=appt.id,
            patient_utterance="It started 3 days ago and the discomfort is around 4 out of 10"
        )
        self.assertIn("symptom_duration", turn1.all_answers)
        self.assertEqual(turn1.all_answers["symptom_duration"], "2-3 days")
        self.assertEqual(turn1.all_answers["pain_scale"], 4)
        self.assertFalse(turn1.is_complete)
        self.assertFalse(turn1.is_urgent)

        # Turn 2: Patient answers allergies and emergency symptoms
        turn2 = QuestionnaireService.process_conversational_turn(
            db=self.db,
            appointment_id=appt.id,
            patient_utterance="No chest pain or breathing issues at all, completely fine otherwise"
        )
        self.assertEqual(turn2.all_answers.get("emergency_symptoms"), "No")

    # =========================================================================
    # 5. URGENCY DETECTION & ESCALATION POLICY
    # =========================================================================

    def test_urgency_escalation_on_critical_symptoms_or_severe_pain(self):
        """
        Verify escalation policy: Potentially urgent information (e.g. chest pain, pain >= 8)
        must trigger URGENT_ESCALATED, prompt emergency guidance, and log an audit alert.
        """
        appt = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            chief_complaint="Palpitations and shortness of breath"
        )
        QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)

        # Patient reports severe chest pain & pain 9/10
        turn = QuestionnaireService.process_conversational_turn(
            db=self.db,
            appointment_id=appt.id,
            patient_utterance="I have active chest pain and severe shortness of breath, pain level is 9"
        )

        self.assertTrue(turn.is_urgent)
        self.assertGreaterEqual(len(turn.urgent_reasons), 1)

        # Check questionnaire status in DB
        quest = QuestionnaireService.get_by_appointment(self.db, appt.id)
        self.assertEqual(quest.status, "URGENT_ESCALATED")
        self.assertTrue(quest.is_urgent)

        # Verify prompt warns patient to seek emergency care / 911
        self.assertIn("IMPORTANT MEDICAL NOTICE", turn.ai_prompt_message)
        self.assertIn("911", turn.ai_prompt_message)

        # Verify Urgent Audit Event logged
        audit = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.action == "QUESTIONNAIRE_URGENT_ESCALATION",
            models.AuditEvent.resource_id == quest.id
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.status, "URGENT_ALERT")

    # =========================================================================
    # 6. DOCTOR REVIEW & CLINICAL SIGN-OFF WORKFLOW
    # =========================================================================

    def test_doctor_review_queue_and_signoff(self):
        """
        Verify doctor review queue:
        Submitted responses are visible in doctor's pending reviews,
        and doctor can submit clinical notes and sign-off.
        """
        appt = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            chief_complaint="Mild sprain"
        )
        QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)

        # Submit answers
        req = schemas.QuestionnaireSubmitRequest(
            answers={
                "symptom_duration": "2-3 days",
                "pain_scale": 3,
                "current_medications": "None",
                "emergency_symptoms": "No"
            }
        )
        QuestionnaireService.submit_answers(self.db, appt.id, req)

        # 1. Doctor retrieves pending reviews queue
        pending = QuestionnaireService.get_pending_reviews_for_doctor(self.db, self.doctor.id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["appointment_id"], appt.id)
        self.assertEqual(pending[0]["patient_name"], "Alice Walker")
        self.assertIn("Patient-Reported Intake Summary", pending[0]["patient_intake_summary"])

        # 2. Doctor signs off with clinical notes
        reviewed = QuestionnaireService.submit_doctor_review(
            db=self.db,
            appointment_id=appt.id,
            doctor_id=self.doctor.id,
            doctor_notes="Reviewed pre-visit intake. Routine sprain, will order x-ray if weight bearing is compromised."
        )

        self.assertEqual(reviewed.status, "REVIEWED")
        self.assertEqual(reviewed.reviewed_by, self.doctor.id)
        self.assertIsNotNone(reviewed.reviewed_at)
        self.assertIn("Routine sprain", reviewed.doctor_notes)

        # Audit Event check
        audit = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.action == "QUESTIONNAIRE_REVIEWED",
            models.AuditEvent.resource_id == reviewed.id
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor_id, self.doctor.id)
        self.assertEqual(audit.actor_role, "DOCTOR")

    # =========================================================================
    # 7. CONTROLLED CAPABILITY INTEGRATION
    # =========================================================================

    def test_controlled_capabilities_get_and_submit_questionnaire(self):
        """Verify get_questionnaire and submit_questionnaire capabilities execute safely through registry."""
        appt = AppointmentService.create_appointment(
            db=self.db,
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            patient_id=self.patient.id,
            chief_complaint="Annual checkup"
        )
        QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)

        cap_context = CapabilityContext(
            db=self.db,
            correlation_id=f"CORR-{uuid.uuid4().hex[:8]}",
            tenant_id=self.hospital.id,
            user_id=self.patient.id,
            user_role="PATIENT"
        )

        import asyncio
        loop = asyncio.new_event_loop()

        # Get questionnaire capability
        get_res = loop.run_until_complete(
            capability_registry.invoke("get_questionnaire", {"appointment_id": appt.id}, cap_context)
        )
        self.assertTrue(get_res.success)
        self.assertEqual(get_res.data["appointment_id"], appt.id)

        # Submit questionnaire capability
        sub_res = loop.run_until_complete(
            capability_registry.invoke(
                "submit_questionnaire",
                {"appointment_id": appt.id, "answers": {"allergies": "Penicillin", "pain_scale": 2}},
                cap_context
            )
        )
        self.assertTrue(sub_res.success)
        self.assertIn("Patient-Reported Intake Summary", sub_res.data["patient_intake_summary"])

        loop.close()

if __name__ == "__main__":
    unittest.main()
