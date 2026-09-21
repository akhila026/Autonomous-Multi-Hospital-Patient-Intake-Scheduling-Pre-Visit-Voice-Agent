from typing import Optional, List
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import jwt

from backend.database import get_db
from backend import models
from backend.auth.roles import UserRole
from backend.auth.security import decode_access_token
from backend.auth.tenant import TenantContext

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> models.User:
    """
    Extracts, decodes, and validates the JWT Bearer token.
    Resolves the authentic user from the database.
    Rejects missing, expired, or tampered tokens with HTTP 401.
    """
    raw_token = token
    if not raw_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_token = parts[1]

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        payload = decode_access_token(raw_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials: Token is invalid.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: Subject missing.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated."
        )

    return user

async def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    """Resolves authenticated user if valid token present, otherwise None."""
    try:
        return await get_current_user(token=token, authorization=authorization, db=db)
    except HTTPException:
        return None

def require_roles(*allowed_roles: UserRole):
    """
    Reusable dependency factory that validates the authenticated user's role.
    Raises HTTP 403 if the user does not possess one of the allowed roles.
    """
    role_values = [r.value if isinstance(r, UserRole) else str(r) for r in allowed_roles]

    async def role_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in role_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: Insufficient permissions. Required role(s): {role_values}"
            )
        return current_user

    return role_checker

# Reusable role-specific dependencies
require_platform_admin = require_roles(UserRole.PLATFORM_ADMIN)
require_hospital_admin = require_roles(UserRole.PLATFORM_ADMIN, UserRole.HOSPITAL_ADMIN)
require_admin_or_platform = require_hospital_admin
require_doctor = require_roles(UserRole.DOCTOR, UserRole.PLATFORM_ADMIN, UserRole.HOSPITAL_ADMIN)
require_patient = require_roles(UserRole.PATIENT, UserRole.PLATFORM_ADMIN)
require_authenticated_user = get_current_user

async def get_current_tenant(
    current_user: models.User = Depends(get_current_user)
) -> TenantContext:
    """
    Extracts trusted tenant context strictly from authenticated user.
    Never trusts client-provided headers for hospital scoping.
    """
    is_platform = (current_user.role == UserRole.PLATFORM_ADMIN.value)
    return TenantContext(hospital_id=current_user.hospital_id, is_platform_admin=is_platform)

async def get_current_user_role(
    current_user: models.User = Depends(get_current_user)
) -> UserRole:
    """Dependency retrieving the authenticated user's active role."""
    try:
        return UserRole(current_user.role)
    except ValueError:
        return UserRole.PATIENT

def verify_doctor_ownership(
    doctor_id: str,
    current_user: models.User,
    db: Session
) -> models.Doctor:
    """
    Enforces Doctor Ownership (PRD Requirement 5):
    - Doctor can ONLY manage their own availability/slots.
    - Hospital Admin can manage doctors within their own hospital.
    - Platform Admin can operate globally.
    - Cross-doctor modification is strictly rejected with HTTP 403.
    """
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail=f"Doctor with ID '{doctor_id}' not found.")

    # 1. Platform Admin override
    if current_user.role == UserRole.PLATFORM_ADMIN.value:
        return doctor

    # 2. Doctor ownership check
    if current_user.role == UserRole.DOCTOR.value:
        # Match by doctor.user_id or user.doctor_profile.id
        is_owner = (doctor.user_id == current_user.id) or (
            hasattr(current_user, "doctor_profile") and current_user.doctor_profile and current_user.doctor_profile.id == doctor_id
        )
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doctor Ownership Violation: You are not authorized to modify another doctor's schedule or availability."
            )
        return doctor

    # 3. Hospital Admin check
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if doctor.hospital_id != current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation Violation: Hospital administrators cannot manage doctors belonging to another hospital."
            )
        return doctor

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: You do not have permission to manage this doctor's availability."
    )

def verify_patient_ownership(
    patient_id: str,
    current_user: models.User,
    db: Session
) -> models.Patient:
    """
    Enforces Patient Ownership (PRD Requirement 6):
    - Patient can ONLY access their own profile and appointments.
    - Hospital Admin can access patients registered with their own hospital.
    - Platform Admin can access globally.
    - Retrieving another patient's data by spoofing an ID is strictly rejected with HTTP 403.
    """
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient with ID '{patient_id}' not found.")

    # 1. Platform Admin override
    if current_user.role == UserRole.PLATFORM_ADMIN.value:
        return patient

    # 2. Patient ownership check
    if current_user.role == UserRole.PATIENT.value:
        is_owner = (patient.user_id == current_user.id) or (
            hasattr(current_user, "patient_profile") and current_user.patient_profile and current_user.patient_profile.id == patient_id
        )
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient Ownership Violation: You are not authorized to access another patient's profile."
            )
        return patient

    # 3. Hospital Admin check
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if patient.primary_hospital_id and patient.primary_hospital_id != current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation Violation: Hospital administrators cannot access patients from another hospital."
            )
        return patient

    # 4. Doctor check (Doctor can view patients who have appointments with them)
    if current_user.role == UserRole.DOCTOR.value:
        doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else None
        if doctor_id:
            has_appointment = db.query(models.Appointment).filter(
                models.Appointment.patient_id == patient.id,
                models.Appointment.doctor_id == doctor_id
            ).first()
            if has_appointment or (patient.primary_hospital_id == current_user.hospital_id):
                return patient

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: You do not have permission to access this patient profile."
    )
