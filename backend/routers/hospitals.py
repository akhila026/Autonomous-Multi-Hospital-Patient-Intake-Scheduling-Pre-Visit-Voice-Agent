from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.services.hospital_service import HospitalService
from backend.auth.roles import UserRole
from backend.auth.dependencies import (
    require_platform_admin, require_hospital_admin, get_optional_current_user, get_current_user
)

router = APIRouter(prefix="/hospitals", tags=["Hospitals"])

def verify_hospital_tenant_access(hospital_id: str, current_user: Optional[models.User]):
    """
    Enforces Tenant Isolation:
    Platform Admins have system-wide visibility.
    Hospital Admins can ONLY access their own hospital.
    """
    if not current_user:
        return
    if current_user.role == UserRole.PLATFORM_ADMIN.value:
        return
    if current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if current_user.hospital_id != hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation Violation: Hospital administrators cannot access another hospital's private profile or resources."
            )
    elif current_user.role == UserRole.DOCTOR.value:
        doc = current_user.doctor_profile
        if doc and doc.hospital_id != hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant Isolation Violation: Physicians cannot access another hospital's private data."
            )

@router.post("/register", response_model=schemas.HospitalResponse)
def register_hospital(req: schemas.HospitalRegisterRequest, db: Session = Depends(get_db)):
    """Public hospital registration onboarding endpoint."""
    return HospitalService.register_hospital(db, req)

@router.post("/{hospital_id}/submit", response_model=schemas.HospitalResponse)
def submit_hospital_application(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Submits a draft or reviewed hospital application for platform admin approval."""
    verify_hospital_tenant_access(hospital_id, current_user)
    actor = current_user.email if current_user else "Hospital Admin"
    return HospitalService.submit_hospital(db, hospital_id, actor=actor)

@router.get("", response_model=List[schemas.HospitalResponse])
def list_hospitals(
    status: Optional[str] = Query(None),
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Lists hospitals.
    Platform admins can see all hospitals in any status.
    Hospital admins see their own hospital or approved ones.
    Public callers see approved hospitals by default.
    """
    if current_user and current_user.role == UserRole.PLATFORM_ADMIN.value:
        return HospitalService.get_all_hospitals(db, status=status)
    if current_user and current_user.role == UserRole.HOSPITAL_ADMIN.value:
        if current_user.hospital_id:
            hosp = db.query(models.Hospital).filter(models.Hospital.id == current_user.hospital_id).all()
            return hosp
    return HospitalService.get_all_hospitals(db, status=status or "APPROVED")

@router.get("/{hospital_id}", response_model=schemas.HospitalResponse)
def get_hospital(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Tenant Isolation:
    Hospital administrators can ONLY access their own hospital data.
    Cross-hospital requests are rejected with HTTP 403 Forbidden.
    """
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.get_hospital_by_id(db, hospital_id)

@router.patch("/{hospital_id}", response_model=schemas.HospitalResponse)
def update_hospital_profile(
    hospital_id: str,
    req: schemas.HospitalUpdateRequest,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    """
    Allows Hospital Admin to update their own hospital profile, services, hours, or configuration.
    Platform Admins can update any hospital.
    """
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.update_hospital_profile(db, hospital_id, req)

@router.patch("/{hospital_id}/approval", response_model=schemas.HospitalResponse)
def update_hospital_approval(
    hospital_id: str,
    req: schemas.HospitalApprovalRequest,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """
    Platform Admin only endpoint for approving, rejecting, suspending, or reactivating hospitals (PRD Section 5 & 21).
    """
    actor = current_user.email if current_user else "Platform Admin"
    return HospitalService.update_approval(db, hospital_id, req, actor=actor)

# --- Department Endpoints ---
@router.get("/{hospital_id}/departments", response_model=List[schemas.DepartmentResponse])
def list_departments(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.list_departments(db, hospital_id)

@router.post("/{hospital_id}/departments", response_model=schemas.DepartmentResponse)
def add_department(
    hospital_id: str,
    req: schemas.DepartmentCreateRequest,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.add_department(db, hospital_id, req)

@router.patch("/{hospital_id}/departments/{department_id}", response_model=schemas.DepartmentResponse)
def update_department(
    hospital_id: str,
    department_id: str,
    req: schemas.DepartmentUpdateRequest,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.update_department(db, hospital_id, department_id, req)

@router.delete("/{hospital_id}/departments/{department_id}")
def delete_department(
    hospital_id: str,
    department_id: str,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    success = HospitalService.delete_department(db, hospital_id, department_id)
    return {"message": "Department deactivated successfully", "success": success}

# --- Calendar Endpoints ---
@router.get("/{hospital_id}/calendars", response_model=List[schemas.CalendarResponse])
def list_calendars(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.list_calendars(db, hospital_id)

@router.post("/{hospital_id}/calendars", response_model=schemas.CalendarResponse)
def create_calendar(
    hospital_id: str,
    req: schemas.CalendarCreateRequest,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.create_calendar(db, hospital_id, req)

# --- Communication Preferences & Integration ---
@router.get("/{hospital_id}/communication-preferences")
def get_communication_preferences(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    hosp = HospitalService.get_hospital_by_id(db, hospital_id)
    return hosp.communication_preferences or {"preferred_channels": ["SMS", "EMAIL"], "reminder_window_hours": 24}

@router.put("/{hospital_id}/communication-preferences")
def update_communication_preferences(
    hospital_id: str,
    prefs: Dict[str, Any],
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.update_hospital_profile(
        db,
        hospital_id,
        schemas.HospitalUpdateRequest(communication_preferences=prefs)
    )

@router.get("/{hospital_id}/integration")
def get_integration_config(
    hospital_id: str,
    current_user: Optional[models.User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    hosp = HospitalService.get_hospital_by_id(db, hospital_id)
    return {
        "supported_healthcare_systems": hosp.supported_healthcare_systems or ["MOCK_EHR"],
        "integration_config": hosp.integration_config or {"environment": "sandbox", "endpoint": "https://api.mockehr.org", "timeout_ms": 3000}
    }

@router.put("/{hospital_id}/integration")
def update_integration_config(
    hospital_id: str,
    config: Dict[str, Any],
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    hosp = HospitalService.get_hospital_by_id(db, hospital_id)
    if hosp.status != "APPROVED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot configure production integrations on unapproved hospital (status: '{hosp.status}')."
        )
    return HospitalService.update_hospital_profile(
        db,
        hospital_id,
        schemas.HospitalUpdateRequest(
            integration_config=config.get("integration_config"),
            supported_healthcare_systems=config.get("supported_healthcare_systems")
        )
    )

# --- Hospital Activity ---
@router.get("/{hospital_id}/activity", response_model=schemas.HospitalActivityResponse)
def get_hospital_activity(
    hospital_id: str,
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    verify_hospital_tenant_access(hospital_id, current_user)
    return HospitalService.get_hospital_activity(db, hospital_id)
