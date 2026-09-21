import asyncio
import uuid
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable, Awaitable

from sqlalchemy.orm import Session
from backend import models
from backend.database import SessionLocal
from backend.workflows.events import event_dispatcher, DomainEvent, EventType
from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class WorkflowContext:
    """Execution context passed through each workflow step."""
    def __init__(
        self,
        db: Session,
        workflow_id: str,
        workflow_type: str,
        hospital_id: str,
        appointment_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ):
        self.db = db
        self.workflow_id = workflow_id
        self.workflow_type = workflow_type
        self.hospital_id = hospital_id
        self.appointment_id = appointment_id
        self.payload = payload or {}
        self.correlation_id = correlation_id or f"CORR-{uuid.uuid4().hex[:8]}"
        self.state: Dict[str, Any] = {}
        self.step_index: int = 0


class WorkflowStep:
    """Individual workflow step supporting conditional execution, delays, and retries."""
    def __init__(
        self,
        name: str,
        handler: Callable[[WorkflowContext], Awaitable[Dict[str, Any]]],
        condition: Optional[Callable[[WorkflowContext], bool]] = None,
        delay_seconds: float = 0.0,
        max_retries: int = 3,
        backoff_factor: float = 1.0
    ):
        self.name = name
        self.handler = handler
        self.condition = condition
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor


class WorkflowDefinition:
    """Defines a sequence of steps for a specific workflow type."""
    def __init__(self, workflow_type: str, steps: List[WorkflowStep]):
        self.workflow_type = workflow_type
        self.steps = steps


