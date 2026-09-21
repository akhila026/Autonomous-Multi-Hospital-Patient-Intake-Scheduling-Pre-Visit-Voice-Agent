from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from backend import models, schemas
from backend.auth.security import hash_password

class HospitalService:
    @staticmethod
    def register_hospital(db: Session, req: schemas.HospitalRegisterRequest) -> models.Hospital:
        existing = db.query(models.Hospital).filter(models.Hospital.license_number == req.license_number).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Hospital with license '{req.license_number}' is already registered.")

        initial_status = req.status.upper() if req.status else "DRAFT"
        if initial_status not in ["DRAFT", "SUBMITTED", "PENDING_APPROVAL"]:
            initial_status = "DRAFT"
        if initial_status == "PENDING_APPROVAL":
            initial_status = "SUBMITTED"

        now_iso = datetime.utcnow().isoformat()
        initial_history = [
            {
                "from_status": None,
                "to_status": initial_status,
                "actor": req.admin_email or "Self-Service Registration",
                "timestamp": now_iso,
                "notes": f"Initial hospital self-service registration ({initial_status})"
            }
        ]

        hospital = models.Hospital(
            name=req.name,
            address=req.address,
            license_number=req.license_number,
            contact_email=req.contact_email,
            phone=req.phone,
            status=initial_status,
            services=req.services or ["Emergency Care", "Inpatient", "Outpatient Consultation"],
            operating_hours=req.operating_hours or {"Monday-Friday": "08:00-18:00", "Saturday": "09:00-13:00"},
            supported_healthcare_systems=req.supported_healthcare_systems or ["MOCK_EHR"],
            integration_config=req.integration_config or {"environment": "sandbox", "endpoint": "https://api.mockehr.org", "timeout_ms": 3000},
            communication_preferences={"preferred_channels": ["SMS", "EMAIL"], "reminder_window_hours": 24},
            lifecycle_history=initial_history
        )
        db.add(hospital)
        db.commit()
        db.refresh(hospital)

        # 1. Provision initial departments if specified
        if req.departments:
            for dept_name in req.departments:
                dept_name_clean = dept_name.strip()
                if dept_name_clean:
                    existing_dept = db.query(models.Department).filter(
                        models.Department.hospital_id == hospital.id,
                        models.Department.name == dept_name_clean
                    ).first()
                    if not existing_dept:
                        dept = models.Department(
                            hospital_id=hospital.id,
                            name=dept_name_clean,
                            code=dept_name_clean[:10].upper().replace(" ", "-"),
                            description=f"{dept_name_clean} department at {hospital.name}",
                            is_active=True
                        )
                        db.add(dept)

        # 2. Provision initial specialties if specified
        if req.specialties:
            for spec_name in req.specialties:
                spec_name_clean = spec_name.strip()
                if spec_name_clean:
                    existing_spec = db.query(models.Specialty).filter(models.Specialty.name == spec_name_clean).first()
                    if not existing_spec:
                        spec = models.Specialty(
                            name=spec_name_clean,
                            code=spec_name_clean[:10].upper().replace(" ", "-"),
                            description=f"{spec_name_clean} specialty services"
                        )
                        db.add(spec)

        # 3. Create Administrator User & HospitalAdmin profile if admin details provided
        if req.admin_email:
            admin_email = req.admin_email.lower().strip()
            existing_user = db.query(models.User).filter(models.User.email == admin_email).first()
            if not existing_user:
                hashed = hash_password(req.admin_password or "AdminPass123!")
                admin_user = models.User(
                    email=admin_email,
                    full_name=req.admin_name or f"{req.name} Administrator",
                    role="HOSPITAL_ADMIN",
                    phone=req.admin_phone or req.phone,
                    password_hash=hashed,
                    hospital_id=hospital.id,
                    is_active=True
                )
                db.add(admin_user)
                db.flush()

                admin_profile = models.HospitalAdmin(
                    hospital_id=hospital.id,
                    user_id=admin_user.id,
                    full_name=admin_user.full_name,
                    email=admin_user.email,
                    phone=admin_user.phone,
                    role_title=req.admin_role or "Hospital Administrator",
                    is_active=True
                )
                db.add(admin_profile)
            else:
                # If user already exists, associate with this hospital if not yet set
                if not existing_user.hospital_id:
                    existing_user.hospital_id = hospital.id
                    existing_user.role = "HOSPITAL_ADMIN"

        db.commit()
        db.refresh(hospital)
        return hospital

    @staticmethod
    def get_all_hospitals(db: Session, status: Optional[str] = None) -> List[models.Hospital]:
        q = db.query(models.Hospital)
        if status:
            status_upper = status.upper()
            if status_upper == "PENDING_APPROVAL":
                q = q.filter(models.Hospital.status.in_(["SUBMITTED", "UNDER_REVIEW", "PENDING_APPROVAL"]))
            else:
                q = q.filter(models.Hospital.status == status_upper)
        return q.order_by(models.Hospital.created_at.desc()).all()

    @staticmethod
    def get_hospital_by_id(db: Session, hospital_id: str) -> models.Hospital:
        hospital = db.query(models.Hospital).filter(models.Hospital.id == hospital_id).first()
        if not hospital:
            raise HTTPException(status_code=404, detail="Hospital not found")
        return hospital

    @staticmethod
    def transition_status(
        db: Session,
        hospital_id: str,
        new_status: str,
        actor: str = "Platform Admin",
        rejection_reason: Optional[str] = None,
        correction_notes: Optional[str] = None
    ) -> models.Hospital:
        hospital = HospitalService.get_hospital_by_id(db, hospital_id)
        current_status = hospital.status or "DRAFT"
        new_status = new_status.upper()

        allowed_statuses = ["DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED", "SUSPENDED"]
        if new_status not in allowed_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid lifecycle status: {new_status}. Allowed: {allowed_statuses}")

        history = list(hospital.lifecycle_history or [])
        history.append({
            "from_status": current_status,
            "to_status": new_status,
            "actor": actor,
            "timestamp": datetime.utcnow().isoformat(),
            "reason": rejection_reason,
            "correction_notes": correction_notes
        })

        hospital.status = new_status
        hospital.lifecycle_history = history

        if new_status == "REJECTED":
            hospital.rejection_reason = rejection_reason or "Application rejected by Platform Administration."
        elif new_status == "APPROVED":
            hospital.rejection_reason = None
            hospital.correction_notes = None
        elif new_status == "SUSPENDED":
            hospital.rejection_reason = rejection_reason or "Hospital suspended by Platform Administration."
        
        if correction_notes:
            hospital.correction_notes = correction_notes

        db.commit()
        db.refresh(hospital)
        return hospital

    @staticmethod
    def submit_hospital(db: Session, hospital_id: str, actor: str = "Hospital Admin") -> models.Hospital:
        hospital = HospitalService.get_hospital_by_id(db, hospital_id)
        if hospital.status not in ["DRAFT", "UNDER_REVIEW", "REJECTED"]:
            raise HTTPException(status_code=400, detail=f"Hospital in '{hospital.status}' cannot be submitted.")
        return HospitalService.transition_status(db, hospital_id, "SUBMITTED", actor=actor)

    @staticmethod
    def approve_hospital(db: Session, hospital_id: str, actor: str = "Platform Admin") -> models.Hospital:
        return HospitalService.transition_status(db, hospital_id, "APPROVED", actor=actor)

    @staticmethod
    def reject_hospital(db: Session, hospital_id: str, reason: str, actor: str = "Platform Admin") -> models.Hospital:
        return HospitalService.transition_status(db, hospital_id, "REJECTED", actor=actor, rejection_reason=reason)

    @staticmethod
    def request_corrections(db: Session, hospital_id: str, notes: str, actor: str = "Platform Admin") -> models.Hospital:
        return HospitalService.transition_status(db, hospital_id, "UNDER_REVIEW", actor=actor, correction_notes=notes)

    @staticmethod
    def suspend_hospital(db: Session, hospital_id: str, reason: str, actor: str = "Platform Admin") -> models.Hospital:
        return HospitalService.transition_status(db, hospital_id, "SUSPENDED", actor=actor, rejection_reason=reason)

    @staticmethod
    def reactivate_hospital(db: Session, hospital_id: str, actor: str = "Platform Admin") -> models.Hospital:
        hospital = HospitalService.get_hospital_by_id(db, hospital_id)
        if hospital.status != "SUSPENDED":
            raise HTTPException(status_code=400, detail=f"Hospital must be in SUSPENDED status to be reactivated (current: '{hospital.status}').")
        return HospitalService.transition_status(db, hospital_id, "APPROVED", actor=actor)

    @staticmethod
    def update_approval(db: Session, hospital_id: str, req: schemas.HospitalApprovalRequest, actor: str = "Platform Admin") -> models.Hospital:
        status_target = req.status.upper()
        if status_target == "REACTIVATED":
            return HospitalService.reactivate_hospital(db, hospital_id, actor=actor)
        return HospitalService.transition_status(
            db,
            hospital_id,
            status_target,
            actor=actor,
            rejection_reason=req.rejection_reason,
            correction_notes=req.correction_notes
        )

    @staticmethod
    def update_hospital_profile(db: Session, hospital_id: str, req: schemas.HospitalUpdateRequest) -> models.Hospital:
        hospital = HospitalService.get_hospital_by_id(db, hospital_id)
        if req.name is not None: hospital.name = req.name
        if req.address is not None: hospital.address = req.address
        if req.contact_email is not None: hospital.contact_email = req.contact_email
        if req.phone is not None: hospital.phone = req.phone
        if req.services is not None: hospital.services = req.services
        if req.operating_hours is not None: hospital.operating_hours = req.operating_hours
        if req.supported_healthcare_systems is not None: hospital.supported_healthcare_systems = req.supported_healthcare_systems
        if req.integration_config is not None: hospital.integration_config = req.integration_config
        if req.communication_preferences is not None: hospital.communication_preferences = req.communication_preferences

        db.commit()
        db.refresh(hospital)
        return hospital

    # --- Department Management ---
    @staticmethod
    def list_departments(db: Session, hospital_id: str) -> List[models.Department]:
        return db.query(models.Department).filter(
            models.Department.hospital_id == hospital_id,
            models.Department.is_active == True
        ).all()

    @staticmethod
    def add_department(db: Session, hospital_id: str, req: schemas.DepartmentCreateRequest) -> models.Department:
        HospitalService.get_hospital_by_id(db, hospital_id)
        existing = db.query(models.Department).filter(
            models.Department.hospital_id == hospital_id,
            models.Department.name == req.name
        ).first()
        if existing:
            existing.is_active = True
            if req.code: existing.code = req.code
            if req.description: existing.description = req.description
            db.commit()
            db.refresh(existing)
            return existing

        dept = models.Department(
            hospital_id=hospital_id,
            name=req.name,
            code=req.code or req.name[:10].upper().replace(" ", "-"),
            description=req.description,
            is_active=True
        )
        db.add(dept)
        db.commit()
        db.refresh(dept)
        return dept

    @staticmethod
    def update_department(db: Session, hospital_id: str, department_id: str, req: schemas.DepartmentUpdateRequest) -> models.Department:
        dept = db.query(models.Department).filter(
            models.Department.id == department_id,
            models.Department.hospital_id == hospital_id
        ).first()
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found in this hospital")
        if req.name is not None: dept.name = req.name
        if req.code is not None: dept.code = req.code
        if req.description is not None: dept.description = req.description
        if req.is_active is not None: dept.is_active = req.is_active
        db.commit()
        db.refresh(dept)
        return dept

    @staticmethod
    def delete_department(db: Session, hospital_id: str, department_id: str) -> bool:
        dept = db.query(models.Department).filter(
            models.Department.id == department_id,
            models.Department.hospital_id == hospital_id
        ).first()
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found in this hospital")
        dept.is_active = False
        db.commit()
        return True

    # --- Calendar Management ---
    @staticmethod
    def list_calendars(db: Session, hospital_id: str) -> List[models.Calendar]:
        return db.query(models.Calendar).filter(
            models.Calendar.hospital_id == hospital_id,
            models.Calendar.is_active == True
        ).all()

    @staticmethod
    def create_calendar(db: Session, hospital_id: str, req: schemas.CalendarCreateRequest) -> models.Calendar:
        doc = db.query(models.Doctor).filter(
            models.Doctor.id == req.doctor_id,
            models.Doctor.hospital_id == hospital_id
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Doctor not affiliated with this hospital")

        cal = models.Calendar(
            hospital_id=hospital_id,
            doctor_id=req.doctor_id,
            name=req.name or f"{doc.full_name} Clinical Calendar",
            timezone=req.timezone or "UTC",
            is_active=True
        )
        db.add(cal)
        db.commit()
        db.refresh(cal)
        return cal

    # --- Activity & Metrics ---
    @staticmethod
    def get_hospital_activity(db: Session, hospital_id: str) -> schemas.HospitalActivityResponse:
        hospital = HospitalService.get_hospital_by_id(db, hospital_id)
        total_docs = db.query(models.Doctor).filter(models.Doctor.hospital_id == hospital_id).count()
        active_docs = db.query(models.Doctor).filter(
            models.Doctor.hospital_id == hospital_id,
            models.Doctor.status == "ACTIVE",
            models.Doctor.is_active == True
        ).count()
        total_depts = db.query(models.Department).filter(
            models.Department.hospital_id == hospital_id,
            models.Department.is_active == True
        ).count()

        appt_q = db.query(models.Appointment).filter(models.Appointment.hospital_id == hospital_id)
        total_appts = appt_q.count()
        confirmed_appts = appt_q.filter(models.Appointment.status == "CONFIRMED").count()

        sync_q = db.query(models.EhrSyncLog).join(models.Appointment).filter(models.Appointment.hospital_id == hospital_id)
        sync_attempts = sync_q.count()
        sync_timeouts = sync_q.filter(models.EhrSyncLog.status == "TIMEOUT").count()

        recent_audits = db.query(models.AuditEvent).filter(
            models.AuditEvent.hospital_id == hospital_id
        ).order_by(models.AuditEvent.created_at.desc()).limit(10).all()

        return schemas.HospitalActivityResponse(
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            status=hospital.status,
            total_doctors=total_docs,
            active_doctors=active_docs,
            total_departments=total_depts,
            total_appointments=total_appts,
            confirmed_appointments=confirmed_appts,
            sync_attempts=sync_attempts,
            sync_timeouts=sync_timeouts,
            recent_audit_events=[
                {
                    "id": a.id,
                    "event_type": a.resource_type,
                    "action": a.action,
                    "status": a.status,
                    "actor_email": a.actor_id,
                    "created_at": a.created_at.isoformat() if a.created_at else None
                }
                for a in recent_audits
            ]
        )
