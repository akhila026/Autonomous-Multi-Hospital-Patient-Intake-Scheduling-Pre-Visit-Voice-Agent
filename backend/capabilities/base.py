from abc import ABC, abstractmethod
from typing import Type, Any, Optional, Dict
from pydantic import BaseModel, ConfigDict


class CapabilityContext(BaseModel):
    """Context passed to every capability execution."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    correlation_id: str
    user_id: Optional[str] = None
    user_role: Optional[str] = None  # PATIENT, DOCTOR, HOSPITAL_ADMIN, PLATFORM_ADMIN
    tenant_id: Optional[str] = None  # Hospital UUID for tenant isolation
    session_id: Optional[str] = None
    channel: str = "web_voice"  # web_voice, telephone, chat
    db: Optional[Any] = None  # Optional SQLAlchemy Session


class CapabilityResult(BaseModel):
    """Standardized response from a controlled capability."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: Optional[str] = None  # e.g. INVALID_INPUT, UNAUTHORIZED, CROSS_TENANT_VIOLATION, SLOT_UNAVAILABLE, TIMEOUT
    requires_clarification: bool = False
    requires_escalation: bool = False
    is_retryable: bool = False
    idempotent_applied: bool = False
    audit_logged: bool = True


class BaseCapability(ABC):
    """
    Abstract Base Capability (PRD Section 9 & 22).
    Every capability defines:
    - input_schema & output_schema
    - validation
    - authorization
    - error handling
    - retry behavior where required
    - idempotency where required
    - verification where required
    - audit information
    """
    name: str
    description: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel] = CapabilityResult
    requires_authorization: bool = True
    is_idempotent: bool = False
    max_retries: int = 0
    retry_on_timeout: bool = False
    supports_verification: bool = False
    audit_action: Optional[str] = None

    def validate(self, params: BaseModel, context: CapabilityContext) -> None:
        """
        Semantic validation hook beyond Pydantic schema validation.
        Raises ValueError or custom exceptions for invalid arguments.
        """
        pass

    def authorize(self, params: BaseModel, context: CapabilityContext) -> bool:
        """
        Enforces tenant isolation and resource ownership permissions before execution.
        Returns True if authorized, False otherwise.
        """
        if not self.requires_authorization:
            return True
        return True

    @abstractmethod
    async def execute(self, params: BaseModel, context: CapabilityContext) -> CapabilityResult:
        """
        Executes the capability strictly via domain service layers.
        The capability must NEVER directly access the database or Mock EHR tables.
        """
        pass