class WorkflowEngine:
    """
    Asynchronous and Scheduled Workflow Execution Engine (PRD Section 16 & 22).
    Features:
    - Delays & Scheduling (relative seconds or scheduled_for)
    - Conditional branching / step filtering
    - Automatic step-level & workflow-level retries with backoff
    - Idempotency via unique idempotency_key
    - Step-by-step execution history tracking
    - Failure states, automated dead-letter handling & human escalation
    """
    _registry: Dict[str, WorkflowDefinition] = {}

    @classmethod
    def register(cls, definition: WorkflowDefinition):
        cls._registry[definition.workflow_type] = definition

    @classmethod
    def get_definition(cls, workflow_type: str) -> Optional[WorkflowDefinition]:
        return cls._registry.get(workflow_type)

    @classmethod
    async def execute(
        cls,
        db: Session,
        workflow_type: str,
        hospital_id: str,
        appointment_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        max_retries: int = 3,
        scheduled_for: Optional[datetime] = None,
        correlation_id: Optional[str] = None
    ) -> models.Workflow:
        """
        Executes or schedules a registered workflow definition.
        Respects idempotency to prevent duplicate runs.
        """
        definition = cls.get_definition(workflow_type)
        if not definition:
            raise ValueError(f"Unknown workflow type: {workflow_type}")

        # Idempotency check
        if idempotency_key:
            existing = db.query(models.Workflow).filter(
                models.Workflow.idempotency_key == idempotency_key
            ).first()
            if existing:
                if existing.status in ("COMPLETED", "RUNNING"):
                    logger.info(f"Workflow {idempotency_key} already exists with status {existing.status}. Returning.")
                    return existing

        now = datetime.utcnow()
        workflow = models.Workflow(
            hospital_id=hospital_id,
            appointment_id=appointment_id,
            workflow_type=workflow_type,
            status="RUNNING" if not scheduled_for or scheduled_for <= now else "PENDING",
            current_step=0,
            idempotency_key=idempotency_key or f"WF-{workflow_type}-{uuid.uuid4().hex[:8]}",
            retry_count=0,
            max_retries=max_retries,
            scheduled_for=scheduled_for,
            payload=payload or {},
            execution_history=[],
            created_at=now
        )
        db.add(workflow)
        db.commit()
        db.refresh(workflow)

        corr_id = correlation_id or f"CORR-WF-{workflow.id[:8]}"

        # Emit WORKFLOW_STARTED event
        await event_dispatcher.dispatch(DomainEvent(
            event_type=EventType.WORKFLOW_STARTED,
            entity_id=workflow.id,
            tenant_id=hospital_id,
            correlation_id=corr_id,
            payload={
                "workflow_type": workflow_type,
                "appointment_id": appointment_id,
                "idempotency_key": workflow.idempotency_key,
                "scheduled_for": scheduled_for.isoformat() if scheduled_for else None
            }
        ))

        # If scheduled for the future, don't execute steps immediately
        if scheduled_for and scheduled_for > now:
            return workflow

        # Execute steps asynchronously
        return await cls._run_steps(db, workflow, definition, corr_id)

    @classmethod
    async def _run_steps(
        cls,
        db: Session,
        workflow: models.Workflow,
        definition: WorkflowDefinition,
        correlation_id: str
    ) -> models.Workflow:
        """Runs the sequence of steps for a workflow."""
        context = WorkflowContext(
            db=db,
            workflow_id=workflow.id,
            workflow_type=workflow.workflow_type,
            hospital_id=workflow.hospital_id,
            appointment_id=workflow.appointment_id,
            payload=workflow.payload or {},
            correlation_id=correlation_id
        )

        history = list(workflow.execution_history or [])
        failed = False
        final_error = None

        for idx, step in enumerate(definition.steps):
            context.step_index = idx
            workflow.current_step = idx
            step_record = {
                "step": step.name,
                "started_at": datetime.utcnow().isoformat(),
                "status": "RUNNING",
                "retries": 0
            }

            # 1. Condition Check
            if step.condition is not None:
                try:
                    should_run = step.condition(context)
                    if not should_run:
                        step_record["status"] = "SKIPPED"
                        step_record["reason"] = "Condition evaluated to False"
                        step_record["completed_at"] = datetime.utcnow().isoformat()
                        history.append(step_record)
                        workflow.execution_history = history
                        db.commit()
                        continue
                except Exception as cond_err:
                    logger.warning(f"Workflow {workflow.id} step {step.name} condition error: {cond_err}")

            # 2. Delay
            if step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

            # 3. Execution with Retries
            step_success = False
            step_error = None
            step_retries = 0
            backoff = 0.05

            while step_retries <= step.max_retries:
                try:
                    result = await step.handler(context)
                    step_record["status"] = "COMPLETED"
                    step_record["result"] = result or {}
                    step_record["completed_at"] = datetime.utcnow().isoformat()
                    step_record["retries"] = step_retries
                    step_success = True
                    break
                except Exception as err:
                    step_retries += 1
                    step_error = str(err)
                    logger.warning(
                        f"Workflow {workflow.id} step {step.name} attempt {step_retries} failed: {err}"
                    )
                    if step_retries <= step.max_retries:
                        await asyncio.sleep(backoff)
                        backoff *= step.backoff_factor

            if not step_success:
                step_record["status"] = "FAILED"
                step_record["error"] = step_error
                step_record["retries"] = step_retries - 1
                step_record["completed_at"] = datetime.utcnow().isoformat()
                history.append(step_record)
                workflow.execution_history = history
                failed = True
                final_error = f"Step '{step.name}' failed after {step.max_retries} retries: {step_error}"
                break

            history.append(step_record)
            workflow.execution_history = history
            db.commit()

        now = datetime.utcnow()
        if failed:
            workflow.status = "FAILED"
            workflow.error_message = final_error
            workflow.failed_at = now
            db.commit()

            # Emit WORKFLOW_FAILED event
            await event_dispatcher.dispatch(DomainEvent(
                event_type=EventType.WORKFLOW_FAILED,
                entity_id=workflow.id,
                tenant_id=workflow.hospital_id,
                correlation_id=correlation_id,
                payload={
                    "workflow_type": workflow.workflow_type,
                    "appointment_id": workflow.appointment_id,
                    "error": final_error,
                    "step": workflow.current_step
                }
            ))

            # Auto human escalation if critical failure
            await event_dispatcher.dispatch(DomainEvent(
                event_type=EventType.HUMAN_ESCALATION,
                entity_id=workflow.id,
                tenant_id=workflow.hospital_id,
                correlation_id=correlation_id,
                payload={
                    "reason": f"Workflow execution failed: {final_error}",
                    "workflow_id": workflow.id,
                    "workflow_type": workflow.workflow_type,
                    "appointment_id": workflow.appointment_id
                }
            ))
        else:
            workflow.status = "COMPLETED"
            workflow.completed_at = now
            db.commit()

            # Emit WORKFLOW_COMPLETED event
            await event_dispatcher.dispatch(DomainEvent(
                event_type=EventType.WORKFLOW_COMPLETED,
                entity_id=workflow.id,
                tenant_id=workflow.hospital_id,
                correlation_id=correlation_id,
                payload={
                    "workflow_type": workflow.workflow_type,
                    "appointment_id": workflow.appointment_id,
                    "steps_executed": len(history)
                }
            ))

        db.refresh(workflow)
        return workflow

    @classmethod
    async def retry_workflow(cls, db: Session, workflow_id: str) -> models.Workflow:
        """Resumes and reruns a failed workflow from its current or failed state."""
        workflow = db.query(models.Workflow).filter(models.Workflow.id == workflow_id).first()
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        definition = cls.get_definition(workflow.workflow_type)
        if not definition:
            raise ValueError(f"Unknown workflow definition: {workflow.workflow_type}")

        workflow.retry_count += 1
        workflow.status = "RETRIED"
        workflow.error_message = None
        db.commit()

        corr_id = f"CORR-RETRY-{workflow.id[:8]}"
        return await cls._run_steps(db, workflow, definition, corr_id)

    @classmethod
    def list_workflows(
        cls,
        db: Session,
        hospital_id: Optional[str] = None,
        workflow_type: Optional[str] = None,
        status: Optional[str] = None,
        appointment_id: Optional[str] = None,
        limit: int = 50
    ) -> List[models.Workflow]:
        query = db.query(models.Workflow)
        if hospital_id:
            query = query.filter(models.Workflow.hospital_id == hospital_id)
        if workflow_type:
            query = query.filter(models.Workflow.workflow_type == workflow_type)
        if status:
            query = query.filter(models.Workflow.status == status.upper())
        if appointment_id:
            query = query.filter(models.Workflow.appointment_id == appointment_id)
        return query.order_by(models.Workflow.created_at.desc()).limit(limit).all()

    @classmethod
    def get_workflow(cls, db: Session, workflow_id: str) -> Optional[models.Workflow]:
        return db.query(models.Workflow).filter(models.Workflow.id == workflow_id).first()


