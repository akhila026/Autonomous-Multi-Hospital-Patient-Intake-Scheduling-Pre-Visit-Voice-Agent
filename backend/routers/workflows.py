import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend import models, schemas
from backend.database import get_db
from backend.auth.dependencies import get_optional_current_user
from backend.workflows.engine import WorkflowEngine
from backend.workflows.events import event_dispatcher, DomainEvent, EventType
from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflows-and-events"])


# =====================================================================
# SCHEMAS
# =====================================================================

class StartWorkflowRequest(BaseModel):
    workflow_type: str = Field(..., description="e.g. POST_BOOKING_WORKFLOW, APPOINTMENT_CANCELLATION_WORKFLOW, EHR_RECOVERY_WORKFLOW, CLINICAL_URGENCY_WORKFLOW")
    hospital_id: str
    appointment_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    max_retries: int = 3
    scheduled_for: Optional[datetime] = None


class PublishEventRequest(BaseModel):
    event_type: EventType
    entity_id: str
    tenant_id: Optional[str] = None
    correlation_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None


class SendNotificationRequest(BaseModel):
    recipient_type: str = Field("PATIENT", description="PATIENT, DOCTOR, HOSPITAL")
    recipient_contact: str
    channel: str = Field("SMS", description="SMS, EMAIL, WHATSAPP, VOICE")
    template: str
    context: Dict[str, Any] = Field(default_factory=dict)
    scheduled_for: Optional[datetime] = None
    hospital_id: Optional[str] = None
    appointment_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# =====================================================================
# WORKFLOW ENDPOINTS
# =====================================================================

