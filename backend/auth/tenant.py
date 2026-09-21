from typing import Optional, Any
from fastapi import HTTPException, status

class TenantMismatchError(HTTPException, ValueError):
    """Raised when an operation attempts cross-tenant resource assignment."""
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        self.args = (detail,)

class TenantContext:
    """
    Encapsulates authenticated tenant (Hospital) context for request isolation.
    Ensures that Hospital A never accesses Hospital B's resources (PRD Section 4 & 21).
    """
    def __init__(self, hospital_id: Optional[str] = None, is_platform_admin: bool = False):
        self.hospital_id = hospital_id
        self.is_platform_admin = is_platform_admin

    def validate_access(self, target_hospital_id: Optional[str], resource_name: str = "resource") -> bool:
        """
        Validates that the current tenant context matches the target resource hospital.
        Platform admins can operate across hospitals.
        Non-platform admins are strictly confined to their own hospital_id.
        """
        if self.is_platform_admin:
            return True

        if not self.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant Isolation Violation: User is not assigned to any hospital tenant to access this {resource_name}."
            )

        if target_hospital_id and target_hospital_id != self.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant Isolation Violation: Access to {resource_name} belonging to hospital '{target_hospital_id}' is forbidden for your tenant."
            )

        return True

    def filter_query_by_tenant(self, query: Any, model_class: Any) -> Any:
        """
        Applies tenant filter to SQLAlchemy query if the user is scoped to a hospital tenant.
        """
        if self.is_platform_admin:
            return query
        if hasattr(model_class, "hospital_id") and self.hospital_id:
            return query.filter(model_class.hospital_id == self.hospital_id)
        return query


def validate_appointment_tenant_isolation(
    hospital_id: str,
    doctor_hospital_id: str,
    patient_hospital_id: Optional[str] = None,
    calendar_hospital_id: Optional[str] = None
) -> bool:
    """
    Service-layer validation ensuring related hospital-owned resources belong to the same hospital.
    Enforces:
    - appointment.hospital_id == doctor.hospital_id
    - appointment.hospital_id == patient.hospital_id (where patient is scoped to a specific hospital)
    - appointment.hospital_id == calendar.hospital_id (where calendar is specified)
    Rejects cross-hospital assignments with a clear validation error.
    """
    if not hospital_id:
        raise TenantMismatchError("Tenant Isolation Error: hospital_id is required.")

    if hospital_id != doctor_hospital_id:
        raise TenantMismatchError(
            f"Tenant Isolation Error: Doctor belongs to hospital '{doctor_hospital_id}', "
            f"which does not match appointment hospital '{hospital_id}'."
        )

    if patient_hospital_id and patient_hospital_id != hospital_id:
        raise TenantMismatchError(
            f"Tenant Isolation Error: Patient is registered with hospital '{patient_hospital_id}', "
            f"which does not match appointment hospital '{hospital_id}'."
        )

    if calendar_hospital_id and calendar_hospital_id != hospital_id:
        raise TenantMismatchError(
            f"Tenant Isolation Error: Calendar belongs to hospital '{calendar_hospital_id}', "
            f"which does not match appointment hospital '{hospital_id}'."
        )

    return True
