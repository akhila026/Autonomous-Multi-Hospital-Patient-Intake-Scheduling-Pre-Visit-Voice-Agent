from typing import Dict, Any, List, Optional
from backend.capabilities.base import BaseCapability, CapabilityContext, CapabilityResult
from backend.audit.logger import AuditLogger


class CapabilityRegistry:
    """
    Registry for managing and safely invoking controlled capabilities.
    Enforces authorization, tenant isolation, semantic validation, output schemas,
    retry policies, and structured audit recording.
    """
    def __init__(self):
        self._capabilities: Dict[str, BaseCapability] = {}

    def register(self, capability: BaseCapability):
        self._capabilities[capability.name] = capability

    def get(self, name: str) -> Optional[BaseCapability]:
        return self._capabilities.get(name)

    def list_capabilities(self) -> List[Dict[str, Any]]:
        """Returns schemas of all registered capabilities for LLM tool calling."""
        return [
            {
                "name": cap.name,
                "description": cap.description,
                "input_schema": cap.input_schema.model_json_schema(),
                "output_schema": cap.output_schema.model_json_schema() if hasattr(cap.output_schema, "model_json_schema") else {},
                "is_idempotent": cap.is_idempotent,
                "requires_authorization": cap.requires_authorization
            }
            for cap in self._capabilities.values()
        ]

    async def invoke(self, name: str, raw_params: dict, context: CapabilityContext) -> CapabilityResult:
        cap = self.get(name)
        if not cap:
            return CapabilityResult(
                success=False,
                error=f"Capability '{name}' is not registered.",
                error_code="CAPABILITY_NOT_FOUND",
                requires_clarification=True
            )

        # 1. Schema Validation
        try:
            validated_params = cap.input_schema.model_validate(raw_params)
        except Exception as ve:
            return CapabilityResult(
                success=False,
                error=f"Invalid arguments for '{name}': {str(ve)}",
                error_code="INVALID_INPUT",
                requires_clarification=True
            )

        # 2. Semantic Validation Hook
        try:
            cap.validate(validated_params, context)
        except ValueError as val_err:
            return CapabilityResult(
                success=False,
                error=str(val_err),
                error_code="VALIDATION_ERROR",
                requires_clarification=True
            )

        # 3. Tenant Isolation & Resource Ownership Authorization
        is_authorized = True
        try:
            is_authorized = cap.authorize(validated_params, context)
        except Exception as auth_ex:
            is_authorized = False

        if not is_authorized:
            AuditLogger.record_event(
                event_type="CAPABILITY_UNAUTHORIZED",
                action=cap.audit_action or name,
                actor_id=context.user_id,
                tenant_id=context.tenant_id,
                details={"error": "Tenant or resource ownership check failed", "params": raw_params},
                correlation_id=context.correlation_id,
                db=context.db
            )
            return CapabilityResult(
                success=False,
                error=f"Tenant isolation or resource ownership authorization failed for '{name}'.",
                error_code="UNAUTHORIZED",
                requires_escalation=True
            )

        try:
            # 4. Audit capability execution start
            AuditLogger.record_event(
                event_type="CAPABILITY_EXECUTION_START",
                action=cap.audit_action or name,
                actor_id=context.user_id,
                tenant_id=context.tenant_id,
                details={"params": raw_params},
                correlation_id=context.correlation_id,
                db=context.db
            )

            # 5. Execution with retry support for idempotent capabilities
            attempts = 0
            max_attempts = (cap.max_retries + 1) if (cap.is_idempotent or cap.retry_on_timeout) else 1
            last_error = None
            result = None

            while attempts < max_attempts:
                attempts += 1
                try:
                    result = await cap.execute(validated_params, context)
                    break
                except Exception as ex:
                    last_error = ex
                    if attempts >= max_attempts:
                        raise ex

            if result is None:
                raise last_error or Exception("Execution failed without result")

            # 6. Audit capability completion
            AuditLogger.record_event(
                event_type="CAPABILITY_EXECUTION_END",
                action=cap.audit_action or name,
                actor_id=context.user_id,
                tenant_id=context.tenant_id,
                details={"success": result.success, "error": result.error},
                correlation_id=context.correlation_id,
                db=context.db
            )

            return result

        except Exception as e:
            AuditLogger.record_event(
                event_type="CAPABILITY_EXECUTION_ERROR",
                action=cap.audit_action or name,
                actor_id=context.user_id,
                tenant_id=context.tenant_id,
                details={"exception": str(e)},
                correlation_id=context.correlation_id,
                db=context.db
            )
            return CapabilityResult(
                success=False,
                error=str(e),
                error_code="EXECUTION_ERROR",
                requires_escalation=True
            )


# Global singleton registry
capability_registry = CapabilityRegistry()
