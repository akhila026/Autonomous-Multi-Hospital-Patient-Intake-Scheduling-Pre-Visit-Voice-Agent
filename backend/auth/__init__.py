from backend.auth.roles import UserRole, ROLE_PERMISSIONS
from backend.auth.security import hash_password, verify_password, create_access_token, decode_access_token, generate_session_token
from backend.auth.tenant import TenantContext, TenantMismatchError, validate_appointment_tenant_isolation
from backend.auth.dependencies import (
    get_current_user,
    get_optional_current_user,
    require_roles,
    require_platform_admin,
    require_hospital_admin,
    require_doctor,
    require_patient,
    require_authenticated_user,
    get_current_tenant,
    get_current_user_role,
    verify_doctor_ownership,
    verify_patient_ownership
)

__all__ = [
    "UserRole",
    "ROLE_PERMISSIONS",
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "generate_session_token",
    "TenantContext",
    "TenantMismatchError",
    "validate_appointment_tenant_isolation",
    "get_current_user",
    "get_optional_current_user",
    "require_roles",
    "require_platform_admin",
    "require_hospital_admin",
    "require_doctor",
    "require_patient",
    "require_authenticated_user",
    "get_current_tenant",
    "get_current_user_role",
    "verify_doctor_ownership",
    "verify_patient_ownership"
]
