from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend import schemas, models
from backend.auth.roles import UserRole
from backend.auth.dependencies import get_optional_current_user
from backend.services.ai_triage_service import AITriageService
from backend.ai.agent import AIPatientAccessAgent, AgentTurnResponse
from backend.ai.context import ConversationContextManager
from backend.capabilities.registry import capability_registry
from backend.capabilities.base import CapabilityContext

router = APIRouter(prefix="/ai", tags=["AI Patient Access Agent"])


class AIAgentChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique dialogue session identifier")
    message: str = Field(..., description="User message or voice transcript turn")
    patient_id: Optional[str] = Field(None, description="Authenticated patient UUID if known")
    hospital_id: Optional[str] = Field(None, description="Active hospital tenant ID")
    channel: str = Field("web_voice", description="web_voice, chat, or telephone")


class AIAgentResetRequest(BaseModel):
    session_id: str


# =====================================================================
# 1. AI PATIENT-ACCESS AGENT CHAT ENDPOINT
# =====================================================================

@router.post("/agent/chat", response_model=AgentTurnResponse)
async def agent_chat(req: AIAgentChatRequest, db: Session = Depends(get_db)):
    """
    Main turn-by-turn conversational interaction with the AI Patient-Access Agent.
    Orchestrates intent detection, reference resolution, controlled capabilities,
    and clinical guardrails.
    """
    return await AIPatientAccessAgent.process_turn(
        db=db,
        session_id=req.session_id,
        message=req.message,
        patient_id=req.patient_id,
        hospital_id=req.hospital_id,
        channel=req.channel
    )


# =====================================================================
# 2. CONTEXT & STATE INSPECTION ENDPOINTS
# =====================================================================

@router.get("/agent/context/{session_id}")
def get_session_context(
    session_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves durable conversational context for a given session ID.
    Enforces Patient Ownership: A patient can only inspect their own conversational session context.
    """
    ctx = ConversationContextManager.get_or_create(db, session_id)

    if current_user and current_user.role == UserRole.PATIENT.value:
        patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
        if not patient_id:
            p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
            patient_id = p.id if p else current_user.id
        if ctx.patient_id and ctx.patient_id != patient_id and ctx.patient_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You are not authorized to access another patient's conversational context."
            )

    return {
        "session_id": ctx.session_id,
        "patient_id": ctx.patient_id,
        "hospital_id": ctx.hospital_id,
        "channel": ctx.channel,
        "current_intent": ctx.current_intent,
        "selected_hospital": ctx.selected_hospital,
        "selected_doctor": ctx.selected_doctor,
        "selected_slot": ctx.selected_slot,
        "current_appointment": ctx.current_appointment,
        "relevant_preferences": ctx.relevant_preferences,
        "communication_preferences": ctx.communication_preferences,
        "completed_workflow_state": ctx.completed_workflow_state,
        "turns_count": len(ctx.turns)
    }


@router.post("/agent/reset/{session_id}")
def reset_session_context(
    session_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Resets conversational context working memory for a fresh dialogue.
    Enforces Patient Ownership: A patient cannot reset another patient's session.
    """
    conv = db.query(models.AIConversation).filter(models.AIConversation.session_id == session_id).first()
    if conv:
        if current_user and current_user.role == UserRole.PATIENT.value:
            patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
            if not patient_id:
                p = db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
                patient_id = p.id if p else current_user.id
            if conv.patient_id and conv.patient_id != patient_id and conv.patient_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient Ownership Violation: You are not authorized to reset another patient's conversational context."
                )

        if conv.ai_context:
            db.delete(conv.ai_context)
        db.delete(conv)
        db.commit()
    return {"session_id": session_id, "status": "RESET_SUCCESSFUL"}


# =====================================================================
# 3. DIRECT HUMAN ESCALATION ENDPOINT
# =====================================================================

class HumanEscalationRequest(BaseModel):
    session_id: str
    reason: str
    urgency: str = "ROUTINE"

@router.post("/agent/escalate")
async def escalate_to_human(req: HumanEscalationRequest, db: Session = Depends(get_db)):
    """Directly escalates active session to a human clinical operator."""
    cap_ctx = CapabilityContext(
        correlation_id=f"ESC-{req.session_id}",
        session_id=req.session_id,
        db=db
    )
    result = await capability_registry.invoke(
        "transfer_to_human",
        {"session_id": req.session_id, "reason": req.reason, "urgency": req.urgency},
        cap_ctx
    )
    return result.data if result.success else {"error": result.error}


# =====================================================================
# 4. PRESERVED LEGACY TRIAGE ENDPOINT (FOR BACKWARD COMPATIBILITY)
# =====================================================================

@router.post("/triage/chat", response_model=schemas.AITriageChatResponse)
def triage_chat(req: schemas.AITriageChatRequest, db: Session = Depends(get_db)):
    return AITriageService.process_triage(db, req)
