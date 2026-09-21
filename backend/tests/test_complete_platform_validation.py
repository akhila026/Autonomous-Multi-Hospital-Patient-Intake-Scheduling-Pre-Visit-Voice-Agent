import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.database import SessionLocal, Base, engine
from backend import models, schemas
from backend.auth.roles import UserRole
from backend.auth.security import hash_password, create_access_token
from backend.services.appointment_service import AppointmentService
from backend.services.questionnaire_service import QuestionnaireService
from backend.services.observability_service import ObservabilityService, BookingTraceStage
from backend.services.mock_ehr_service import MockEhrService
from backend.services.doctor_service import DoctorService
from backend.services.slot_service import SlotService
from backend.workflows.engine import WorkflowEngine
from backend.ehr.connector import MockEHRConnector
from backend.ehr.reconciliation import EHRReconciliationManager


class TestCompletePlatformValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        cls.connector = MockEHRConnector()

    def setUp(self):
        self.db: Session = SessionLocal()
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)

        # Setup Platform Admin auth
        self.p_admin_user = self.db.query(models.User).filter(
            models.User.role == UserRole.PLATFORM_ADMIN.value
        ).first()
        if not self.p_admin_user:
            self.p_admin_user = models.User(
                email="platform.admin@aegiscare.io",
                full_name="Platform Admin",
                role=UserRole.PLATFORM_ADMIN.value,
                password_hash=hash_password("AdminPass123!"),
                is_active=True
            )
            self.db.add(self.p_admin_user)
            self.db.commit()
            self.db.refresh(self.p_admin_user)

        self.token_p_admin = create_access_token({
            "sub": self.p_admin_user.id,
            "role": UserRole.PLATFORM_ADMIN.value
        })

    def tearDown(self):
        self.connector.set_chaos_config(mode="NORMAL", is_active=False)
        self.db.close()

    def _auth(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _create_isolated_weekday_slot(self, doctor_id: str, days_ahead: int = 3):
        """Creates a guaranteed available weekday slot within standard 09:00 - 17:00 working hours."""
        now = datetime.utcnow()
        for day_offset in range(days_ahead, days_ahead + 20):
            target = now + timedelta(days=day_offset)
            if target.weekday() >= 5:  # Skip Saturday and Sunday
                continue
            for hour in [10, 11, 14, 15]:
                cand_start = datetime(target.year, target.month, target.day, hour, 0, 0)
                cand_end = cand_start + timedelta(minutes=30)
                exists_slot = self.db.query(models.TimeSlot).filter(
                    models.TimeSlot.doctor_id == doctor_id,
                    models.TimeSlot.start_time == cand_start
                ).first()
                exists_appt = self.db.query(models.Appointment).filter(
                    models.Appointment.doctor_id == doctor_id,
                    models.Appointment.status.in_(["CONFIRMED", "HELD", "PENDING_EHR_SYNC", "UNKNOWN_OUTCOME"]),
                    models.Appointment.start_time < cand_end,
                    models.Appointment.end_time > cand_start
                ).first()
                exists_blocked = self.db.query(models.BlockedSlot).filter(
                    models.BlockedSlot.doctor_id == doctor_id,
                    models.BlockedSlot.start_time < cand_end,
                    models.BlockedSlot.end_time > cand_start
                ).first()
                if not exists_slot and not exists_appt and not exists_blocked:
                    slot = models.TimeSlot(
                        doctor_id=doctor_id,
                        start_time=cand_start,
                        end_time=cand_end,
                        status="AVAILABLE"
                    )
                    self.db.add(slot)
                    self.db.commit()
                    self.db.refresh(slot)
                    return slot
        raise RuntimeError(f"Could not find free weekday slot for doctor {doctor_id}")

    # =========================================================================
    # STAGE 1: HOSPITAL JOURNEY
    # =========================================================================
    def test_stage_1_hospital_journey(self):
        """
        Hospital Journey:
        Hospital Registration -> Approval -> Configuration -> Doctor ->
        Calendar & Availability -> Healthcare System Integration.
        """
        prefix = uuid.uuid4().hex[:6]

        # 1. Hospital Registration
        hosp_payload = {
            "name": f"St. Jude Neuroscience Pavilion {prefix}",
            "address": "700 Brain Health Way, Metro City, NY",
            "license_number": f"HOSP-NEURO-{prefix.upper()}",
            "contact_email": f"neuro.{prefix}@stjudehealth.org",
            "phone": "+1-555-901-4433",
            "departments": ["Neurology", "Neurosurgery", "Sleep Medicine"]
        }
        res_reg = self.client.post("/api/hospitals/register", json=hosp_payload)
        self.assertIn(res_reg.status_code, [200, 201])
        hosp_data = res_reg.json()
        hosp_id = hosp_data["id"]
        self.assertIn(hosp_data["status"], ["DRAFT", "PENDING_APPROVAL", "SUBMITTED"])

        # 2. Hospital Approval by Platform Admin
        res_approve = self.client.patch(
            f"/api/hospitals/{hosp_id}/approval",
            headers=self._auth(self.token_p_admin),
            json={"status": "APPROVED", "rejection_reason": None}
        )
        self.assertEqual(res_approve.status_code, 200)
        approved_hosp = res_approve.json()
        self.assertEqual(approved_hosp["status"], "APPROVED")

        # 3. Hospital Configuration: Setup Admin User
        admin_user = models.User(
            email=f"admin.{prefix}@stjudehealth.org",
            full_name=f"Admin {prefix}",
            role=UserRole.HOSPITAL_ADMIN.value,
            hospital_id=hosp_id,
            password_hash=hash_password("AdminPass123!"),
            is_active=True
        )
        self.db.add(admin_user)
        self.db.commit()
        self.db.refresh(admin_user)
        token_hosp_admin = create_access_token({
            "sub": admin_user.id,
            "role": UserRole.HOSPITAL_ADMIN.value,
            "hospital_id": hosp_id
        })

        # 4. Doctor Onboarding under this Hospital
        doc_payload = {
            "full_name": f"Dr. Julian Vance, MD ({prefix})",
            "specialty": "Neurology",
            "bio": "Specialist in neuromuscular disorders and chronic migraines.",
            "consultation_fee": 250.0,
            "slot_duration_min": 30
        }
        res_doc = self.client.post(
            f"/api/doctors/hospital/{hosp_id}",
            headers=self._auth(token_hosp_admin),
            json=doc_payload
        )
        self.assertIn(res_doc.status_code, [200, 201])
        doc_data = res_doc.json()
        doc_id = doc_data["id"]
        self.assertEqual(doc_data["hospital_id"], hosp_id)

        # 5. Calendar & Availability Setup (Working Hours Mon-Fri 09:00 - 17:00)
        sched_payload = {
            "schedules": [
                {"day_of_week": i, "start_time": "09:00", "end_time": "17:00", "is_active": True}
                for i in range(5)
            ]
        }
        res_sched = self.client.post(
            f"/api/doctors/{doc_id}/schedules",
            headers=self._auth(token_hosp_admin),
            json=sched_payload
        )
        self.assertEqual(res_sched.status_code, 200)

        # 6. Verify Discrete Time Slots Generated for Doctor
        res_slots = self.client.get(f"/api/doctors/{doc_id}/slots")
        self.assertEqual(res_slots.status_code, 200)
        slots = res_slots.json()
        self.assertGreater(len(slots), 0)
        first_slot = slots[0]
        self.assertEqual(first_slot["status"], "AVAILABLE")

        # 7. Healthcare System Integration Configuration
        ehr_cfg = self.connector.get_chaos_config()
        self.assertIsNotNone(ehr_cfg)
        self.assertIn("mode", ehr_cfg)

    # =========================================================================
    # STAGE 2: PATIENT JOURNEY
    # =========================================================================
    def test_stage_2_patient_journey(self):
        """
        Patient Journey:
        Patient Registration -> Voice Conversation -> AI Intent Detection ->
        Context Resolution -> Doctor Discovery -> 7-Rule Availability -> Slot Selection & Hold.
        """
        prefix = uuid.uuid4().hex[:6]

        # 1. Patient Registration
        pat_payload = {
            "full_name": f"Eleanor Vance ({prefix})",
            "email": f"eleanor.{prefix}@example.com",
            "phone": f"+1-555-01{prefix[:4]}",
            "date_of_birth": "1988-04-12",
            "gender": "Female",
            "emergency_contact": "+1-555-999-0000"
        }
        res_pat = self.client.post("/api/patients/register", json=pat_payload)
        self.assertIn(res_pat.status_code, [200, 201])
        pat_data = res_pat.json()
        pat_id = pat_data["id"]
        token_pat = create_access_token({
            "sub": pat_id,
            "role": UserRole.PATIENT.value,
            "patient_id": pat_id
        })

        # 2. Voice Conversation Turn
        session_id = f"voice-test-{prefix}"
        voice_payload = {
            "session_id": session_id,
            "transcript": "I need to book an appointment with a neurologist for migraines.",
            "patient_id": pat_id,
            "channel": "web_voice"
        }
        res_voice = self.client.post("/api/voice/turn", json=voice_payload)
        self.assertEqual(res_voice.status_code, 200)
        voice_res = res_voice.json()

        # 3. AI Intent Detection
        self.assertIn(voice_res["intent"], ["BOOK_APPOINTMENT", "SEARCH_DOCTORS", "CHECK_AVAILABILITY"])
        self.assertIsNotNone(voice_res["spoken_text"])
        self.assertFalse(voice_res["is_emergency"])

        # 4. Context Resolution & Doctor Discovery
        res_discovery = self.client.get("/api/doctors?specialty=Neurology")
        self.assertEqual(res_discovery.status_code, 200)
        doctors = res_discovery.json()
        self.assertGreater(len(doctors), 0)
        selected_doctor = doctors[0]
        self.assertEqual(selected_doctor["specialty"], "Neurology")

        # 5. Availability Check (7-Rule Validation via /api/scheduling/availability)
        res_avail = self.client.get(f"/api/scheduling/availability?doctor_id={selected_doctor['id']}")
        self.assertEqual(res_avail.status_code, 200)
        avail_slots = [s for s in res_avail.json() if s.get("status") == "AVAILABLE" or s.get("is_bookable", True)]
        if not avail_slots:
            SlotService.generate_slots_for_doctor(self.db, selected_doctor['id'], days_ahead=7)
            res_avail = self.client.get(f"/api/scheduling/availability?doctor_id={selected_doctor['id']}")
            avail_slots = [s for s in res_avail.json() if s.get("status") == "AVAILABLE" or s.get("is_bookable", True)]
        self.assertGreater(len(avail_slots), 0)
        chosen_slot = avail_slots[0]

        # 6. Slot Selection & Provisional Hold
        hold_payload = {
            "patient_id": pat_id,
            "slot_id": chosen_slot["id"],
            "chief_complaint": "Severe chronic migraines",
            "urgency_level": "ROUTINE"
        }
        res_hold = self.client.post(
            "/api/appointments/hold",
            headers=self._auth(token_pat),
            json=hold_payload
        )
        self.assertEqual(res_hold.status_code, 200)
        hold_data = res_hold.json()
        self.assertIn("appointment_id", hold_data)
        self.assertEqual(hold_data["slot_id"], chosen_slot["id"])

    # =========================================================================
    # STAGE 3: BOOKING JOURNEY
    # =========================================================================
    def test_stage_3_booking_journey(self):
        """
        Booking Journey:
        Appointment Creation -> Mock EHR -> External Verification ->
        Internal Synchronization -> Patient Confirmation.
        """
        prefix = uuid.uuid4().hex[:6]
        doctor = self.db.query(models.Doctor).filter(models.Doctor.is_active == True).first()
        self.assertIsNotNone(doctor)

        # Create isolated slot within doctor's working hours
        slot = self._create_isolated_weekday_slot(doctor.id, days_ahead=4)

        # Create patient
        patient = models.Patient(
            full_name=f"Booking Test Patient {prefix}",
            email=f"book.{prefix}@example.com",
            phone=f"+1-555-02{prefix[:4]}",
            primary_hospital_id=doctor.hospital_id
        )
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        token_pat = create_access_token({
            "sub": patient.id,
            "role": UserRole.PATIENT.value,
            "patient_id": patient.id
        })

        idempotency_key = f"BOOK-IDEMP-{prefix.upper()}"
        correlation_id = f"BOOK-CORR-{prefix.upper()}"

        confirm_payload = {
            "slot_id": slot.id,
            "patient_id": patient.id,
            "chief_complaint": "Persistent migraines with aura",
            "urgency_level": "ROUTINE",
            "ai_triage_notes": "Triage AI auto-mapped to Neurology consult.",
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id
        }

        # 1. Appointment Confirmation API Call
        res_confirm = self.client.post(
            "/api/appointments/confirm",
            headers=self._auth(token_pat),
            json=confirm_payload
        )
        self.assertEqual(res_confirm.status_code, 200)
        appt_data = res_confirm.json()

        # 2. Verify Internal Synchronization
        self.assertEqual(appt_data["status"], "CONFIRMED")
        self.assertIsNotNone(appt_data["ehr_appointment_id"])
        self.assertTrue(appt_data["ehr_appointment_id"].startswith("EHR-"))

        # 3. Verify Slot Transitioned to BOOKED (expire session cache to read committed row)
        self.db.expire_all()
        refreshed_slot = self.db.query(models.TimeSlot).filter(models.TimeSlot.id == slot.id).first()
        self.assertEqual(refreshed_slot.status, "BOOKED")

        # 4. Verify External Verification in Mock EHR
        external_records = MockEhrService.get_all_ehr_records(self.db)
        matched_remote = [r for r in external_records if r.idempotency_key == idempotency_key]
        self.assertEqual(len(matched_remote), 1)
        self.assertIn(matched_remote[0].status, ["CONFIRMED", "CONFIRMED_IN_EHR"])

        # 5. Verify Patient Confirmation & Observability Trace
        trace_events = self.db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == correlation_id
        ).all()
        self.assertGreater(len(trace_events), 0)
        actions = [a.action for a in trace_events]
        self.assertIn("APPOINTMENT_CONFIRMED", actions)

    # =========================================================================
    # STAGE 4: FOLLOW-UP JOURNEY
    # =========================================================================
    def test_stage_4_followup_journey(self):
        """
        Follow-up Journey:
        Questionnaire Assignment -> Conversational / Structured Collection ->
        Structured Storage & Urgency -> Doctor Review -> Reminder Workflow.
        """
        prefix = uuid.uuid4().hex[:6]
        doctor = self.db.query(models.Doctor).filter(models.Doctor.is_active == True).first()

        # Create isolated slot within doctor's working hours
        slot = self._create_isolated_weekday_slot(doctor.id, days_ahead=6)

        patient = models.Patient(
            full_name=f"Followup Patient {prefix}",
            email=f"followup.{prefix}@example.com",
            phone=f"+1-555-03{prefix[:4]}",
            primary_hospital_id=doctor.hospital_id
        )
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        # Book appointment
        appt = AppointmentService.create_slot_hold(
            self.db,
            schemas.SlotHoldRequest(patient_id=patient.id, slot_id=slot.id, chief_complaint="Cardiac pain")
        )
        asyncio.run(AppointmentService.confirm_and_sync_appointment(
            self.db,
            schemas.AppointmentConfirmRequest(
                patient_id=patient.id,
                slot_id=slot.id,
                idempotency_key=appt.idempotency_key,
                chief_complaint="Cardiac pain",
                urgency_level="URGENT"
            )
        ))
        self.db.refresh(appt)

        # 1. Questionnaire Auto-Assignment
        quest_resp = QuestionnaireService.get_by_appointment(self.db, appt.id)
        if not quest_resp:
            quest_resp = QuestionnaireService.assign_questionnaire_to_appointment(self.db, appt)
        self.assertIsNotNone(quest_resp)
        self.assertEqual(quest_resp.appointment_id, appt.id)

        # 2. Structured Response Submission with Red-Flag Keyword for Urgency
        questions = quest_resp.questions or []
        first_qid = questions[0]["id"] if questions else "q_symptom"
        submit_payload = {
            "answers": {
                first_qid: "Severe radiating chest pain and shortness of breath"
            }
        }
        res_submit = self.client.post(
            f"/api/questionnaires/{appt.id}/submit",
            json=submit_payload
        )
        self.assertEqual(res_submit.status_code, 200)
        submitted_data = res_submit.json()

        # 3. Urgency Detection & Structured Storage
        self.assertEqual(submitted_data["status"], "URGENT_ESCALATED")
        self.assertTrue(submitted_data["is_urgent"])
        self.assertIsNotNone(submitted_data["patient_intake_summary"])
        self.assertIn("Intake", submitted_data["patient_intake_summary"])

        # 4. Doctor Review Queue & Clinical Sign-off
        res_review = self.client.post(
            f"/api/questionnaires/{appt.id}/review",
            json={"doctor_notes": "Reviewed intake. Prescribing immediate troponin evaluation and ECG."}
        )
        self.assertEqual(res_review.status_code, 200)
        review_data = res_review.json()
        self.assertEqual(review_data["status"], "REVIEWED")
        self.assertIn("troponin", review_data["doctor_notes"])

        # 5. Reminder / Notification Workflow Execution
        wf = asyncio.run(WorkflowEngine.execute(
            db=self.db,
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=doctor.hospital_id,
            appointment_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "patient_id": patient.id,
                "doctor_id": doctor.id,
                "start_time": str(slot.start_time)
            },
            idempotency_key=f"WF-REMIND-{appt.id}"
        ))
        self.assertIsNotNone(wf)
        self.assertIn(wf.status, ["COMPLETED", "RUNNING"])

    # =========================================================================
    # STAGE 5: OPERATIONS JOURNEY
    # =========================================================================
    def test_stage_5_operations_journey(self):
        """
        Operations Journey:
        Admin Visibility -> AI Activity -> Integration Activity ->
        Workflow Activity -> Audit Trail -> Basic Metrics.
        """
        # 1. Admin Visibility (Platform Admin Dashboard)
        res_padmin = self.client.get(
            "/api/dashboard/platform-admin",
            headers=self._auth(self.token_p_admin)
        )
        self.assertEqual(res_padmin.status_code, 200)
        padmin_data = res_padmin.json()

        # 2. Basic Operational Visibility
        self.assertIn("hospitals", padmin_data)
        self.assertIn("doctors", padmin_data)
        self.assertIn("patients", padmin_data)
        self.assertIn("appointments", padmin_data)
        self.assertGreater(len(padmin_data["hospitals"]), 0)
        self.assertGreater(len(padmin_data["doctors"]), 0)
        self.assertGreater(len(padmin_data["patients"]), 0)
        self.assertIn("total", padmin_data["appointments"])

        # 3. AI Activity & Clinical Safety Metrics
        ai_eval = padmin_data.get("ai_evaluation", {})
        self.assertIn("clinical_safety_compliance_rate", ai_eval)
        self.assertEqual(ai_eval.get("diagnostic_hallucination_count"), 0)

        # 4. Integration Activity & Resilience Rate
        analytics = padmin_data.get("analytics", {})
        self.assertIn("booking_conversion_rate", analytics)
        self.assertIn("ehr_resilience_rate", analytics)

        # 5. Workflows Activity
        res_wf = self.client.get(
            "/api/workflows",
            headers=self._auth(self.token_p_admin)
        )
        self.assertEqual(res_wf.status_code, 200)
        workflows = res_wf.json()
        self.assertIsInstance(workflows, list)

        # 6. Operational Metrics Across 4 Pillars (AI, Scheduling, Integration, Workflow)
        res_metrics = self.client.get(
            "/api/analytics/metrics",
            headers=self._auth(self.token_p_admin)
        )
        self.assertEqual(res_metrics.status_code, 200)
        metrics_data = res_metrics.json()
        self.assertIn("ai", metrics_data)
        self.assertIn("scheduling", metrics_data)
        self.assertIn("integration", metrics_data)
        self.assertIn("workflow", metrics_data)

        # 7. Audit Trail with Correlation ID Linkage
        audit_res = self.client.get(
            "/api/analytics/audit-logs?limit=10",
            headers=self._auth(self.token_p_admin)
        )
        self.assertEqual(audit_res.status_code, 200)
        audit_data = audit_res.json()
        audit_events = audit_data.get("events", [])
        self.assertGreater(len(audit_events), 0)
        self.assertIsNotNone(audit_events[0].get("correlation_id"))

    # =========================================================================
    # STAGE 6: FAILURE & RECOVERY JOURNEY
    # =========================================================================
    def test_stage_6_failure_and_recovery_journey(self):
        """
        Failure & Recovery Journey:
        1. Trigger EHR Failure (TIMEOUT_AFTER_SAVE) -> Verify Safe Classification (UNKNOWN_OUTCOME)
        2. Execute Idempotent Verification -> Zero Duplicates Created -> Local State Reconciled
        3. Trigger Unresolvable Outage (UNRESOLVED_OUTAGE) -> Verify Escalation to RECONCILIATION_REQUIRED & Slot Release.
        """
        # Part A: Test TIMEOUT_AFTER_SAVE Deterministic Recovery
        res_demo = self.client.post("/api/v1/mock-ehr/demo/simulate-timeout-recovery", json={
            "scenario": "TIMEOUT_AFTER_SAVE",
            "simulated_delay_ms": 50
        })
        self.assertEqual(res_demo.status_code, 200)
        demo_data = res_demo.json()
        self.assertEqual(demo_data["scenario"], "TIMEOUT_AFTER_SAVE")
        self.assertEqual(demo_data["classification"], "UNKNOWN_OUTCOME")
        self.assertEqual(demo_data["final_appointment_status"], "CONFIRMED")
        self.assertEqual(demo_data["final_slot_status"], "BOOKED")
        self.assertEqual(demo_data["external_duplicate_count"], 1)
        self.assertTrue(demo_data["duplicate_prevented"])

        # Part B: Test Persistent Outage Escalation & Slot Release
        res_outage = self.client.post("/api/v1/mock-ehr/demo/simulate-timeout-recovery", json={
            "scenario": "UNRESOLVED_OUTAGE",
            "simulated_delay_ms": 50
        })
        self.assertEqual(res_outage.status_code, 200)
        outage_data = res_outage.json()
        self.assertEqual(outage_data["final_appointment_status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(outage_data["final_slot_status"], "AVAILABLE")
        self.assertEqual(outage_data["recovery_action_taken"], "ESCALATED_RECONCILIATION_REQUIRED")
        self.assertIsNotNone(outage_data.get("reconciliation_record"))


if __name__ == "__main__":
    unittest.main()
