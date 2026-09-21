import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend import models, schemas
from backend.auth.dependencies import get_current_user, require_platform_admin, require_admin_or_platform
from backend.auth.roles import UserRole
from backend.services.observability_service import ObservabilityService, BookingTraceStage
from backend.audit.logger import AuditLogger, AuditCategory
from backend.audit.privacy import redact_sensitive_data

logger = logging.getLogger("healthcare.analytics.router")

router = APIRouter(prefix="/analytics", tags=["Analytics, Observability & Auditing"])


# =====================================================================
# 1. FOUR-PILLAR OPERATIONAL METRICS
# =====================================================================

@router.get("/metrics")
def get_operational_metrics(
    hospital_id: Optional[str] = Query(None, description="Optional facility ID to scope metrics"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns unified operational metrics across the 4 core pillars:
    - AI (conversations, latency, capability success/failure, escalation, usage, cost)
    - Scheduling (booking success, availability, cancellations, rescheduling, utilization)
    - Integration (requests, success/failure, verification, retry, recovery, reconciliation, unknown outcomes)
    - Workflow (running, completed, failed, retried, duration, notifications)
    Enforces multi-tenant isolation for Hospital Admins.
    """
    target_hosp_id = hospital_id
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if hospital_id and hospital_id != current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation: You cannot access metrics for another hospital."
            )
        target_hosp_id = current_user.hospital_id

    metrics = ObservabilityService.get_aggregated_metrics(hospital_id=target_hosp_id, db=db)
    return metrics


# =====================================================================
# 2. END-TO-END 9-STAGE DISTRIBUTED TRACE INSPECTION
# =====================================================================

@router.get("/trace/{correlation_id}")
def get_booking_trace(
    correlation_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves the complete 9-stage chronological timeline for a given correlation ID:
    Conversation -> AI Decision -> Capability -> Scheduling -> EHR Operation ->
    Verification -> Synchronization -> Workflow -> Notification.
    """
    trace = ObservabilityService.get_booking_trace(correlation_id=correlation_id, db=db)
    if not trace or not trace.get("timeline"):
        raise HTTPException(status_code=404, detail="Trace not found for correlation ID.")

    # Multi-tenant check: if hospital admin, verify trace belongs to their facility
    if current_user.role == UserRole.HOSPITAL_ADMIN.value and trace.get("tenant_id"):
        if trace["tenant_id"] != current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation: You cannot view traces for another hospital."
            )

    return trace


@router.get("/traces")
def list_recent_traces(
    limit: int = Query(25, ge=1, le=100),
    hospital_id: Optional[str] = Query(None),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lists recent booking operations with summary status badges for each stage."""
    target_hosp_id = hospital_id
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        target_hosp_id = current_user.hospital_id

    traces = ObservabilityService.list_recent_traces(limit=limit, tenant_id=target_hosp_id, db=db)
    return {"traces": traces}


# =====================================================================
# 3. FILTERABLE AUDIT LOGS (8 CATEGORIES, HIPAA-REDACTED)
# =====================================================================

@router.get("/audit-logs")
def get_audit_logs(
    category: Optional[str] = Query(None, description="Category filter (e.g. LOGIN_ACCESS, APPOINTMENT_OPERATIONS, AI_ACTIONS, etc.)"),
    action: Optional[str] = Query(None),
    actor_role: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    correlation_id: Optional[str] = Query(None),
    hospital_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns audit trail records strictly scoped and sanitized.
    Supports filtering across all 8 mandatory audit event categories:
    - LOGIN_ACCESS
    - APPOINTMENT_OPERATIONS
    - PATIENT_DATA_ACCESS
    - AI_ACTIONS
    - CAPABILITY_EXECUTIONS
    - INTEGRATION_OPERATIONS
    - CONFIGURATION_CHANGES
    - ADMINISTRATIVE_ACTIONS
    """
    query = db.query(models.AuditEvent)

    # Multi-tenant isolation: Hospital Admins are strictly scoped to their facility
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        query = query.filter(models.AuditEvent.hospital_id == current_user.hospital_id)
    elif hospital_id:
        query = query.filter(models.AuditEvent.hospital_id == hospital_id)

    if category:
        cat_upper = category.upper()
        query = query.filter(
            models.AuditEvent.action.like(f"{cat_upper}:%") | 
            models.AuditEvent.action.like(f"{cat_upper}_%") |
            models.AuditEvent.details.contains({"category": cat_upper})
        )

    if action:
        query = query.filter(models.AuditEvent.action.ilike(f"%{action}%"))

    if actor_role:
        query = query.filter(models.AuditEvent.actor_role == actor_role.upper())

    if status_filter:
        query = query.filter(models.AuditEvent.status == status_filter.upper())

    if correlation_id:
        query = query.filter(models.AuditEvent.correlation_id == correlation_id)

    total_count = query.count()
    events = query.order_by(models.AuditEvent.created_at.desc()).offset(offset).limit(limit).all()

    # Ensure all returned details are sanitized
    sanitized_events = [
        {
            "id": ev.id,
            "correlation_id": ev.correlation_id,
            "hospital_id": ev.hospital_id,
            "actor_id": ev.actor_id,
            "actor_role": ev.actor_role or "SYSTEM",
            "action": ev.action,
            "category": (ev.details or {}).get("category") or ev.action.split(":")[0],
            "resource_type": ev.resource_type,
            "resource_id": ev.resource_id,
            "status": ev.status,
            "details": redact_sensitive_data(ev.details or {}),
            "created_at": ev.created_at.isoformat() if ev.created_at else None
        }
        for ev in events
    ]

    return {
        "total": total_count,
        "offset": offset,
        "limit": limit,
        "categories": [c.value for c in AuditCategory],
        "events": sanitized_events
    }


# =====================================================================
# 4. DEMO END-TO-END BOOKING TRACE SIMULATOR
# =====================================================================

@router.post("/trace/demo-booking")
async def trigger_demo_booking_trace(
    current_user: models.User = Depends(require_admin_or_platform),
    db: Session = Depends(get_db)
):
    """
    Demonstrates the complete 9-stage booking operation trace in one click:
    Conversation -> AI Decision -> Capability -> Scheduling -> EHR Operation ->
    Verification -> Synchronization -> Workflow -> Notification.
    """
    from backend.services.appointment_service import AppointmentService

    # Find candidate slot and patient
    hosp = db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").first()
    if not hosp:
        raise HTTPException(status_code=400, detail="No approved hospital found.")

    doc = db.query(models.Doctor).filter(models.Doctor.hospital_id == hosp.id, models.Doctor.is_active == True).first()
    if not doc:
        raise HTTPException(status_code=400, detail="No active doctor found.")

    pat = db.query(models.Patient).first()
    if not pat:
        raise HTTPException(status_code=400, detail="No patient found.")

    slot = db.query(models.TimeSlot).filter(
        models.TimeSlot.doctor_id == doc.id,
        models.TimeSlot.status == "AVAILABLE"
    ).first()

    if not slot:
        # Create a fresh test slot
        slot = models.TimeSlot(
            doctor_id=doc.id,
            start_time=datetime.utcnow() + timedelta(days=2, hours=10),
            end_time=datetime.utcnow() + timedelta(days=2, hours=10, minutes=30),
            status="AVAILABLE"
        )
        db.add(slot)
        db.commit()
        db.refresh(slot)

    correlation_id = f"CORR-DEMO-{uuid.uuid4().hex[:8]}"

    # 1. Stage 1: CONVERSATION
    ObservabilityService.record_stage(
        correlation_id=correlation_id,
        stage=BookingTraceStage.CONVERSATION,
        status="COMPLETED",
        details={"session_id": f"sess-demo-{correlation_id}", "channel": "web_voice", "transcript_snippet": "I would like to book an appointment with Dr. Jenkins."},
        actor_id=pat.id,
        actor_role="PATIENT",
        tenant_id=hosp.id,
        db=db
    )

    # 2. Stage 2: AI_DECISION
    ObservabilityService.record_stage(
        correlation_id=correlation_id,
        stage=BookingTraceStage.AI_DECISION,
        status="COMPLETED",
        details={"intent": "BOOK_APPOINTMENT", "urgency_level": "ROUTINE", "selected_doctor_id": doc.id, "selected_slot_id": slot.id},
        actor_id="AI_PATIENT_ACCESS_AGENT",
        actor_role="AI_AGENT",
        tenant_id=hosp.id,
        db=db
    )

    # 3. Stage 3: CAPABILITY
    ObservabilityService.record_stage(
        correlation_id=correlation_id,
        stage=BookingTraceStage.CAPABILITY,
        status="COMPLETED",
        details={"capability": "create_appointment", "slot_id": slot.id, "patient_id": pat.id},
        actor_id="CAPABILITY_REGISTRY",
        actor_role="SYSTEM",
        tenant_id=hosp.id,
        db=db
    )

    # 4-7. Stages 4, 5, 6, 7 through confirm_and_sync_appointment
    confirm_req = schemas.AppointmentConfirmRequest(
        slot_id=slot.id,
        patient_id=pat.id,
        chief_complaint="Demonstration trace consultation",
        urgency_level="ROUTINE",
        idempotency_key=f"DEMO-IDEMP-{correlation_id}",
        correlation_id=correlation_id
    )
    appt = await AppointmentService.confirm_and_sync_appointment(db, confirm_req)

    # Return reconstructed trace
    trace = ObservabilityService.get_booking_trace(correlation_id=correlation_id, db=db)
    return {
        "status": "TRACE_DEMO_COMPLETED",
        "correlation_id": correlation_id,
        "appointment_id": appt.id,
        "trace": trace
    }