# =====================================================================
# STANDARD WORKFLOW STEP HANDLERS & REGISTRATION
# =====================================================================

# --- 1. POST_BOOKING_WORKFLOW Handlers ---

async def step_assign_questionnaire(ctx: WorkflowContext) -> Dict[str, Any]:
    """Assigns pre-visit questionnaire for the booked appointment."""
    from backend.services.questionnaire_service import QuestionnaireService
    if not ctx.appointment_id:
        return {"skipped": True, "reason": "No appointment_id provided"}

    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first()
    if not appt:
        return {"skipped": True, "reason": f"Appointment {ctx.appointment_id} not found"}

    patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt.patient_id else None
    patient_name = patient.name if patient else "Patient"

    quest = QuestionnaireService.assign_questionnaire_to_appointment(ctx.db, appt)
    ctx.state["questionnaire_id"] = quest.id if quest else None

    # Emit QUESTIONNAIRE_ASSIGNED
    if quest:
        await event_dispatcher.dispatch(DomainEvent(
            event_type=EventType.QUESTIONNAIRE_ASSIGNED,
            entity_id=quest.id,
            tenant_id=ctx.hospital_id,
            correlation_id=ctx.correlation_id,
            payload={
                "appointment_id": ctx.appointment_id,
                "patient_name": patient_name,
                "template_id": getattr(quest, "template_id", getattr(quest, "id", None))
            }
        ))
    return {"assigned": bool(quest), "questionnaire_id": quest.id if quest else None}


