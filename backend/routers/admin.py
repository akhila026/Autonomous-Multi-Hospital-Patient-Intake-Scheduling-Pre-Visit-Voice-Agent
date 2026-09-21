from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models
from backend.auth.roles import UserRole
from backend.auth.dependencies import require_hospital_admin

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])

@router.get("/metrics")
def get_admin_metrics(
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    """
    Returns administrative metrics.
    Platform admins receive global multi-tenant totals.
    Hospital admins receive strictly tenant-scoped metrics for their own hospital.
    """
    is_platform = (current_user.role == UserRole.PLATFORM_ADMIN.value)
    h_id = current_user.hospital_id

    if is_platform:
        total_hospitals = db.query(models.Hospital).count()
        approved_hospitals = db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").count()
        pending_hospitals = db.query(models.Hospital).filter(models.Hospital.status == "PENDING_APPROVAL").count()

        total_doctors = db.query(models.Doctor).count()
        total_patients = db.query(models.Patient).count()

        appt_q = db.query(models.Appointment)
        total_appointments = appt_q.count()
        confirmed_appointments = appt_q.filter(models.Appointment.status == "CONFIRMED").count()
        sync_failed_appointments = appt_q.filter(models.Appointment.status == "SYNC_FAILED").count()

        sync_q = db.query(models.EhrSyncLog)
        total_sync_logs = sync_q.count()
        timeout_logs = sync_q.filter(models.EhrSyncLog.status == "TIMEOUT").count()
        verify_recovery_logs = sync_q.filter(models.EhrSyncLog.action == "VERIFY_RECOVERY").count()
        recent_logs = sync_q.order_by(models.EhrSyncLog.created_at.desc()).limit(10).all()
    else:
        # Hospital-scoped
        total_hospitals = 1
        approved_hospitals = 1 if (current_user.hospital and current_user.hospital.status == "APPROVED") else 0
        pending_hospitals = 0

        total_doctors = db.query(models.Doctor).filter(models.Doctor.hospital_id == h_id).count()
        total_patients = db.query(models.Patient).filter(models.Patient.primary_hospital_id == h_id).count()

        appt_q = db.query(models.Appointment).filter(models.Appointment.hospital_id == h_id)
        total_appointments = appt_q.count()
        confirmed_appointments = appt_q.filter(models.Appointment.status == "CONFIRMED").count()
        sync_failed_appointments = appt_q.filter(models.Appointment.status == "SYNC_FAILED").count()

        sync_q = db.query(models.EhrSyncLog).join(models.Appointment).filter(models.Appointment.hospital_id == h_id)
        total_sync_logs = sync_q.count()
        timeout_logs = sync_q.filter(models.EhrSyncLog.status == "TIMEOUT").count()
        verify_recovery_logs = sync_q.filter(models.EhrSyncLog.action == "VERIFY_RECOVERY").count()
        recent_logs = sync_q.order_by(models.EhrSyncLog.created_at.desc()).limit(10).all()

    return {
        "hospitals": {
            "total": total_hospitals,
            "approved": approved_hospitals,
            "pending": pending_hospitals
        },
        "doctors": {
            "total": total_doctors
        },
        "patients": {
            "total": total_patients
        },
        "appointments": {
            "total": total_appointments,
            "confirmed": confirmed_appointments,
            "sync_failed": sync_failed_appointments
        },
        "ehr_integration": {
            "total_sync_attempts": total_sync_logs,
            "timeouts_encountered": timeout_logs,
            "idempotent_recoveries": verify_recovery_logs,
            "resilience_recovery_rate": f"{(verify_recovery_logs / timeout_logs * 100):.1f}%" if timeout_logs > 0 else "100.0%"
        },
        "recent_sync_logs": [
            {
                "id": l.id,
                "appointment_id": l.appointment_id,
                "idempotency_key": l.idempotency_key,
                "action": l.action,
                "status": l.status,
                "response_time_ms": l.response_time_ms,
                "error_message": l.error_message,
                "created_at": l.created_at
            } for l in recent_logs
        ]
    }

# =========================================================================
# Platform Admin Hospital Onboarding & Lifecycle Governance (PRD Section 5)
# =========================================================================
from typing import List
from backend import schemas
from backend.services.hospital_service import HospitalService
from backend.auth.dependencies import require_platform_admin

@router.get("/hospitals/applications", response_model=List[schemas.HospitalResponse])
def list_hospital_applications(
    status: Optional[str] = None,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Review all incoming and active hospital applications."""
    return HospitalService.get_all_hospitals(db, status=status)

@router.post("/hospitals/{hospital_id}/approve", response_model=schemas.HospitalResponse)
def approve_hospital(
    hospital_id: str,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Approve hospital application to enable active doctors and booking."""
    return HospitalService.approve_hospital(db, hospital_id, actor=current_user.email)

@router.post("/hospitals/{hospital_id}/reject", response_model=schemas.HospitalResponse)
def reject_hospital(
    hospital_id: str,
    req: schemas.HospitalApprovalRequest,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Reject hospital application with specified reason."""
    reason = req.rejection_reason or "Application rejected by Platform Administration."
    return HospitalService.reject_hospital(db, hospital_id, reason=reason, actor=current_user.email)

@router.post("/hospitals/{hospital_id}/request-corrections", response_model=schemas.HospitalResponse)
def request_hospital_corrections(
    hospital_id: str,
    req: schemas.HospitalCorrectionRequest,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Request corrections from applicant hospital with feedback notes."""
    return HospitalService.request_corrections(db, hospital_id, notes=req.correction_notes, actor=current_user.email)

@router.post("/hospitals/{hospital_id}/suspend", response_model=schemas.HospitalResponse)
def suspend_hospital(
    hospital_id: str,
    req: schemas.HospitalSuspensionRequest,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Suspend active hospital, preventing slot creation and booking."""
    return HospitalService.suspend_hospital(db, hospital_id, reason=req.reason, actor=current_user.email)

@router.post("/hospitals/{hospital_id}/reactivate", response_model=schemas.HospitalResponse)
def reactivate_hospital(
    hospital_id: str,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: Reactivate a suspended hospital back to APPROVED status."""
    return HospitalService.reactivate_hospital(db, hospital_id, actor=current_user.email)

@router.get("/hospitals/{hospital_id}/activity", response_model=schemas.HospitalActivityResponse)
def get_hospital_activity(
    hospital_id: str,
    current_user: models.User = Depends(require_platform_admin),
    db: Session = Depends(get_db)
):
    """Platform Admin: View complete activity, appointment volume, and audit events for a hospital."""
    return HospitalService.get_hospital_activity(db, hospital_id)
