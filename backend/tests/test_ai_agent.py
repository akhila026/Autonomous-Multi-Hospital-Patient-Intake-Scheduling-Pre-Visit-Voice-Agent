import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend import models
from backend.ai.guardrails import ClinicalGuardrails
from backend.ai.intent import IntentDetector
from backend.ai.context import ConversationContext, ConversationContextManager
from backend.ai.agent import AIPatientAccessAgent
from backend.capabilities.registry import capability_registry


class TestAIPatientAccessAgent(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        TestSession = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = TestSession()

        # Seed Hospital
        self.hospital = models.Hospital(
            name="Aegis Health Center",
            address="123 Care Ave, New York, NY",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="admin@aegishealth.org",
            phone="+1-555-000-1111",
            status="APPROVED"
        )
        self.db.add(self.hospital)
        self.db.commit()

        # Seed Doctor
        self.doctor = models.Doctor(
            hospital_id=self.hospital.id,
            full_name="Dr. Sarah Jenkins",
            specialty="Orthopedics",
            consultation_fee=180.0,
            slot_duration_min=30,
            is_active=True
        )
        self.db.add(self.doctor)
        self.db.commit()

        # Seed Doctor Availability Schedule for all days
        for d in range(7):
            sched = models.AvailabilitySchedule(
                doctor_id=self.doctor.id,
                day_of_week=d,
                start_time="00:00",
                end_time="23:59",
                is_active=True
            )
            self.db.add(sched)
        self.db.commit()

        # Seed Slot
        slot_time = datetime.utcnow() + timedelta(days=2)
        self.slot = models.TimeSlot(
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)

        # Seed Patient
        self.patient = models.Patient(
            full_name="Michael Scott",
            phone="+1-555-832-1920",
            email="michael@dundermifflin.com"
        )
        self.db.add(self.patient)
        self.db.commit()

        self.session_id = f"TEST-SES-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        self.db.close()

    # =================================================================
    # 1. CLINICAL GUARDRAIL TESTS
    # =================================================================

    def test_guardrail_refuses_medical_diagnosis(self):
        """Clinical guardrails must refuse diagnostic requests with a standard clinical disclaimer."""
        res = ClinicalGuardrails.evaluate("Can you diagnose this painful rash on my arm?")
        self.assertFalse(res.passed)
        self.assertTrue(res.is_clinical_prohibited)
        self.assertEqual(res.refusal_reason, "DIAGNOSIS_REQUEST")
        self.assertIn("cannot provide a medical diagnosis", res.reply)
        self.assertIn("licensed physician", res.reply)

    def test_guardrail_refuses_prescriptions(self):
        """Clinical guardrails must refuse prescription writing or refills."""
        res = ClinicalGuardrails.evaluate("Can you prescribe me some antibiotics for my sore throat?")
        self.assertFalse(res.passed)
        self.assertTrue(res.is_clinical_prohibited)
        self.assertEqual(res.refusal_reason, "PRESCRIPTION_REQUEST")
        self.assertIn("cannot prescribe medications", res.reply)

    def test_guardrail_refuses_dosage_modification(self):
        """Clinical guardrails must refuse advice on changing medication dosages."""
        res = ClinicalGuardrails.evaluate("Should I double my dosage of blood pressure medication?")
        self.assertFalse(res.passed)
        self.assertTrue(res.is_clinical_prohibited)
        self.assertEqual(res.refusal_reason, "MEDICATION_CHANGE_REQUEST")
        self.assertIn("cannot recommend changes to your medication", res.reply)

    def test_guardrail_refuses_treatment_recommendations(self):
        """Clinical guardrails must refuse clinical treatment plans."""
        res = ClinicalGuardrails.evaluate("How should I treat my sprained ankle at home?")
        self.assertFalse(res.passed)
        self.assertTrue(res.is_clinical_prohibited)
        self.assertEqual(res.refusal_reason, "TREATMENT_RECOMMENDATION_REQUEST")
        self.assertIn("cannot provide clinical treatment plans", res.reply)

    def test_guardrail_emergency_red_flag(self):
        """Acute medical red-flags must trigger immediate emergency alert and 911 directives."""
        res = ClinicalGuardrails.evaluate("I have crushing chest pain and shortness of breath")
        self.assertFalse(res.passed)
        self.assertTrue(res.is_emergency)
        self.assertEqual(res.refusal_reason, "EMERGENCY_RED_FLAG_DETECTED")
        self.assertIn("911", res.reply)
        self.assertIn("Emergency Room", res.reply)

    # =================================================================
    # 2. INTENT DETECTION TESTS
    # =================================================================

    def test_intent_detection(self):
        """Verify accurate categorization of administrative intents."""
        d1 = IntentDetector.detect("I want to schedule an appointment with a cardiologist")
        self.assertEqual(d1.intent, "BOOK_APPOINTMENT")
        self.assertEqual(d1.entities.specialty, "Cardiology")

        d2 = IntentDetector.detect("Find me an orthopedic doctor")
        self.assertEqual(d2.intent, "SEARCH_DOCTORS")
        self.assertEqual(d2.entities.specialty, "Orthopedics")

        d3 = IntentDetector.detect("When are open slots for Dr. Sarah Jenkins?")
        self.assertEqual(d3.intent, "CHECK_AVAILABILITY")
        self.assertEqual(d3.entities.doctor_name, "Dr. Sarah Jenkins")

        d4 = IntentDetector.detect("I need to cancel my appointment")
        self.assertEqual(d4.intent, "CANCEL_APPOINTMENT")

        d5 = IntentDetector.detect("Can I reschedule my visit to another day?")
        self.assertEqual(d5.intent, "RESCHEDULE_APPOINTMENT")

        d6 = IntentDetector.detect("Please transfer me to a human representative")
        self.assertEqual(d6.intent, "ESCALATE_TO_HUMAN")

    # =================================================================
    # 3. CLARIFICATION ON MISSING INFORMATION
    # =================================================================

    def test_clarification_on_missing_doctor_and_slot(self):
        """When user asks to book without doctor or slot, the agent asks for clarification."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        turn = loop.run_until_complete(
            AIPatientAccessAgent.process_turn(
                db=self.db,
                session_id=self.session_id,
                message="I would like to book an appointment please"
            )
        )
        loop.close()

        self.assertEqual(turn.intent, "BOOK_APPOINTMENT")
        self.assertTrue(turn.needs_clarification)
        self.assertIn("Which doctor or specialty", turn.reply)

    # =================================================================
    # 4. MULTI-TURN CONTEXT & REFERENCE RESOLUTION WORKFLOW
    # =================================================================

    def test_multi_turn_booking_and_questionnaire_flow(self):
        """
        Complete Multi-Turn Workflow:
        Turn 1: "Find an orthopedics doctor" -> AI searches doctors and stores Dr. Sarah Jenkins in context.
        Turn 2: "Check her availability" -> AI resolves "her" to Dr. Sarah, returns open slots.
        Turn 3: "Book the first one" -> AI resolves slot, executes create_appointment capability,
                confirms booking, and presents pre-visit questionnaire.
        Turn 4: "My symptoms started 3 days ago" -> AI collects questionnaire answers.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Turn 1: Search Doctors
        t1 = loop.run_until_complete(
            AIPatientAccessAgent.process_turn(
                db=self.db,
                session_id=self.session_id,
                message="Find an orthopedics doctor"
            )
        )
        self.assertEqual(t1.intent, "SEARCH_DOCTORS")
        self.assertIn("search_doctors", t1.capabilities_executed)
        self.assertIn("Dr. Sarah Jenkins", t1.reply)

        # Turn 2: Check availability using relative reference
        t2 = loop.run_until_complete(
            AIPatientAccessAgent.process_turn(
                db=self.db,
                session_id=self.session_id,
                message="Check availability for the first doctor"
            )
        )
        self.assertEqual(t2.intent, "CHECK_AVAILABILITY")
        self.assertIn("check_availability", t2.capabilities_executed)
        self.assertIn("open slots with **Dr. Sarah Jenkins**", t2.reply)

        # Turn 3: Book slot using pronoun / demonstrative reference
        t3 = loop.run_until_complete(
            AIPatientAccessAgent.process_turn(
                db=self.db,
                session_id=self.session_id,
                message="Please book that slot for me",
                patient_id=self.patient.id
            )
        )
        self.assertEqual(t3.intent, "BOOK_APPOINTMENT")
        self.assertIn("create_appointment", t3.capabilities_executed)
        self.assertIn("get_questionnaire", t3.capabilities_executed)
        self.assertIn("Appointment Confirmed!", t3.reply)
        self.assertIn("Pre-Visit Intake", t3.reply)

        # Verify Context Persistence in SQLite
        ctx = ConversationContextManager.get_or_create(self.db, self.session_id)
        self.assertIsNotNone(ctx.current_appointment)
        self.assertIn("APPOINTMENT_CONFIRMED", ctx.completed_workflow_state)
        self.assertEqual(len(ctx.turns), 6)  # 3 user turns + 3 assistant turns

        # Turn 4: Questionnaire Intake Submission
        t4 = loop.run_until_complete(
            AIPatientAccessAgent.process_turn(
                db=self.db,
                session_id=self.session_id,
                message="Symptoms started 3 days ago after twisting knee while jogging"
            )
        )
        self.assertEqual(t4.intent, "FILL_QUESTIONNAIRE")
        self.assertIn("submit_questionnaire", t4.capabilities_executed)
        self.assertIn("Thank you for submitting your pre-visit intake information", t4.reply)

        loop.close()

    # =================================================================
    # 5. CONTROLLED CAPABILITY REGISTRY INTEGRITY
    # =================================================================

    def test_all_17_capabilities_registered(self):
        """Verify that all 17 controlled capabilities are properly registered with input/output schemas."""
        caps = capability_registry.list_capabilities()
        self.assertEqual(len(caps), 17)
        for c in caps:
            self.assertIn("name", c)
            self.assertIn("description", c)
            self.assertIn("input_schema", c)
            self.assertIn("output_schema", c)


if __name__ == "__main__":
    unittest.main()