async def step_send_patient_confirmation(ctx: WorkflowContext) -> Dict[str, Any]:
    """Sends immediate appointment confirmation notification to the patient."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    doctor = ctx.db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first() if appt else None
    patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt and appt.patient_id else None
    hospital = ctx.db.query(models.Hospital).filter(models.Hospital.id == ctx.hospital_id).first()

    patient_name = patient.name if patient else ctx.payload.get("patient_name", "Patient")
    patient_phone = patient.phone if patient else ctx.payload.get("patient_phone", "+1-555-0100")
    doctor_name = doctor.full_name if doctor else ctx.payload.get("doctor_name", "Staff Physician")
    slot_time = str(appt.start_time) if appt and appt.start_time else ctx.payload.get("slot_time", "Scheduled Time")
    hospital_name = hospital.name if hospital else "Care Center"
    ehr_id = appt.ehr_appointment_id if appt and appt.ehr_appointment_id else (ctx.payload.get("ehr_appointment_id") or "CONFIRMED")

    notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="PATIENT",
        recipient_contact=patient_phone,
        template="APPOINTMENT_CONFIRMATION",
        context={
            "doctor_name": doctor_name,
            "patient_name": patient_name,
            "slot_time": slot_time,
            "hospital_name": hospital_name,
            "ehr_appointment_id": ehr_id
        },
        channel=ctx.payload.get("channel", "SMS"),
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-PATIENT-CONFIRM-{ctx.appointment_id}"
    )

    from backend.services.observability_service import ObservabilityService, BookingTraceStage
    ObservabilityService.record_stage(
        correlation_id=ctx.correlation_id,
        stage=BookingTraceStage.NOTIFICATION,
        status="COMPLETED",
        details={
            "notification_id": notif.id,
            "recipient_type": "PATIENT",
            "recipient_contact": patient_phone,
            "channel": ctx.payload.get("channel", "SMS"),
            "template": "APPOINTMENT_CONFIRMATION"
        },
        actor_id="NOTIFICATION_SERVICE",
        actor_role="SYSTEM",
        tenant_id=ctx.hospital_id,
        db=ctx.db
    )

    return {"notification_id": notif.id, "status": notif.status}


async def step_notify_doctor(ctx: WorkflowContext) -> Dict[str, Any]:
    """Sends new consultation notification to the doctor."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    doctor = ctx.db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first() if appt else None
    patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt and appt.patient_id else None

    doctor_email = doctor.email if doctor and doctor.email else "doctor@hospital.org"
    patient_name = patient.name if patient else "New Patient"
    slot_time = str(appt.start_time) if appt and appt.start_time else "Upcoming Time"
    chief_complaint = appt.chief_complaint if appt and appt.chief_complaint else "Consultation"

    notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="DOCTOR",
        recipient_contact=doctor_email,
        template="NEW_APPOINTMENT",
        context={
            "patient_name": patient_name,
            "slot_time": slot_time,
            "chief_complaint": chief_complaint
        },
        channel="EMAIL",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-DOC-NEW-{ctx.appointment_id}"
    )
    return {"notification_id": notif.id, "status": notif.status}


