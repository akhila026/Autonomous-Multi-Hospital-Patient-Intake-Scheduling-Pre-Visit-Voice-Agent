import time
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator
from sqlalchemy.orm import Session

from backend.voice.session import VoiceSession, VoiceCallState, VoiceTurnEvent, voice_session_registry
from backend.voice.synthesizer import VoiceSynthesizer
from backend.ai.agent import AIPatientAccessAgent, AgentTurnResponse


class VoicePipeline:
    """
    Real-Time Voice Pipeline (PRD Section 11).
    Orchestrates real-time speech streaming, turn-taking, interruption/barge-in,
    silence detection, long-running operation fillers, and speech generation.
    Strictly delegates all reasoning, capability calling, and verification to AIPatientAccessAgent.
    The voice pipeline NEVER directly accesses the database or external EHR.
    """

    @staticmethod
    def create_or_resume_session(
        session_id: str,
        patient_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        caller_phone: Optional[str] = None,
        channel: str = "web_voice"
    ) -> VoiceSession:
        return voice_session_registry.get_or_create(
            session_id=session_id,
            patient_id=patient_id,
            hospital_id=hospital_id,
            caller_phone=caller_phone,
            channel=channel
        )

    @staticmethod
    def handle_barge_in(session: VoiceSession) -> Optional[VoiceTurnEvent]:
        """
        Handles patient interruption / barge-in.
        When user speaks while assistant is speaking, aborts assistant playback and sets state to INTERRUPTED.
        """
        if session.state in (VoiceCallState.SPEAKING, VoiceCallState.THINKING):
            session.mark_interrupted()
            return VoiceTurnEvent(
                event_type="barge_in",
                payload={"message": "Assistant speech playback aborted due to user barge-in.", "session_id": session.session_id}
            )
        return None

    @staticmethod
    def detect_interim_filler(transcript: str) -> Optional[str]:
        """
        Generates immediate acoustic filler for operations that require external queries or scheduling scans
        to ensure perceived conversational latency stays well under 2 seconds.
        """
        lower = transcript.lower()
        if any(w in lower for w in ["book", "confirm", "reserve"]):
            return "One moment please, reserving your slot and verifying with the hospital records system..."
        elif any(w in lower for w in ["availability", "slots", "when", "free", "time"]):
            return "Checking the physician's schedule right now, one moment..."
        elif any(w in lower for w in ["doctor", "specialist", "find"]):
            return "Searching our approved healthcare directory for specialists, one moment..."
        elif any(w in lower for w in ["reschedule", "move", "change"]):
            return "Looking up your appointment to reschedule, one second..."
        elif any(w in lower for w in ["cancel"]):
            return "Retrieving your appointment details to process cancellation..."
        return None

    @staticmethod
    async def process_voice_turn(
        db: Session,
        session_id: str,
        transcript: str,
        patient_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        channel: str = "web_voice"
    ) -> Dict[str, Any]:
        """
        Processes a complete spoken turn through the existing AI Agent,
        formats speech output, calculates latency, and updates voice session state.
        """
        session = VoicePipeline.create_or_resume_session(
            session_id=session_id,
            patient_id=patient_id,
            hospital_id=hospital_id,
            channel=channel
        )

        turn_start = time.time()
        session.set_state(VoiceCallState.THINKING)

        # 1. Identify if long-running operation filler is suitable
        filler_text = VoicePipeline.detect_interim_filler(transcript)

        # 2. Delegate directly to the existing AIPatientAccessAgent
        # Uses existing conversation context, controlled capabilities, and EHR verification
        turn_response: AgentTurnResponse = await AIPatientAccessAgent.process_turn(
            db=db,
            session_id=session_id,
            message=transcript,
            patient_id=patient_id,
            hospital_id=hospital_id,
            channel=channel
        )

        latency_ms = session.record_turn_latency(turn_start)

        # 3. Speech Generation & Prosody Formatting
        spoken_text = VoiceSynthesizer.clean_text_for_speech(turn_response.reply)
        speech_segments = VoiceSynthesizer.segment_sentences(spoken_text)

        # 4. Update session state
        if turn_response.is_escalated:
            session.set_state(VoiceCallState.ESCALATED)
        else:
            session.set_state(VoiceCallState.SPEAKING)

        return {
            "session_id": session_id,
            "spoken_text": spoken_text,
            "speech_segments": speech_segments,
            "reply": turn_response.reply,
            "interim_filler": filler_text,
            "intent": turn_response.intent,
            "capabilities_executed": turn_response.capabilities_executed,
            "needs_clarification": turn_response.needs_clarification,
            "is_escalated": turn_response.is_escalated,
            "is_emergency": turn_response.is_emergency,
            "latency_ms": latency_ms,
            "call_state": session.state.value
        }

    @staticmethod
    async def stream_voice_events(
        db: Session,
        session_id: str,
        transcript: str,
        patient_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        channel: str = "web_voice"
    ) -> AsyncGenerator[VoiceTurnEvent, None]:
        """
        Asynchronous generator for real-time WebSocket voice streaming events:
        Yields interim filler -> thinking -> final speech segments -> call state.
        """
        session = VoicePipeline.create_or_resume_session(
            session_id=session_id,
            patient_id=patient_id,
            hospital_id=hospital_id,
            channel=channel
        )

        turn_start = time.time()
        session.set_state(VoiceCallState.THINKING)
        yield VoiceTurnEvent(
            event_type="assistant_thinking",
            payload={"session_id": session_id, "state": "THINKING"}
        )

        # Emit low-latency filler if operation involves external queries
        filler = VoicePipeline.detect_interim_filler(transcript)
        if filler:
            session.active_filler_sent = True
            yield VoiceTurnEvent(
                event_type="assistant_filler",
                payload={"filler_text": filler, "session_id": session_id}
            )

        # Execute turn through existing AI Patient Access Agent
        turn_response: AgentTurnResponse = await AIPatientAccessAgent.process_turn(
            db=db,
            session_id=session_id,
            message=transcript,
            patient_id=patient_id,
            hospital_id=hospital_id,
            channel=channel
        )

        latency_ms = session.record_turn_latency(turn_start)

        # If user interrupted while thinking was executing, abort speech output
        if session.is_interrupted:
            yield VoiceTurnEvent(
                event_type="interrupted",
                payload={"session_id": session_id, "message": "Speech output cancelled by barge-in"}
            )
            return

        spoken_text = VoiceSynthesizer.clean_text_for_speech(turn_response.reply)
        speech_segments = VoiceSynthesizer.segment_sentences(spoken_text)

        session.set_state(VoiceCallState.ESCALATED if turn_response.is_escalated else VoiceCallState.SPEAKING)

        yield VoiceTurnEvent(
            event_type="assistant_speech",
            payload={
                "spoken_text": spoken_text,
                "speech_segments": speech_segments,
                "raw_reply": turn_response.reply,
                "intent": turn_response.intent,
                "capabilities_executed": turn_response.capabilities_executed,
                "needs_clarification": turn_response.needs_clarification,
                "is_escalated": turn_response.is_escalated,
                "is_emergency": turn_response.is_emergency,
                "latency_ms": latency_ms,
                "call_state": session.state.value
            }
        )