@router.get("/workflows")
def list_workflows(
    hospital_id: Optional[str] = None,
    workflow_type: Optional[str] = None,
    status: Optional[str] = None,
    appointment_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Lists asynchronous and scheduled workflows."""
    workflows = WorkflowEngine.list_workflows(
        db=db,
        hospital_id=hospital_id,
        workflow_type=workflow_type,
        status=status,
        appointment_id=appointment_id,
        limit=limit
    )
    return [
        {
            "id": w.id,
            "hospital_id": w.hospital_id,
            "appointment_id": w.appointment_id,
            "workflow_type": w.workflow_type,
            "status": w.status,
            "current_step": w.current_step,
            "idempotency_key": w.idempotency_key,
            "retry_count": w.retry_count,
            "max_retries": w.max_retries,
            "scheduled_for": w.scheduled_for.isoformat() if w.scheduled_for else None,
            "payload": w.payload,
            "execution_history": w.execution_history,
            "error_message": w.error_message,
            "completed_at": w.completed_at.isoformat() if w.completed_at else None,
            "failed_at": w.failed_at.isoformat() if w.failed_at else None,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None
        }
        for w in workflows
    ]


@router.get("/workflows/{workflow_id}")
def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Fetches details and execution history of a specific workflow."""
    wf = WorkflowEngine.get_workflow(db, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {
        "id": wf.id,
        "hospital_id": wf.hospital_id,
        "appointment_id": wf.appointment_id,
        "workflow_type": wf.workflow_type,
        "status": wf.status,
        "current_step": wf.current_step,
        "idempotency_key": wf.idempotency_key,
        "retry_count": wf.retry_count,
        "max_retries": wf.max_retries,
        "scheduled_for": wf.scheduled_for.isoformat() if wf.scheduled_for else None,
        "payload": wf.payload,
        "execution_history": wf.execution_history,
        "error_message": wf.error_message,
        "completed_at": wf.completed_at.isoformat() if wf.completed_at else None,
        "failed_at": wf.failed_at.isoformat() if wf.failed_at else None,
        "created_at": wf.created_at.isoformat() if wf.created_at else None
    }


@router.post("/workflows/start")
async def start_workflow(
    req: StartWorkflowRequest,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Starts or schedules a workflow."""
    try:
        wf = await WorkflowEngine.execute(
            db=db,
            workflow_type=req.workflow_type,
            hospital_id=req.hospital_id,
            appointment_id=req.appointment_id,
            payload=req.payload,
            idempotency_key=req.idempotency_key,
            max_retries=req.max_retries,
            scheduled_for=req.scheduled_for
        )
        return {
            "workflow_id": wf.id,
            "workflow_type": wf.workflow_type,
            "status": wf.status,
            "current_step": wf.current_step,
            "idempotency_key": wf.idempotency_key,
            "execution_history": wf.execution_history
        }
    except Exception as ex:
        logger.error(f"Failed to start workflow: {ex}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/workflows/{workflow_id}/retry")
async def retry_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Retries a failed workflow from its current state."""
    try:
        wf = await WorkflowEngine.retry_workflow(db, workflow_id)
        return {
            "workflow_id": wf.id,
            "status": wf.status,
            "retry_count": wf.retry_count,
            "execution_history": wf.execution_history
        }
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


# =====================================================================
# NOTIFICATION ENDPOINTS
# =====================================================================

@router.get("/notifications")
def list_notifications(
    recipient_type: Optional[str] = None,
    hospital_id: Optional[str] = None,
    appointment_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Lists multi-party notifications across channels."""
    notifs = NotificationService.get_notifications(
        db=db,
        recipient_type=recipient_type,
        hospital_id=hospital_id,
        appointment_id=appointment_id,
        status=status,
        limit=limit
    )
    return [
        {
            "id": n.id,
            "hospital_id": n.hospital_id,
            "appointment_id": n.appointment_id,
            "recipient_type": n.recipient_type,
            "recipient_contact": n.recipient_contact,
            "channel": n.channel,
            "template": n.template,
            "message_content": n.message_content,
            "status": n.status,
            "scheduled_for": n.scheduled_for.isoformat() if n.scheduled_for else None,
            "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "idempotency_key": n.idempotency_key
        }
        for n in notifs
    ]


@router.post("/notifications/send")
def send_notification(
    req: SendNotificationRequest,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Creates and delivers or schedules a notification."""
    notif = NotificationService.create_or_schedule_notification(
        db=db,
        recipient_type=req.recipient_type,
        recipient_contact=req.recipient_contact,
        template=req.template,
        context=req.context,
        channel=req.channel,
        scheduled_for=req.scheduled_for,
        hospital_id=req.hospital_id,
        appointment_id=req.appointment_id,
        idempotency_key=req.idempotency_key
    )
    return {
        "notification_id": notif.id,
        "status": notif.status,
        "message_content": notif.message_content,
        "scheduled_for": notif.scheduled_for.isoformat() if notif.scheduled_for else None
    }


@router.post("/notifications/dispatch")
def dispatch_due_notifications(
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Scans and dispatches all scheduled notifications that are due."""
    count = NotificationService.dispatch_due_notifications(db)
    return {"dispatched_count": count}


# =====================================================================
# DOMAIN EVENT ENDPOINTS
# =====================================================================

@router.post("/events/publish")
async def publish_event(
    req: PublishEventRequest,
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Publishes a domain event to the platform pub/sub event bus."""
    event = DomainEvent(
        event_type=req.event_type,
        entity_id=req.entity_id,
        tenant_id=req.tenant_id,
        correlation_id=req.correlation_id or f"CORR-{datetime.utcnow().timestamp()}",
        payload=req.payload,
        actor_id=req.actor_id or (current_user.id if current_user else "SYSTEM"),
        actor_role=req.actor_role or (current_user.role if current_user else "API")
    )
    await event_dispatcher.dispatch(event)
    return {
        "status": "DISPATCHED",
        "event_id": event.id,
        "event_type": event.event_type.value,
        "entity_id": event.entity_id,
        "correlation_id": event.correlation_id
    }


@router.get("/events/history")
def get_event_history(
    limit: int = Query(50, ge=1, le=100),
    event_type: Optional[str] = None,
    current_user: Optional[models.User] = Depends(get_optional_current_user)
):
    """Retrieves recent dispatched domain events."""
    events = event_dispatcher.get_history(limit=limit, event_type=event_type)
    return [
        {
            "id": e.id,
            "event_type": e.event_type.value,
            "entity_id": e.entity_id,
            "tenant_id": e.tenant_id,
            "correlation_id": e.correlation_id,
            "payload": e.payload,
            "actor_id": e.actor_id,
            "actor_role": e.actor_role,
            "timestamp": e.timestamp.isoformat()
        }
        for e in events
    ]


@router.get("/events/metrics")
def get_event_metrics(current_user: Optional[models.User] = Depends(get_optional_current_user)):
    """Retrieves domain event dispatch metrics."""
    return {
        "metrics": event_dispatcher.get_metrics()
    }