async def step_schedule_post_booking_reminders(ctx: WorkflowContext) -> Dict[str, Any]:
    """Schedules 24-hour and 1-hour pre-visit reminders for the patient and doctor."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    doctor = ctx.db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first() if appt else None
    patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt and appt.patient_id else None
    patient_phone = patient.phone if patient else "+1-555-0100"
    doctor_name = doctor.full_name if doctor else "Doctor"
    patient_name = patient.name if patient else "Patient"

    base_time = appt.start_time if appt and appt.start_time else (datetime.utcnow() + timedelta(days=2))
    rem_24h_time = base_time - timedelta(hours=24)
    rem_1h_time = base_time - timedelta(hours=1)

    n_24 = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="PATIENT",
        recipient_contact=patient_phone,
        template="PRE_VISIT_REMINDER_24H",
        context={"doctor_name": doctor_name, "slot_time": str(base_time)},
        scheduled_for=rem_24h_time,
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-PATIENT-24H-{ctx.appointment_id}"
    )

    n_1 = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="PATIENT",
        recipient_contact=patient_phone,
        template="PRE_VISIT_REMINDER_1H",
        context={"doctor_name": doctor_name, "slot_time": str(base_time)},
        scheduled_for=rem_1h_time,
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-PATIENT-1H-{ctx.appointment_id}"
    )

    # Doctor schedule reminder
    n_doc = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="DOCTOR",
        recipient_contact=doctor.email if doctor and doctor.email else "doctor@hospital.org",
        template="UPCOMING_APPOINTMENT",
        context={"patient_name": patient_name, "slot_time": str(base_time)},
        scheduled_for=rem_24h_time,
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-DOC-UPCOMING-{ctx.appointment_id}"
    )

    return {
        "scheduled_patient_24h": n_24.id,
        "scheduled_patient_1h": n_1.id,
        "scheduled_doc_reminder": n_doc.id
    }


# Register POST_BOOKING_WORKFLOW
WorkflowEngine.register(WorkflowDefinition(
    workflow_type="POST_BOOKING_WORKFLOW",
    steps=[
        WorkflowStep("ASSIGN_QUESTIONNAIRE", step_assign_questionnaire),
        WorkflowStep("SEND_PATIENT_CONFIRMATION", step_send_patient_confirmation),
        WorkflowStep("NOTIFY_DOCTOR", step_notify_doctor),
        WorkflowStep("SCHEDULE_REMINDERS", step_schedule_post_booking_reminders)
    ]
))


# --- 2. APPOINTMENT_CANCELLATION_WORKFLOW Handlers ---

async def step_release_appointment_slot(ctx: WorkflowContext) -> Dict[str, Any]:
    """Ensures appointment is marked CANCELLED and calendar slot is released."""
    if not ctx.appointment_id:
        return {"skipped": True}

    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first()
    if appt:
        appt.status = "CANCELLED"
        if appt.slot_id:
            slot = ctx.db.query(models.TimeSlot).filter(models.TimeSlot.id == appt.slot_id).first()
            if slot:
                slot.status = "AVAILABLE"
                slot.hold_expires_at = None
        ctx.db.commit()
    return {"appointment_cancelled": True}


async def step_notify_cancellation(ctx: WorkflowContext) -> Dict[str, Any]:
    """Sends cancellation notices to both patient and doctor."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    doctor = ctx.db.query(models.Doctor).filter(models.Doctor.id == appt.doctor_id).first() if appt else None
    patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt and appt.patient_id else None
    reason = ctx.payload.get("reason", "Patient requested cancellation")

    patient_phone = patient.phone if patient else "+1-555-0100"
    patient_name = patient.name if patient else "Patient"
    slot_time_str = str(appt.start_time) if appt and appt.start_time else "Scheduled Time"

    # Patient Notice
    p_notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="PATIENT",
        recipient_contact=patient_phone,
        template="APPOINTMENT_CANCELLATION",
        context={
            "doctor_name": doctor.full_name if doctor else "Doctor",
            "slot_time": slot_time_str,
            "reason": reason
        },
        channel="SMS",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-PATIENT-CANCEL-{ctx.appointment_id}"
    )

    # Doctor Notice
    d_notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="DOCTOR",
        recipient_contact=doctor.email if doctor and doctor.email else "doctor@hospital.org",
        template="DOCTOR_CANCELLATION",
        context={
            "patient_name": patient_name,
            "slot_time": slot_time_str,
            "reason": reason
        },
        channel="EMAIL",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-DOC-CANCEL-{ctx.appointment_id}"
    )
    return {"patient_notif_id": p_notif.id, "doctor_notif_id": d_notif.id}


async def step_cancel_scheduled_reminders(ctx: WorkflowContext) -> Dict[str, Any]:
    """Cancels any pending future notifications/reminders for the cancelled appointment."""
    pending = ctx.db.query(models.Notification).filter(
        models.Notification.appointment_id == ctx.appointment_id,
        models.Notification.status == "SCHEDULED"
    ).all()
    count = len(pending)
    for n in pending:
        n.status = "CANCELLED"

    rem_pending = ctx.db.query(models.ReminderLog).filter(
        models.ReminderLog.appointment_id == ctx.appointment_id,
        models.ReminderLog.status == "SCHEDULED"
    ).all()
    for r in rem_pending:
        r.status = "CANCELLED"

    ctx.db.commit()
    return {"cancelled_reminders_count": count + len(rem_pending)}


WorkflowEngine.register(WorkflowDefinition(
    workflow_type="APPOINTMENT_CANCELLATION_WORKFLOW",
    steps=[
        WorkflowStep("RELEASE_SLOT", step_release_appointment_slot),
        WorkflowStep("SEND_CANCELLATION_NOTICES", step_notify_cancellation),
        WorkflowStep("CANCEL_PENDING_REMINDERS", step_cancel_scheduled_reminders)
    ]
))


# --- 3. EHR_RECOVERY_WORKFLOW Handlers ---

