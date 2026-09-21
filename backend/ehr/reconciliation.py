from typing import Optional, Dict, Any
from pydantic import BaseModel
from backend.ehr.connector import BaseEHRConnector, EHRVerificationResult
from backend.audit.logger import AuditLogger

class ReconciliationRecord(BaseModel):
    idempotency_key: str
    appointment_id: str
    failure_reason: str
    external_verified: bool
    status: str  # RECONCILED, ESCALATED_TO_HUMAN, RETRY_PENDING

class EHRReconciliationManager:
    """
    Executes the failure recovery state machine (PRD Section 13 & 28 Option B).
    Catches timeouts, verifies external EHR existence before retrying, and logs reconciliation.
    """
    def __init__(self, connector: BaseEHRConnector):
        self.connector = connector

    async def handle_timeout_outcome(
        self,
        idempotency_key: str,
        appointment_id: str,
        correlation_id: str
    ) -> EHRVerificationResult:
        """
        Step 1: On timeout, query external system using idempotency key.
        Step 2: If found -> return verified external ID (reconcile to CONFIRMED without duplicate).
        Step 3: If not found -> safe to retry once.
        """
        AuditLogger.record_event(
            event_type="EHR_TIMEOUT_DETECTED",
            action="VERIFY_EXTERNAL_STATE",
            details={"idempotency_key": idempotency_key, "appointment_id": appointment_id},
            correlation_id=correlation_id
        )

        verification = await self.connector.verify_appointment(idempotency_key)

        if verification.found:
            AuditLogger.record_event(
                event_type="EHR_RECONCILIATION_SUCCESS",
                action="RECONCILED_CONFIRMED",
                details={"external_appointment_id": verification.external_appointment_id},
                correlation_id=correlation_id
            )
        else:
            AuditLogger.record_event(
                event_type="EHR_RECORD_NOT_FOUND",
                action="SAFE_RETRY_PERMITTED",
                details={"idempotency_key": idempotency_key},
                correlation_id=correlation_id
            )

        return verification
