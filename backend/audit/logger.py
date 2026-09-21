import logging
import json
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Union

from backend.audit.correlation import get_correlation_id
from backend.audit.privacy import redact_sensitive_data

logger = logging.getLogger("healthcare.audit")


class AuditCategory(str, Enum):
    LOGIN_ACCESS = "LOGIN_ACCESS"
    APPOINTMENT_OPERATIONS = "APPOINTMENT_OPERATIONS"
    PATIENT_DATA_ACCESS = "PATIENT_DATA_ACCESS"
    AI_ACTIONS = "AI_ACTIONS"
    CAPABILITY_EXECUTIONS = "CAPABILITY_EXECUTIONS"
    INTEGRATION_OPERATIONS = "INTEGRATION_OPERATIONS"
    CONFIGURATION_CHANGES = "CONFIGURATION_CHANGES"
    ADMINISTRATIVE_ACTIONS = "ADMINISTRATIVE_ACTIONS"


class AuditLogger:
    """
    Enterprise audit logger maintaining immutable, HIPAA-compliant audit records
    across all 8 mandatory healthcare platform operational event categories:
    1. LOGIN_ACCESS
    2. APPOINTMENT_OPERATIONS
    3. PATIENT_DATA_ACCESS
    4. AI_ACTIONS
    5. CAPABILITY_EXECUTIONS
    6. INTEGRATION_OPERATIONS
    7. CONFIGURATION_CHANGES
    8. ADMINISTRATIVE_ACTIONS
    """

    @staticmethod
    def record_event(
        event_type: Union[AuditCategory, str],
        action: str,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = "SYSTEM",
        tenant_id: Optional[str] = None,
        resource_type: str = "SYSTEM",
        resource_id: Optional[str] = None,
        status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        category: Optional[Union[AuditCategory, str]] = None,
        db: Optional[Any] = None
    ) -> Dict[str, Any]:
        cid = correlation_id or get_correlation_id()
        
        # Resolve category
        if category:
            cat_str = category.value if isinstance(category, AuditCategory) else str(category)
        elif isinstance(event_type, AuditCategory):
            cat_str = event_type.value
        elif any(c.value == str(event_type).upper() for c in AuditCategory):
            cat_str = str(event_type).upper()
        else:
            cat_str = AuditCategory.ADMINISTRATIVE_ACTIONS.value

        # Redact any direct or indirect sensitive healthcare information
        safe_details = redact_sensitive_data(details or {})
        safe_details["category"] = cat_str

        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": cid,
            "category": cat_str,
            "event_type": str(event_type),
            "action": action,
            "actor_id": actor_id or "ANONYMOUS",
            "actor_role": actor_role or "SYSTEM",
            "tenant_id": tenant_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "status": status,
            "details": safe_details
        }

        logger.info(f"[AUDIT] {cat_str}:{action} (corr={cid}) - {json.dumps(payload)}")

        # Persist immutable audit record into SQLite when DB session is available
        if db:
            try:
                from backend import models
                status_val = status
                if details and (details.get("success") is False or "exception" in details or "error" in details):
                    status_val = "FAILED"

                rec = models.AuditEvent(
                    correlation_id=cid,
                    hospital_id=tenant_id,
                    actor_id=actor_id or "SYSTEM",
                    actor_role=actor_role or "SYSTEM",
                    action=f"{cat_str}:{action}" if not action.startswith(f"{cat_str}:") else action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    status=status_val,
                    details=safe_details
                )
                db.add(rec)
                db.commit()
            except Exception as e:
                logger.warning(f"Could not commit audit event: {e}")

        return payload

    # =========================================================================
    # CONVENIENCE HELPERS FOR THE 8 AUDIT CATEGORIES
    # =========================================================================

    @classmethod
    def log_login_access(
        cls, action: str, actor_id: str, email: str, status: str = "SUCCESS",
        tenant_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None, db: Optional[Any] = None
    ):
        det = details or {}
        det["email"] = email
        return cls.record_event(
            category=AuditCategory.LOGIN_ACCESS,
            event_type=AuditCategory.LOGIN_ACCESS,
            action=action,
            actor_id=actor_id,
            actor_role="USER",
            tenant_id=tenant_id,
            resource_type="AUTH_SESSION",
            status=status,
            details=det,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_appointment_operation(
        cls, action: str, appointment_id: str, actor_id: str, actor_role: str,
        tenant_id: Optional[str] = None, status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
        db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.APPOINTMENT_OPERATIONS,
            event_type=AuditCategory.APPOINTMENT_OPERATIONS,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type="APPOINTMENT",
            resource_id=appointment_id,
            status=status,
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_patient_data_access(
        cls, action: str, patient_id: str, actor_id: str, actor_role: str,
        tenant_id: Optional[str] = None, status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
        db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.PATIENT_DATA_ACCESS,
            event_type=AuditCategory.PATIENT_DATA_ACCESS,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type="PATIENT_RECORD",
            resource_id=patient_id,
            status=status,
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_ai_action(
        cls, action: str, session_id: str, actor_id: Optional[str] = None,
        actor_role: str = "PATIENT", tenant_id: Optional[str] = None,
        status: str = "SUCCESS", details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None, db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.AI_ACTIONS,
            event_type=AuditCategory.AI_ACTIONS,
            action=action,
            actor_id=actor_id or "AI_AGENT",
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type="AI_CONVERSATION",
            resource_id=session_id,
            status=status,
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_capability_execution(
        cls, capability_name: str, actor_id: Optional[str] = None,
        actor_role: str = "SYSTEM", tenant_id: Optional[str] = None,
        status: str = "SUCCESS", details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None, db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.CAPABILITY_EXECUTIONS,
            event_type=AuditCategory.CAPABILITY_EXECUTIONS,
            action=f"INVOKE_{capability_name.upper()}",
            actor_id=actor_id or "CAPABILITY_REGISTRY",
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type="CAPABILITY",
            resource_id=capability_name,
            status=status,
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_integration_operation(
        cls, action: str, system_name: str = "MOCK_EHR",
        tenant_id: Optional[str] = None, status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
        db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.INTEGRATION_OPERATIONS,
            event_type=AuditCategory.INTEGRATION_OPERATIONS,
            action=action,
            actor_id="INTEGRATION_CONNECTOR",
            actor_role="SYSTEM",
            tenant_id=tenant_id,
            resource_type="EXTERNAL_INTEGRATION",
            resource_id=system_name,
            status=status,
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_configuration_change(
        cls, action: str, actor_id: str, actor_role: str,
        resource_type: str, resource_id: str, tenant_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
        db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.CONFIGURATION_CHANGES,
            event_type=AuditCategory.CONFIGURATION_CHANGES,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            status="SUCCESS",
            details=details,
            correlation_id=correlation_id,
            db=db
        )

    @classmethod
    def log_administrative_action(
        cls, action: str, actor_id: str, actor_role: str,
        resource_type: str, resource_id: str, tenant_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
        db: Optional[Any] = None
    ):
        return cls.record_event(
            category=AuditCategory.ADMINISTRATIVE_ACTIONS,
            event_type=AuditCategory.ADMINISTRATIVE_ACTIONS,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            status="SUCCESS",
            details=details,
            correlation_id=correlation_id,
            db=db
        )
