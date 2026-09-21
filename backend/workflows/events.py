import asyncio
import uuid
import logging
from enum import Enum
from typing import Dict, Any, Callable, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class EventType(str, Enum):
    """The 18 core domain event types (PRD Section 16 & 21)."""
    HOSPITAL_APPROVED = "hospital_approved"
    DOCTOR_CREATED = "doctor_created"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    QUESTIONNAIRE_ASSIGNED = "questionnaire_assigned"
    QUESTIONNAIRE_COMPLETED = "questionnaire_completed"
    AI_CONVERSATION_STARTED = "ai_conversation_started"
    CAPABILITY_EXECUTED = "capability_executed"
    EHR_OPERATION_STARTED = "ehr_operation_started"
    EHR_OPERATION_COMPLETED = "ehr_operation_completed"
    EHR_OPERATION_FAILED = "ehr_operation_failed"
    EXTERNAL_VERIFICATION_COMPLETED = "external_verification_completed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    HUMAN_ESCALATION = "human_escalation"


class DomainEvent(BaseModel):
    """
    Domain Event Data Transfer Object.
    Represents an immutable occurrence within the healthcare platform.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    entity_id: str
    tenant_id: Optional[str] = None  # hospital_id
    correlation_id: str = Field(default_factory=lambda: f"CORR-{uuid.uuid4().hex[:8]}")
    payload: Dict[str, Any] = Field(default_factory=dict)
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EventDispatcher:
    """
    Pub/Sub event dispatcher for asynchronous domain events (PRD Section 16 & 21).
    Supports isolated subscriber execution, global observers, telemetry metrics,
    and dead-letter error containment.
    """
    def __init__(self):
        self._handlers: Dict[EventType, List[Callable]] = {e: [] for e in EventType}
        self._global_handlers: List[Callable] = []
        self._event_history: List[DomainEvent] = []
        self._metrics: Dict[str, int] = {e.value: 0 for e in EventType}

    def subscribe(self, event_type: EventType, handler: Callable):
        """Registers a subscriber for a specific event type."""
        if event_type in self._handlers:
            self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: Callable):
        """Registers a global observer invoked for every dispatched event (e.g. audit logger, telemetry)."""
        self._global_handlers.append(handler)

    async def dispatch(self, event: DomainEvent) -> None:
        """
        Dispatches a domain event to all registered subscribers.
        Ensures handler failures are isolated and logged without halting other listeners.
        """
        self._metrics[event.event_type.value] = self._metrics.get(event.event_type.value, 0) + 1
        self._event_history.append(event)
        if len(self._event_history) > 200:
            self._event_history.pop(0)

        # 1. Global observers (Audit, Analytics, Telemetry)
        for gh in self._global_handlers:
            try:
                if asyncio.iscoroutinefunction(gh):
                    await gh(event)
                else:
                    gh(event)
            except Exception as ex:
                logger.error(f"Global handler error on {event.event_type}: {ex}", exc_info=True)

        # 2. Type-specific subscribers
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as ex:
                logger.error(f"Event handler error on {event.event_type} for entity {event.entity_id}: {ex}", exc_info=True)

    def get_history(self, limit: int = 50, event_type: Optional[str] = None) -> List[DomainEvent]:
        """Returns recent dispatched events for inspection and telemetry."""
        events = self._event_history
        if event_type:
            events = [e for e in events if e.event_type.value == event_type or e.event_type.name == event_type]
        return list(reversed(events[-limit:]))

    def get_metrics(self) -> Dict[str, int]:
        """Returns cumulative event dispatch counts."""
        return dict(self._metrics)

    def reset_for_test(self):
        """Cleans in-memory history and metrics for testing."""
        self._event_history.clear()
        self._metrics = {e.value: 0 for e in EventType}


event_dispatcher = EventDispatcher()
