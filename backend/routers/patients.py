import re
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.auth.roles import UserRole
from backend.auth.security import hash_password
from backend.auth.dependencies import (
    get_current_user, require_hospital_admin, verify_patient_ownership
)

router = APIRouter(prefix="/patients", tags=["Patients"])

# Allowed preference keys for data minimization
ALLOWED_PREFERENCE_KEYS = {
    "language", "accessibility", "notification_lead_hours", 
    "scheduling_constraints", "appointment_reminders_enabled"
}

@router.post("/register", response_model=schemas.PatientResponse)
def register_patient(req: schemas.PatientRegisterRequest, db: Session = Depends(get_db)):
    """
    Public patient self-registration endpoint.
    If a password is provided, securely creates and links a User account for JWT login.
    """
    email_clean = req.email.strip().lower()

    # 1. Validate email format
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format. Please provide a valid email address (e.g. user@example.com)."
        )

    # 2. Validate password length if provided
    if req.password is not None and len(req.password.strip()) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters in length."
        )

    # 3. Duplicate email detection
    existing_patient = db.query(models.Patient).filter(models.Patient.email == email_clean).first()
    existing_user = db.query(models.User).filter(models.User.email == email_clean).first()

    if existing_user and existing_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{email_clean}' already exists. Please log in instead."
        )

    if existing_patient and existing_patient.user_id:
        linked_user = db.query(models.User).filter(models.User.id == existing_patient.user_id).first()
        if linked_user and linked_user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An account with email '{email_clean}' already exists. Please log in instead."
            )

    user = existing_user
    if req.password and not user:
        user = models.User(
            email=email_clean,
            password_hash=hash_password(req.password),
            full_name=req.full_name,
            role=UserRole.PATIENT.value,
            phone=req.phone,
            hospital_id=req.hospital_id,
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif req.password and user and not user.password_hash:
        user.password_hash = hash_password(req.password)
        db.commit()

    if existing_patient:
        if user and not existing_patient.user_id:
            existing_patient.user_id = user.id
            db.commit()
            db.refresh(existing_patient)
        return existing_patient

    # Data minimization: filter allowed preference keys
    sanitized_preferences = {}
    if req.preferences and isinstance(req.preferences, dict):
        sanitized_preferences = {k: v for k, v in req.preferences.items() if k in ALLOWED_PREFERENCE_KEYS}

    patient = models.Patient(
        user_id=user.id if user else None,
        primary_hospital_id=req.hospital_id,
        patient_mrn=f"MRN-{uuid.uuid4().hex[:8].upper()}",
        full_name=req.full_name,
        phone=req.phone,
        email=email_clean,
        date_of_birth=req.date_of_birth,
        gender=req.gender,
        emergency_contact=req.emergency_contact,
        communication_preference=req.communication_preference or "SMS",
        preferences=sanitized_preferences,
        external_patient_id=req.external_patient_id
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient

@router.get("/me", response_model=schemas.PatientResponse)
def get_current_patient_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns the authenticated patient's own profile without requiring caller to supply patient_id.
    Strictly enforces patient ownership.
    """
    if current_user.role == UserRole.PATIENT.value:
        patient = current_user.patient_profile or db.query(models.Patient).filter(models.Patient.user_id == current_user.id).first()
        if not patient:
            patient = db.query(models.Patient).filter(models.Patient.email == current_user.email).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient profile not linked to user account.")
        return patient

    # Admin fallback
    patient = db.query(models.Patient).first()
    if not patient:
        raise HTTPException(status_code=404, detail="No patient record found.")
    return patient

@router.get("/demo", response_model=schemas.PatientResponse)
def get_demo_patient(db: Session = Depends(get_db)):
    """
    Returns default demo patient (Alice Morgan) for conversational access,
    voice concierge, and interactive booking.
    """
    pat = db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first()
    if not pat:
        pat = db.query(models.Patient).first()
    if not pat:
        raise HTTPException(status_code=404, detail="No demo patient found.")
    return pat

@router.get("", response_model=List[schemas.PatientResponse])
def list_patients(
    current_user: models.User = Depends(require_hospital_admin),
    db: Session = Depends(get_db)
):
    """
    Lists patients scoped to hospital tenant for hospital admins.
    Platform admins see all patients.
    """
    q = db.query(models.Patient)
    if current_user.role == UserRole.HOSPITAL_ADMIN.value and current_user.hospital_id:
        q = q.filter(models.Patient.primary_hospital_id == current_user.hospital_id)
    return q.order_by(models.Patient.created_at.desc()).all()

@router.get("/{patient_id}", response_model=schemas.PatientResponse)
def get_patient(
    patient_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Patient Ownership (PRD Requirement 6):
    - Patient can ONLY view their own profile.
    - Retrieving another patient's data is rejected with HTTP 403 Forbidden.
    - Hospital administrators can view patients registered with their own hospital.
    """
    return verify_patient_ownership(patient_id, current_user, db)

@router.patch("/{patient_id}", response_model=schemas.PatientResponse)
def update_patient_profile(
    patient_id: str,
    req: schemas.PatientUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Enforces Patient Ownership for updating profile fields:
    - Patient can only update permitted fields of their own profile.
    - Follows data minimization by validating and sanitizing inputs.
    """
    patient = verify_patient_ownership(patient_id, current_user, db)

    update_data = req.model_dump(exclude_unset=True)

    # Data minimization for preferences
    if "preferences" in update_data and update_data["preferences"] is not None:
        raw_prefs = update_data.pop("preferences")
        current_prefs = dict(patient.preferences or {})
        for k, v in raw_prefs.items():
            if k in ALLOWED_PREFERENCE_KEYS:
                current_prefs[k] = v
        patient.preferences = current_prefs

    if "communication_preference" in update_data and update_data["communication_preference"]:
        pref = update_data["communication_preference"].upper()
        if pref not in ["SMS", "EMAIL", "WHATSAPP", "VOICE"]:
            raise HTTPException(status_code=400, detail="Invalid communication preference. Allowed: SMS, EMAIL, WHATSAPP, VOICE")
        patient.communication_preference = pref
        del update_data["communication_preference"]

    for field, value in update_data.items():
        if hasattr(patient, field) and value is not None:
            setattr(patient, field, value)

    # Sync user name and phone if linked
    if patient.user:
        if req.full_name:
            patient.user.full_name = req.full_name
        if req.phone:
            patient.user.phone = req.phone

    db.commit()
    db.refresh(patient)
    return patient
