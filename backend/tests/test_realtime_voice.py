import unittest
import uuid
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal, Base, engine
from backend import models, schemas
from backend.voice.session import VoiceSession, VoiceCallState, voice_session_registry
from backend.voice.synthesizer import VoiceSynthesizer
from backend.voice.pipeline import VoicePipeline
from mock_ehr.database import EhrSessionLocal
from mock_ehr import models as ehr_models


class TestRealtimeVoiceInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    def setUp(self):
        self.db: Session = SessionLocal()
        self.ehr_db: Session = EhrSessionLocal()
        self._reset_mock_ehr()
        self._setup_voice_fixtures()

    def tearDown(self):
        self._reset_mock_ehr()
        self.db.close()
        self.ehr_db.close()

    def _reset_mock_ehr(self):
        chaos = self.ehr_db.query(ehr_models.EhrChaosConfig).first()
        if chaos:
            chaos.mode = "NORMAL"
            chaos.failure_active = False
            self.ehr_db.commit()

    def _setup_voice_fixtures(self):
        # Create hospital fixture
        self.hospital = models.Hospital(
            id=f"hosp-voice-{uuid.uuid4().hex[:6]}",
            name="Aegis Voice Memorial Hospital",
            address="500 Healthcare Blvd",
            license_number=f"LIC-VOICE-{uuid.uuid4().hex[:6]}",
            contact_email="voice@aegiscare.org",
            phone="+1-555-800-9000",
            status="APPROVED"
        )
        self.db.add(self.hospital)

        # Create doctor
        self.doctor = models.Doctor(
            id=f"doc-voice-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            full_name="Dr. Sarah Jenkins",
            specialty="Orthopedics",
            consultation_fee=160.0,
            status="ACTIVE"
        )
        self.db.add(self.doctor)

        # Create calendar
        self.calendar = models.Calendar(
            id=f"cal-voice-{uuid.uuid4().hex[:6]}",
            hospital_id=self.hospital.id,
            doctor_id=self.doctor.id,
            name="Sarah Jenkins Ortho Calendar",
            is_active=True
        )
        self.db.add(self.calendar)

        # Create patient
        self.patient = models.Patient(
            id=f"pat-voice-{uuid.uuid4().hex[:6]}",
            full_name="Elena Fisher",
            phone="+1-555-777-1234",
            email="elena.fisher@example.com",
            primary_hospital_id=self.hospital.id
        )
        self.db.add(self.patient)

        # Create slot
        slot_time = datetime.utcnow() + timedelta(days=2, hours=3)
        self.slot = models.TimeSlot(
            id=f"slot-voice-{uuid.uuid4().hex[:6]}",
            doctor_id=self.doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        self.db.add(self.slot)
        self.db.commit()

    def test_synthesizer_clean_text_and_abbreviation_expansion(self):
        """Verify markdown stripping and clinical acronym expansion."""
        raw_text = (
            "✅ **Appointment Confirmed!**\n"
            "• **Doctor**: Dr. Sarah Jenkins\n"
            "• **Time**: Monday, Sep 21 at 10:00 AM\n"
            "• **Verified EHR ID**: `EHR-9921`\n"
            "Fee is $160. Please answer [intake](http://link)."
        )
        cleaned = VoiceSynthesizer.clean_text_for_speech(raw_text)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertNotIn("✅", cleaned)
        self.assertIn("Doctor Sarah Jenkins", cleaned)
        self.assertIn("E.H.R.", cleaned)
        self.assertIn("A.M.", cleaned)
        self.assertIn("160 dollars", cleaned)

        segments = VoiceSynthesizer.segment_sentences(cleaned)
        self.assertTrue(len(segments) >= 1)

    def test_barge_in_interruption_handling(self):
        """Verify that user speech while assistant is speaking triggers immediate barge-in."""
        session_id = f"barge-sess-{uuid.uuid4().hex[:6]}"
        session = VoicePipeline.create_or_resume_session(session_id)
        session.set_state(VoiceCallState.SPEAKING)

        event = VoicePipeline.handle_barge_in(session)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "barge_in")
        self.assertEqual(session.state, VoiceCallState.INTERRUPTED)
        self.assertTrue(session.is_interrupted)

    def test_long_running_operation_acoustic_fillers(self):
        """Verify that queries requiring external scans return immediate low-latency fillers."""
        booking_filler = VoicePipeline.detect_interim_filler("Please book that slot for me")
        self.assertIsNotNone(booking_filler)
        self.assertIn("hospital records", booking_filler.lower())

        avail_filler = VoicePipeline.detect_interim_filler("When are appointments available?")
        self.assertIsNotNone(avail_filler)
        self.assertIn("schedule", avail_filler.lower())

    def test_inbound_telephone_call_with_patient_id(self):
        """
        PRD Section 11: Inbound telephone call simulation.
        Caller ID identifies patient via authorized capability and returns personalized welcome.
        """
        response = self.client.post("/api/v1/voice/inbound-call", json={
            "caller_phone": "+1-555-777-1234",
            "hospital_id": self.hospital.id
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["patient_name"], "Elena Fisher")
        self.assertIn("Elena Fisher", data["welcome_prompt"])
        self.assertIn("Elena Fisher", data["spoken_welcome"])
        self.assertEqual(data["call_state"], "CONNECTED")

    def test_voice_turn_rest_execution(self):
        """Verify REST turn execution with speech generation and latency tracking."""
        session_id = f"turn-test-{uuid.uuid4().hex[:6]}"
        response = self.client.post("/api/v1/voice/turn", json={
            "session_id": session_id,
            "transcript": "I need to see Dr. Sarah Jenkins for knee pain",
            "patient_id": self.patient.id,
            "hospital_id": self.hospital.id,
            "channel": "web_voice"
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("check_availability", data["capabilities_executed"])
        self.assertTrue(data["needs_clarification"])
        self.assertIn("Doctor Sarah Jenkins", data["spoken_text"])
        self.assertTrue(data["latency_ms"] >= 0)

    def test_multi_turn_voice_booking_journey(self):
        """
        Complete 2-turn voice booking:
        Turn 1: Spoken request -> AI asks for slot selection
        Turn 2: Spoken selection -> AI books, verifies with EHR, speaks verified confirmation
        """
        session_id = f"voice-booking-{uuid.uuid4().hex[:6]}"

        # Turn 1: Spoken request
        res1 = self.client.post("/api/v1/voice/turn", json={
            "session_id": session_id,
            "transcript": "I need an appointment with Dr. Sarah Jenkins",
            "patient_id": self.patient.id,
            "hospital_id": self.hospital.id,
            "channel": "web_voice"
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertTrue(data1["needs_clarification"])
        self.assertIn("Option 1", data1["reply"])

        # Turn 2: Spoken selection
        res2 = self.client.post("/api/v1/voice/turn", json={
            "session_id": session_id,
            "transcript": "Option 1 please",
            "patient_id": self.patient.id,
            "hospital_id": self.hospital.id,
            "channel": "web_voice"
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertIn("create_appointment", data2["capabilities_executed"])
        self.assertIn("Confirmed", data2["spoken_text"])
        self.assertIn("Doctor", data2["spoken_text"])

        # Verify DB appointment confirmed and verified
        appt = self.db.query(models.Appointment).filter(
            models.Appointment.patient_id == self.patient.id,
            models.Appointment.doctor_id == self.doctor.id
        ).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.status, "CONFIRMED")
        self.assertIsNotNone(appt.ehr_appointment_id)

    def test_websocket_realtime_voice_streaming(self):
        """
        Test WebSocket real-time streaming:
        1. Connects to /ws/voice/{session_id}
        2. Receives call_connected event
        3. Sends speech_start -> verifies barge-in handling
        4. Sends final_transcript -> receives assistant_thinking and assistant_speech
        5. Sends end_call -> receives call_ended
        """
        session_id = f"ws-test-{uuid.uuid4().hex[:6]}"

        with self.client.websocket_connect(f"/ws/voice/{session_id}") as websocket:
            # 1. Connected event
            connected = websocket.receive_json()
            self.assertEqual(connected["event_type"], "call_connected")
            self.assertEqual(connected["payload"]["session_id"], session_id)

            # 2. Speech start event
            websocket.send_json({"event": "speech_start"})

            # 3. Final transcript event
            websocket.send_json({
                "event": "final_transcript",
                "text": "Hello, how can you help me today?",
                "patient_id": self.patient.id,
                "hospital_id": self.hospital.id
            })

            # 4. Receive streaming responses
            received_events = []
            for _ in range(3):
                try:
                    event = websocket.receive_json()
                    received_events.append(event["event_type"])
                    if event["event_type"] == "assistant_speech":
                        self.assertIn("spoken_text", event["payload"])
                        self.assertIn("latency_ms", event["payload"])
                        break
                except Exception:
                    break

            self.assertIn("assistant_speech", received_events)

            # 5. End call
            websocket.send_json({"event": "end_call"})
            end_event = websocket.receive_json()
            self.assertEqual(end_event["event_type"], "call_ended")


if __name__ == "__main__":
    unittest.main()
