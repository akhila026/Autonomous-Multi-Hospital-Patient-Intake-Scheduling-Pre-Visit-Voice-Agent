import json
import uuid
import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db, SessionLocal
from backend.voice.session import VoiceSession, VoiceCallState, VoiceTurnEvent, voice_session_registry
from backend.voice.pipeline import VoicePipeline
from backend.voice.synthesizer import VoiceSynthesizer
from backend.capabilities.registry import capability_registry
from backend.capabilities.base import CapabilityContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["Real-Time Voice & Telephone Interface"])


# =====================================================================
# DTO SCHEMAS
# =====================================================================

class VoiceTurnRequest(BaseModel):
    session_id: str = Field(..., description="Active voice session identifier")
    transcript: str = Field(..., description="Recognized speech text")
    patient_id: Optional[str] = Field(None, description="Authenticated patient UUID if known")
    hospital_id: Optional[str] = Field(None, description="Hospital tenant ID")
    channel: str = Field("web_voice", description="web_voice or telephone")


class VoiceTurnResponse(BaseModel):
    session_id: str
    spoken_text: str
    speech_segments: List[str]
    reply: str
    interim_filler: Optional[str] = None
    intent: str
    capabilities_executed: List[str]
    needs_clarification: bool
    is_escalated: bool
    is_emergency: bool = False
    latency_ms: int
    call_state: str


class InboundCallRequest(BaseModel):
    caller_phone: str = Field(..., description="Inbound telephone caller ID / phone number")
    hospital_id: Optional[str] = Field(None, description="Inbound hospital DID tenant identifier")


class InboundCallResponse(BaseModel):
    session_id: str
    caller_phone: str
    patient_name: Optional[str] = None
    patient_id: Optional[str] = None
    hospital_id: Optional[str] = None
    welcome_prompt: str
    spoken_welcome: str
    call_state: str


# =====================================================================
# 1. HTTP VOICE TURN ENDPOINT (REST / FALLBACK)
# =====================================================================

@router.post("/turn", response_model=VoiceTurnResponse)
async def voice_turn(req: VoiceTurnRequest, db: Session = Depends(get_db)):
    """
    Executes a single voice turn:
    Voice Transcript -> AI Agent -> Controlled Capabilities -> Speech Generation.
    """
    result = await VoicePipeline.process_voice_turn(
        db=db,
        session_id=req.session_id,
        transcript=req.transcript,
        patient_id=req.patient_id,
        hospital_id=req.hospital_id,
        channel=req.channel
    )
    return VoiceTurnResponse(**result)


# =====================================================================
# 2. INBOUND TELEPHONE CALL SIMULATION (PRD Section 11)
# =====================================================================

@router.post("/inbound-call", response_model=InboundCallResponse)
async def inbound_call(req: InboundCallRequest, db: Session = Depends(get_db)):
    """
    Simulates inbound telephone call handling:
    1. Caller-ID identification via authorized lookup_patient capability.
    2. Initializes active voice session.
    3. Emits personalized telephone greeting and establishes context.
    """
    session_id = f"tel-call-{uuid.uuid4().hex[:8]}"

    # Identify caller via authorized capability (zero direct DB access)
    cap_ctx = CapabilityContext(
        correlation_id=f"TEL-INBOUND-{session_id}",
        session_id=session_id,
        tenant_id=req.hospital_id,
        channel="telephone",
        db=db
    )

    lookup_res = await capability_registry.invoke(
        "lookup_patient",
        {"phone": req.caller_phone},
        cap_ctx
    )

    patient_id = None
    patient_name = None

    if lookup_res.success and lookup_res.data and lookup_res.data.get("found"):
        pat = lookup_res.data["patient"]
        patient_id = pat["id"]
        patient_name = pat["full_name"]
        welcome = (
            f"Hello {patient_name}, welcome to AegisCare Health. "
            "I see your records on file. I can help you find doctors, check available appointment slots, "
            "book or change your visits, or answer administrative questions. How can I help you today?"
        )
    else:
        welcome = (
            "Hello and welcome to AegisCare Health Appointment Services. "
            "I can assist you with discovering specialists, checking availability, and scheduling your visits. "
            "Could you please tell me which doctor or specialty you are calling about?"
        )

    session = VoicePipeline.create_or_resume_session(
        session_id=session_id,
        patient_id=patient_id,
        hospital_id=req.hospital_id,
        caller_phone=req.caller_phone,
        channel="telephone"
    )
    session.set_state(VoiceCallState.CONNECTED)

    spoken_welcome = VoiceSynthesizer.clean_text_for_speech(welcome)

    return InboundCallResponse(
        session_id=session_id,
        caller_phone=req.caller_phone,
        patient_name=patient_name,
        patient_id=patient_id,
        hospital_id=req.hospital_id,
        welcome_prompt=welcome,
        spoken_welcome=spoken_welcome,
        call_state=session.state.value
    )


