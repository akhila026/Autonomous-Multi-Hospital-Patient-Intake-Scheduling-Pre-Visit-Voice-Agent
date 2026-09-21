import time
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class VoiceCallState(str, Enum):
    IDLE = "IDLE"
    RINGING = "RINGING"
    CONNECTED = "CONNECTED"
    LISTENING = "LISTENING"
    USER_SPEAKING = "USER_SPEAKING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ESCALATED = "ESCALATED"
    DISCONNECTED = "DISCONNECTED"


class VoiceTurnEvent(BaseModel):
    event_type: str  # call_started, user_speaking, interim_transcript, final_transcript, thinking, filler, speech, barge_in, silence, call_ended
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class VoiceSession:
    """
    State manager for an active real-time voice call session (PRD Section 11).
    Tracks state transitions, turn-taking, silence timers, and latency metrics.
    """
    def __init__(
        self,
        session_id: str,
        patient_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        caller_phone: Optional[str] = None,
        channel: str = "web_voice"
    ):
        self.session_id = session_id
        self.patient_id = patient_id
        self.hospital_id = hospital_id
        self.caller_phone = caller_phone
        self.channel = channel
        self.state = VoiceCallState.IDLE
        self.started_at = time.time()
        self.last_activity_at = time.time()
        
        # Turn-taking and streaming state
        self.current_turn_index = 0
        self.user_speech_started_at: Optional[float] = None
        self.last_user_speech_at: Optional[float] = None
        self.silence_threshold_seconds: float = 1.2
        self.active_agent_task_id: Optional[str] = None
        self.is_interrupted: bool = False
        self.active_filler_sent: bool = False
        
        # Metrics
        self.total_turns: int = 0
        self.turn_latencies_ms: List[int] = []

    def set_state(self, new_state: VoiceCallState) -> VoiceCallState:
        self.state = new_state
        self.last_activity_at = time.time()
        return self.state

    def mark_user_speaking(self) -> None:
        now = time.time()
        self.user_speech_started_at = now
        self.last_user_speech_at = now
        self.is_interrupted = False
        self.active_filler_sent = False
        self.set_state(VoiceCallState.USER_SPEAKING)

    def mark_interrupted(self) -> None:
        self.is_interrupted = True
        self.set_state(VoiceCallState.INTERRUPTED)

    def check_silence(self) -> bool:
        """Returns True if user speech has ceased past the silence threshold."""
        if self.state == VoiceCallState.USER_SPEAKING and self.last_user_speech_at:
            elapsed = time.time() - self.last_user_speech_at
            return elapsed >= self.silence_threshold_seconds
        return False

    def record_turn_latency(self, start_time: float) -> int:
        latency_ms = int((time.time() - start_time) * 1000)
        self.turn_latencies_ms.append(latency_ms)
        self.total_turns += 1
        return latency_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "patient_id": self.patient_id,
            "hospital_id": self.hospital_id,
            "caller_phone": self.caller_phone,
            "channel": self.channel,
            "state": self.state.value,
            "total_turns": self.total_turns,
            "average_latency_ms": int(sum(self.turn_latencies_ms) / len(self.turn_latencies_ms)) if self.turn_latencies_ms else 0,
            "duration_seconds": int(time.time() - self.started_at)
        }


class VoiceSessionRegistry:
    """In-memory active voice session registry."""
    def __init__(self):
        self._sessions: Dict[str, VoiceSession] = {}

    def get_or_create(
        self,
        session_id: str,
        patient_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        caller_phone: Optional[str] = None,
        channel: str = "web_voice"
    ) -> VoiceSession:
        if session_id not in self._sessions:
            self._sessions[session_id] = VoiceSession(
                session_id=session_id,
                patient_id=patient_id,
                hospital_id=hospital_id,
                caller_phone=caller_phone,
                channel=channel
            )
        return self._sessions[session_id]

    def get(self, session_id: str) -> Optional[VoiceSession]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> Optional[VoiceSession]:
        return self._sessions.pop(session_id, None)


voice_session_registry = VoiceSessionRegistry()
