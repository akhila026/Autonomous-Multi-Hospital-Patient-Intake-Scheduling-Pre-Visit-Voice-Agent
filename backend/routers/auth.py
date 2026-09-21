from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.database import get_db
from backend import models, schemas
from backend.auth.roles import UserRole
from backend.auth.security import hash_password, verify_password, create_access_token
from backend.auth.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

def _resolve_user_entities(user: models.User):
    doctor_id = user.doctor_profile.id if (hasattr(user, "doctor_profile") and user.doctor_profile) else None
    patient_id = user.patient_profile.id if (hasattr(user, "patient_profile") and user.patient_profile) else None
    hospital_id = user.hospital_id
    
    if not hospital_id:
        if hasattr(user, "admin_profile") and user.admin_profile:
            hospital_id = user.admin_profile.hospital_id
        elif user.doctor_profile:
            hospital_id = user.doctor_profile.hospital_id
        elif user.patient_profile:
            hospital_id = user.patient_profile.primary_hospital_id
            
    return doctor_id, patient_id, hospital_id

@router.post("/login", response_model=schemas.TokenResponse)
def login(req: schemas.LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticates user credentials and issues a signed JWT access token.
    Token contains verified subject, role, and hospital tenant context.
    """
    email_clean = req.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email_clean).first()

    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        from backend.audit.logger import AuditLogger
        AuditLogger.log_login_access(
            action="USER_LOGIN_FAILED",
            actor_id="ANONYMOUS",
            email=email_clean,
            status="FAILED",
            details={"reason": "Invalid credentials"},
            db=db
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not user.is_active:
        from backend.audit.logger import AuditLogger
        AuditLogger.log_login_access(
            action="USER_LOGIN_DEACTIVATED",
            actor_id=user.id,
            email=user.email,
            status="FAILED",
            details={"reason": "Account deactivated"},
            db=db
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated."
        )

    doctor_id, patient_id, hospital_id = _resolve_user_entities(user)

    token_payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name,
        "hospital_id": hospital_id,
        "doctor_id": doctor_id,
        "patient_id": patient_id
    }
    access_token = create_access_token(token_payload)

    from backend.audit.logger import AuditLogger
    AuditLogger.log_login_access(
        action="USER_LOGIN_SUCCESS",
        actor_id=user.id,
        email=user.email,
        status="SUCCESS",
        tenant_id=hospital_id,
        details={"role": user.role},
        db=db
    )

    return schemas.TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=user.id,
        email=user.email,
        role=user.role,
        full_name=user.full_name,
        hospital_id=hospital_id,
        doctor_id=doctor_id,
        patient_id=patient_id
    )

@router.post("/token", response_model=schemas.TokenResponse, include_in_schema=False)
def login_oauth2(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 compatible token endpoint for Swagger UI authorization."""
    return login(schemas.LoginRequest(email=form_data.username, password=form_data.password), db=db)

@router.post("/register", response_model=schemas.UserResponse)
def register_user(req: schemas.UserRegisterRequest, db: Session = Depends(get_db)):
    """Registers a new user account with hashed credentials."""
    email_clean = req.email.strip().lower()
    existing = db.query(models.User).filter(models.User.email == email_clean).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with email '{email_clean}' already exists."
        )

    # Validate role
    valid_roles = [r.value for r in UserRole]
    if req.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{req.role}'. Valid roles are: {valid_roles}"
        )

    hashed = hash_password(req.password)
    user = models.User(
        email=email_clean,
        password_hash=hashed,
        full_name=req.full_name,
        role=req.role,
        phone=req.phone,
        hospital_id=req.hospital_id,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Automatically create Patient record if role is PATIENT
    if req.role == UserRole.PATIENT.value:
        import uuid
        patient = models.Patient(
            user_id=user.id,
            primary_hospital_id=req.hospital_id,
            patient_mrn=f"MRN-{uuid.uuid4().hex[:8].upper()}",
            full_name=req.full_name,
            email=email_clean,
            phone=req.phone or "+1-555-000-0000",
            date_of_birth=getattr(req, "date_of_birth", None),
            gender=getattr(req, "gender", None),
            emergency_contact=getattr(req, "emergency_contact", None),
            communication_preference=getattr(req, "communication_preference", "SMS") or "SMS",
            external_patient_id=getattr(req, "external_patient_id", None),
            preferences=getattr(req, "preferences", {}) or {}
        )
        db.add(patient)
        db.commit()

    return schemas.UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        phone=user.phone,
        hospital_id=user.hospital_id,
        is_active=user.is_active,
        created_at=user.created_at
    )

@router.get("/me")
def get_current_user_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns profile and tenant metadata for the authenticated user."""
    doctor_id, patient_id, hospital_id = _resolve_user_entities(current_user)
    hospital_name = None
    if hospital_id:
        hosp = db.query(models.Hospital).filter(models.Hospital.id == hospital_id).first()
        if hosp:
            hospital_name = hosp.name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "phone": current_user.phone,
        "hospital_id": hospital_id,
        "hospital_name": hospital_name,
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at
    }