# =====================================================================
# 3. REAL-TIME WEBSOCKET STREAMING ENDPOINT (PRD Section 11)
# =====================================================================

@router.websocket("/ws/{session_id}")
async def voice_websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Bidirectional streaming WebSocket endpoint for real-time voice:
    - Streaming speech recognition transcripts
    - Turn-taking and silence handling
    - Interruption / barge-in cancellation
    - Immediate low-latency operation fillers
    - Speech synthesis events
    """
    await websocket.accept()
    session = voice_session_registry.get_or_create(session_id=session_id)
    session.set_state(VoiceCallState.CONNECTED)

    await websocket.send_text(json.dumps({
        "event_type": "call_connected",
        "payload": {"session_id": session_id, "state": "CONNECTED"}
    }))

    db: Session = SessionLocal()

    try:
        while True:
            raw_message = await websocket.receive_text()
            data = json.loads(raw_message)
            event = data.get("event")

            # 1. User speech start -> handle barge-in interruption
            if event == "speech_start":
                session.mark_user_speaking()
                barge_event = VoicePipeline.handle_barge_in(session)
                if barge_event:
                    await websocket.send_text(json.dumps(barge_event.model_dump()))

            # 2. Interim transcript streaming
            elif event == "interim_transcript":
                interim_text = data.get("text", "")
                session.mark_user_speaking()
                await websocket.send_text(json.dumps({
                    "event_type": "interim_transcript_ack",
                    "payload": {"text": interim_text}
                }))

            # 3. Final transcript -> full turn processing
            elif event in ("final_transcript", "speech_end"):
                transcript = data.get("text", "").strip()
                if not transcript:
                    continue

                patient_id = data.get("patient_id") or session.patient_id
                hospital_id = data.get("hospital_id") or session.hospital_id

                # Stream response events (thinking -> filler -> final speech)
                async for voice_event in VoicePipeline.stream_voice_events(
                    db=db,
                    session_id=session_id,
                    transcript=transcript,
                    patient_id=patient_id,
                    hospital_id=hospital_id,
                    channel=session.channel
                ):
                    await websocket.send_text(json.dumps(voice_event.model_dump()))

            # 4. Silence timeout detected by client
            elif event == "silence_timeout":
                if session.check_silence():
                    await websocket.send_text(json.dumps({
                        "event_type": "silence_detected",
                        "payload": {"session_id": session_id, "state": "SILENCE"}
                    }))

            # 5. Client requests call termination
            elif event == "end_call":
                session.set_state(VoiceCallState.DISCONNECTED)
                await websocket.send_text(json.dumps({
                    "event_type": "call_ended",
                    "payload": session.to_dict()
                }))
                break

    except WebSocketDisconnect:
        session.set_state(VoiceCallState.DISCONNECTED)
        logger.info(f"Voice session {session_id} WebSocket disconnected.")
    except Exception as e:
        session.set_state(VoiceCallState.DISCONNECTED)
        logger.error(f"Voice session {session_id} error: {str(e)}")
        try:
            await websocket.send_text(json.dumps({
                "event_type": "call_error",
                "payload": {"error": str(e), "session_id": session_id}
            }))
        except Exception:
            pass
    finally:
        db.close()