async def step_verify_external_ehr(ctx: WorkflowContext) -> Dict[str, Any]:
    """Queries external EHR connector to verify actual remote state."""
    from backend.ehr.connector import MockEHRConnector
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    if not appt:
        return {"exists_remotely": False, "reason": "No appointment"}

    connector = MockEHRConnector()
    remote_appt = await connector.get_appointment(appt.id)
    ctx.state["remote_exists"] = bool(remote_appt)
    ctx.state["remote_data"] = remote_appt

    await event_dispatcher.dispatch(DomainEvent(
        event_type=EventType.EXTERNAL_VERIFICATION_COMPLETED,
        entity_id=appt.id,
        tenant_id=ctx.hospital_id,
        correlation_id=ctx.correlation_id,
        payload={"exists_remotely": bool(remote_appt), "appointment_id": appt.id}
    ))
    return {"exists_remotely": bool(remote_appt)}


async def step_synchronize_or_retry_ehr(ctx: WorkflowContext) -> Dict[str, Any]:
    """Synchronizes if remote exists, or safely retries external creation."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    if not appt:
        return {"synced": False}

    if ctx.state.get("remote_exists"):
        appt.status = "CONFIRMED"
        remote_data = ctx.state.get("remote_data") or {}
        if not appt.ehr_appointment_id and remote_data.get("ehr_id"):
            appt.ehr_appointment_id = remote_data["ehr_id"]
        ctx.db.commit()
        return {"action": "SYNCHRONIZED_EXISTING", "status": "CONFIRMED"}
    else:
        # Retry external booking safely with idempotency
        from backend.ehr.connector import MockEHRConnector
        connector = MockEHRConnector()
        patient = ctx.db.query(models.Patient).filter(models.Patient.id == appt.patient_id).first() if appt.patient_id else None
        res = await connector.create_appointment({
            "appointment_id": appt.id,
            "patient_name": patient.name if patient else "Patient",
            "doctor_id": appt.doctor_id,
            "slot_id": appt.slot_id
        })
        if res.get("status") == "CONFIRMED":
            appt.status = "CONFIRMED"
            appt.ehr_appointment_id = res.get("ehr_id")
            ctx.db.commit()
            return {"action": "RETRIED_SUCCESSFULLY", "status": "CONFIRMED"}
        else:
            ctx.state["unresolved"] = True
            return {"action": "RETRY_FAILED", "res": res}


def condition_reconciliation_needed(ctx: WorkflowContext) -> bool:
    """True if outcome remains unresolved after recovery check and retry."""
    return bool(ctx.state.get("unresolved"))


async def step_escalate_to_reconciliation(ctx: WorkflowContext) -> Dict[str, Any]:
    """Creates reconciliation record, marks status RECONCILIATION_REQUIRED, triggers human escalation."""
    appt = ctx.db.query(models.Appointment).filter(models.Appointment.id == ctx.appointment_id).first() if ctx.appointment_id else None
    if not appt:
        return {"escalated": False}

    appt.status = "RECONCILIATION_REQUIRED"
    ctx.db.commit()

    recon = models.ReconciliationRecord(
        hospital_id=ctx.hospital_id,
        appointment_id=appt.id,
        idempotency_key=f"RECON-{appt.id}",
        failure_reason="Outcome remained unresolved during EHR recovery workflow",
        status="RECONCILIATION_REQUIRED"
    )
    ctx.db.add(recon)
    ctx.db.commit()

    # Dispatch RECONCILIATION_REQUIRED and HUMAN_ESCALATION events
    await event_dispatcher.dispatch(DomainEvent(
        event_type=EventType.RECONCILIATION_REQUIRED,
        entity_id=recon.id,
        tenant_id=ctx.hospital_id,
        correlation_id=ctx.correlation_id,
        payload={"appointment_id": appt.id, "failure_reason": recon.failure_reason}
    ))
    await event_dispatcher.dispatch(DomainEvent(
        event_type=EventType.HUMAN_ESCALATION,
        entity_id=recon.id,
        tenant_id=ctx.hospital_id,
        correlation_id=ctx.correlation_id,
        payload={"reason": "Manual EHR reconciliation required", "appointment_id": appt.id}
    ))
    return {"reconciliation_id": recon.id, "status": "RECONCILIATION_REQUIRED"}


WorkflowEngine.register(WorkflowDefinition(
    workflow_type="EHR_RECOVERY_WORKFLOW",
    steps=[
        WorkflowStep("VERIFY_EXTERNAL_EHR", step_verify_external_ehr, max_retries=2),
        WorkflowStep("SYNCHRONIZE_OR_RETRY", step_synchronize_or_retry_ehr, max_retries=2),
        WorkflowStep("RECONCILIATION_CHECK", step_escalate_to_reconciliation, condition=condition_reconciliation_needed)
    ]
))


# --- 4. CLINICAL_URGENCY_WORKFLOW Handlers ---

async def step_deliver_emergency_advisory(ctx: WorkflowContext) -> Dict[str, Any]:
    """Delivers immediate emergency warning to patient."""
    patient_phone = ctx.payload.get("patient_phone", "+1-555-0100")
    notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="PATIENT",
        recipient_contact=patient_phone,
        template="IMPORTANT_UPDATES",
        context={
            "hospital_name": "Emergency Clinical Triage",
            "message_content": (
                "EMERGENCY ADVISORY: Potential urgent health condition detected. "
                "Please call 911 or proceed immediately to the nearest Emergency Department. "
                "Do not wait for your scheduled consultation."
            )
        },
        channel="SMS",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-URGENT-ADVISORY-{ctx.appointment_id or uuid.uuid4().hex[:8]}"
    )
    return {"advisory_sent": True, "notification_id": notif.id}


async def step_alert_clinical_staff(ctx: WorkflowContext) -> Dict[str, Any]:
    """Alerts doctor and hospital admin regarding critical patient symptoms."""
    doctor_email = ctx.payload.get("doctor_email", "doctor@hospital.org")
    d_notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="DOCTOR",
        recipient_contact=doctor_email,
        template="OPERATIONAL_ALERTS",
        context={
            "appointment_id": ctx.appointment_id or "N/A",
            "reason": f"Urgent clinical symptoms reported: {ctx.payload.get('symptoms', 'High risk indicators')}"
        },
        channel="EMAIL",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-URGENT-DOC-{ctx.appointment_id or uuid.uuid4().hex[:8]}"
    )

    h_notif = NotificationService.create_or_schedule_notification(
        db=ctx.db,
        recipient_type="HOSPITAL",
        recipient_contact="admin@hospital.org",
        template="OPERATIONAL_ALERTS",
        context={
            "appointment_id": ctx.appointment_id or "N/A",
            "reason": f"Clinical Urgency Escallation: {ctx.payload.get('symptoms', 'High risk indicators')}"
        },
        channel="EMAIL",
        hospital_id=ctx.hospital_id,
        appointment_id=ctx.appointment_id,
        idempotency_key=f"NOTIF-URGENT-HOSP-{ctx.appointment_id or uuid.uuid4().hex[:8]}"
    )
    return {"doctor_alert_id": d_notif.id, "hospital_alert_id": h_notif.id}


async def step_record_urgent_audit_and_escalate(ctx: WorkflowContext) -> Dict[str, Any]:
    """Emits human_escalation domain event and logs critical audit event."""
    await event_dispatcher.dispatch(DomainEvent(
        event_type=EventType.HUMAN_ESCALATION,
        entity_id=ctx.appointment_id or ctx.workflow_id,
        tenant_id=ctx.hospital_id,
        correlation_id=ctx.correlation_id,
        payload={
            "urgency_level": "CRITICAL",
            "symptoms": ctx.payload.get("symptoms"),
            "appointment_id": ctx.appointment_id,
            "escalated_to": "CLINICAL_EMERGENCY_TEAM"
        }
    ))
    return {"escalation_dispatched": True}


WorkflowEngine.register(WorkflowDefinition(
    workflow_type="CLINICAL_URGENCY_WORKFLOW",
    steps=[
        WorkflowStep("DELIVER_PATIENT_ADVISORY", step_deliver_emergency_advisory),
        WorkflowStep("ALERT_CLINICAL_STAFF", step_alert_clinical_staff),
        WorkflowStep("URGENT_AUDIT_ESCALATION", step_record_urgent_audit_and_escalate)
    ]
))
