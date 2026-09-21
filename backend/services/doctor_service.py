from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from backend import models, schemas

class DoctorService:
    @staticmethod
    def create_doctor(db: Session, hospital_id: str, req: schemas.DoctorCreateRequest) -> models.Doctor:
        hospital = db.query(models.Hospital).filter(models.Hospital.id == hospital_id).first()
        if not hospital:
            raise HTTPException(status_code=404, detail="Hospital not found")
        if hospital.status != "APPROVED":
            raise HTTPException(status_code=400, detail="Cannot add doctors to an unapproved hospital.")

        status_val = req.status.upper() if req.status else "ACTIVE"
        is_active_val = (status_val == "ACTIVE")

        doctor = models.Doctor(
            hospital_id=hospital_id,
            department_id=req.department_id,
            specialty_id=req.specialty_id,
            full_name=req.full_name,
            specialty=req.specialty,
            photo_url=req.photo_url,
            qualifications=req.qualifications,
            experience_years=req.experience_years if req.experience_years is not None else 5,
            languages=req.languages or "English",
            bio=req.bio,
            consultation_fee=req.consultation_fee if req.consultation_fee is not None else 150.0,
            slot_duration_min=req.slot_duration_min or 30,
            supported_appointment_types=req.supported_appointment_types or "IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT",
            external_provider_id=req.external_provider_id,
            status=status_val,
            is_active=is_active_val
        )
        db.add(doctor)
        db.commit()
        db.refresh(doctor)
        return doctor

    @staticmethod
    def update_doctor(db: Session, doctor_id: str, req: schemas.DoctorUpdateRequest) -> models.Doctor:
        doc = DoctorService.get_doctor_by_id(db, doctor_id)
        if req.full_name is not None: doc.full_name = req.full_name
        if req.specialty is not None: doc.specialty = req.specialty
        if req.department_id is not None: doc.department_id = req.department_id
        if req.specialty_id is not None: doc.specialty_id = req.specialty_id
        if req.photo_url is not None: doc.photo_url = req.photo_url
        if req.qualifications is not None: doc.qualifications = req.qualifications
        if req.experience_years is not None: doc.experience_years = req.experience_years
        if req.languages is not None: doc.languages = req.languages
        if req.bio is not None: doc.bio = req.bio
        if req.consultation_fee is not None: doc.consultation_fee = req.consultation_fee
        if req.slot_duration_min is not None: doc.slot_duration_min = req.slot_duration_min
        if req.supported_appointment_types is not None: doc.supported_appointment_types = req.supported_appointment_types
        if req.external_provider_id is not None: doc.external_provider_id = req.external_provider_id
        if req.status is not None:
            doc.status = req.status.upper()
            doc.is_active = (doc.status == "ACTIVE")
        if req.is_active is not None:
            doc.is_active = req.is_active
            if not req.is_active and doc.status == "ACTIVE":
                doc.status = "INACTIVE"

        db.commit()
        db.refresh(doc)
        return doc

    @staticmethod
    def update_doctor_status(db: Session, doctor_id: str, new_status: str) -> models.Doctor:
        doc = DoctorService.get_doctor_by_id(db, doctor_id)
        status_upper = new_status.upper()
        allowed = ["INVITED", "ACTIVE", "INACTIVE", "SUSPENDED"]
        if status_upper not in allowed:
            raise HTTPException(status_code=400, detail=f"Invalid doctor status '{new_status}'. Must be one of: {allowed}")

        doc.status = status_upper
        doc.is_active = (status_upper == "ACTIVE")
        db.commit()
        db.refresh(doc)
        return doc

    @staticmethod
    def get_doctors(
        db: Session,
        hospital_id: Optional[str] = None,
        specialty: Optional[str] = None,
        active_only: bool = True
    ) -> List[models.Doctor]:
        q = db.query(models.Doctor)
        if active_only:
            q = q.filter(models.Doctor.is_active == True, models.Doctor.status == "ACTIVE")
        if hospital_id:
            q = q.filter(models.Doctor.hospital_id == hospital_id)
        if specialty:
            q = q.filter(models.Doctor.specialty.ilike(f"%{specialty}%"))
        return q.order_by(models.Doctor.full_name.asc()).all()

    @staticmethod
    def get_doctor_by_id(db: Session, doctor_id: str) -> models.Doctor:
        doc = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Doctor not found")
        return doc

    @staticmethod
    def set_availability_schedules(
        db: Session,
        doctor_id: str,
        schedules: List[schemas.AvailabilityScheduleItem]
    ) -> List[models.AvailabilitySchedule]:
        doc = DoctorService.get_doctor_by_id(db, doctor_id)
        if doc.hospital and doc.hospital.status != "APPROVED":
            raise HTTPException(status_code=400, detail=f"Cannot publish availability for doctor in unapproved hospital (status: '{doc.hospital.status}').")
        if doc.status != "ACTIVE":
            raise HTTPException(status_code=400, detail=f"Cannot publish availability for doctor with status '{doc.status}'. Doctor must be ACTIVE.")

        # Clear existing schedules for this doctor
        db.query(models.AvailabilitySchedule).filter(models.AvailabilitySchedule.doctor_id == doctor_id).delete()

        created = []
        for item in schedules:
            sched = models.AvailabilitySchedule(
                doctor_id=doctor_id,
                day_of_week=item.day_of_week,
                start_time=item.start_time,
                end_time=item.end_time,
                is_active=item.is_active
            )
            db.add(sched)
            created.append(sched)

        db.commit()
        return created

    # --- Blocked Slots ---
    @staticmethod
    def create_blocked_slot(db: Session, doctor_id: str, hospital_id: str, req: schemas.BlockedSlotCreateRequest) -> models.BlockedSlot:
        doc = DoctorService.get_doctor_by_id(db, doctor_id)
        if doc.hospital_id != hospital_id:
            raise HTTPException(status_code=400, detail="Doctor does not belong to specified hospital.")

        if req.end_time <= req.start_time:
            raise HTTPException(status_code=400, detail="Blocked slot end_time must be after start_time.")

        block = models.BlockedSlot(
            hospital_id=hospital_id,
            doctor_id=doctor_id,
            start_time=req.start_time,
            end_time=req.end_time,
            reason=req.reason or "Doctor Unavailable / Blocked Period"
        )
        db.add(block)
        db.commit()
        db.refresh(block)
        return block

    @staticmethod
    def list_blocked_slots(db: Session, doctor_id: str) -> List[models.BlockedSlot]:
        return db.query(models.BlockedSlot).filter(
            models.BlockedSlot.doctor_id == doctor_id
        ).order_by(models.BlockedSlot.start_time.asc()).all()

    @staticmethod
    def delete_blocked_slot(db: Session, doctor_id: str, blocked_slot_id: str) -> bool:
        block = db.query(models.BlockedSlot).filter(
            models.BlockedSlot.id == blocked_slot_id,
            models.BlockedSlot.doctor_id == doctor_id
        ).first()
        if not block:
            raise HTTPException(status_code=404, detail="Blocked slot not found")
        db.delete(block)
        db.commit()
        return True
