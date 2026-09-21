import uuid
from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from fastapi import HTTPException, status

from backend import models, schemas
from backend.config import settings
from backend.scheduling.locks import slot_lock_manager
from backend.auth.roles import UserRole
from backend.auth.tenant import TenantContext, validate_appointment_tenant_isolation


class SchedulingService:
    """
    Central service for doctor availability, strict 7-rule bookability verification,
    atomic slot reservations, concurrency double-booking prevention, and reservation release.
    Adheres to PRD Sections 6, 7, 14, 21, and 22.
    """

    @staticmethod
    def cleanup_expired_holds(db: Session):
        """Releases any slots whose hold duration has elapsed."""
        now = datetime.utcnow()
        expired_slots = db.query(models.TimeSlot).filter(
            models.TimeSlot.status == "HELD",
            models.TimeSlot.hold_expires_at < now
        ).all()

        for slot in expired_slots:
            slot.status = "AVAILABLE"
            slot.hold_expires_at = None
            slot_lock_manager.release_hold_sync(slot.id)

        if expired_slots:
            db.commit()

    @staticmethod
    def validate_slot(
        db: Session,
        doctor_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        slot_id: Optional[str] = None,
        appointment_type: str = "IN_PERSON",
        calendar_id: Optional[str] = None,
        tenant_context: Optional[TenantContext] = None,
        exclude_appointment_id: Optional[str] = None,
        ignore_slot_id: Optional[str] = None
    ) -> schemas.SlotValidationResponse:
        """
        Evaluates the 7 strict bookability rules for a given slot/interval (PRD Section 7):
        1. Doctor is active.
        2. Doctor's calendar is active.
        3. Slot is within doctor's configured working hours.
        4. Slot is not blocked (surgery, rounds, admin break).
        5. Doctor is not on leave / unavailable.
        6. Slot is not already booked or actively held.
        7. Requested appointment type is compatible.
        """
        # Step 0: Fetch Doctor
        doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail=f"Doctor with ID '{doctor_id}' not found.")

        # Enforce tenant isolation if tenant context provided
        if tenant_context:
            tenant_context.validate_access(doctor.hospital_id, resource_name="doctor availability")

        # Resolve slot if slot_id given
        target_slot = None
        if slot_id:
            target_slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == slot_id).first()
            if not target_slot:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason=f"Time slot with ID '{slot_id}' not found.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )
            start_time = target_slot.start_time
            end_time = target_slot.end_time

        if not start_time:
            return schemas.SlotValidationResponse(
                is_bookable=False,
                reason="Slot start_time must be provided or resolved from slot_id.",
                doctor_id=doctor.id,
                doctor_name=doctor.full_name,
                start_time=None,
                end_time=None,
                appointment_type=appointment_type
            )

        if not end_time:
            end_time = start_time + timedelta(minutes=doctor.slot_duration_min or 30)

        # Rule 0: Slot datetime has not already passed
        from backend.config import get_app_now
        now = get_app_now()
        if start_time <= now:
            return schemas.SlotValidationResponse(
                is_bookable=False,
                reason=f"Slot start time ({start_time.strftime('%Y-%m-%d %H:%M')}) has already passed.",
                doctor_id=doctor.id,
                doctor_name=doctor.full_name,
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type
            )

        # Rule 0.5: Hospital is approved
        if doctor.hospital and doctor.hospital.status != "APPROVED":
            return schemas.SlotValidationResponse(
                is_bookable=False,
                reason=f"Hospital '{doctor.hospital.name}' is not in APPROVED status (current: {doctor.hospital.status}). Only approved hospitals may receive appointments.",
                doctor_id=doctor.id,
                doctor_name=doctor.full_name,
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type
            )

        # Rule 1: Doctor is active
        if not doctor.is_active or (doctor.status and doctor.status.upper() != "ACTIVE"):
            return schemas.SlotValidationResponse(
                is_bookable=False,
                reason=f"Doctor {doctor.full_name} is currently inactive (status: {doctor.status}).",
                doctor_id=doctor.id,
                doctor_name=doctor.full_name,
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type
            )

        # Rule 2: Doctor's calendar is active
        if calendar_id:
            cal = db.query(models.Calendar).filter(models.Calendar.id == calendar_id).first()
            if cal and not cal.is_active:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason="Doctor's specified calendar is inactive.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )
        else:
            # Check doctor's calendars
            doctor_calendars = db.query(models.Calendar).filter(models.Calendar.doctor_id == doctor.id).all()
            if doctor_calendars and not any(c.is_active for c in doctor_calendars):
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason="Doctor's clinical calendar is currently inactive.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )

        # Rule 3: Within doctor's configured working hours
        weekday = start_time.weekday()  # 0=Monday ... 6=Sunday
        slot_start_time_str = start_time.strftime("%H:%M")
        slot_end_time_str = end_time.strftime("%H:%M")

        # Check in availabilities or availability_schedules
        availabilities = db.query(models.Availability).filter(
            models.Availability.doctor_id == doctor.id,
            models.Availability.is_active == True,
            models.Availability.day_of_week == weekday
        ).all()

        schedules = db.query(models.AvailabilitySchedule).filter(
            models.AvailabilitySchedule.doctor_id == doctor.id,
            models.AvailabilitySchedule.is_active == True,
            models.AvailabilitySchedule.day_of_week == weekday
        ).all()

        has_any_schedule_rules = (
            db.query(models.Availability).filter(models.Availability.doctor_id == doctor.id).first() is not None or
            db.query(models.AvailabilitySchedule).filter(models.AvailabilitySchedule.doctor_id == doctor.id).first() is not None
        )

        if has_any_schedule_rules:
            all_working_rules = availabilities + schedules
            if not all_working_rules:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason=f"Doctor does not have configured working hours on {start_time.strftime('%A')}.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )

            within_hours = False
            for rule in all_working_rules:
                if rule.start_time <= slot_start_time_str and rule.end_time >= slot_end_time_str:
                    within_hours = True
                    break

            if not within_hours:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason=f"Requested slot time {slot_start_time_str}-{slot_end_time_str} falls outside doctor's working hours.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )

        # Rule 4 & 5: Slot is not blocked and Doctor is not on leave
        blocked_periods = db.query(models.BlockedSlot).filter(
            models.BlockedSlot.doctor_id == doctor.id,
            models.BlockedSlot.start_time < end_time,
            models.BlockedSlot.end_time > start_time
        ).all()

        if blocked_periods:
            for block in blocked_periods:
                reason_lower = (block.reason or "").lower()
                if any(k in reason_lower for k in ["leave", "vacation", "sick", "holiday", "unavailable"]):
                    return schemas.SlotValidationResponse(
                        is_bookable=False,
                        reason=f"Doctor is on leave or unavailable during this period: {block.reason}",
                        doctor_id=doctor.id,
                        doctor_name=doctor.full_name,
                        start_time=start_time,
                        end_time=end_time,
                        appointment_type=appointment_type
                    )
                else:
                    return schemas.SlotValidationResponse(
                        is_bookable=False,
                        reason=f"Slot conflicts with a blocked period: {block.reason}",
                        doctor_id=doctor.id,
                        doctor_name=doctor.full_name,
                        start_time=start_time,
                        end_time=end_time,
                        appointment_type=appointment_type
                    )

        # Rule 6: Slot is not already booked or actively held
        if target_slot and target_slot.id != ignore_slot_id:
            now = datetime.utcnow()
            if target_slot.status == "BOOKED":
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason="This time slot has already been booked.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )
            if target_slot.status == "HELD" and target_slot.hold_expires_at and target_slot.hold_expires_at > now:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason="This time slot is temporarily held by another patient.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )

        # Check existing active appointments overlapping this time window
        appt_query = db.query(models.Appointment).filter(
            models.Appointment.doctor_id == doctor.id,
            models.Appointment.status.in_(["CONFIRMED", "HELD", "PENDING_EHR_SYNC", "PENDING", "SYNC_RECONCILING", "UNKNOWN_OUTCOME"]),
            models.Appointment.start_time < end_time,
            models.Appointment.end_time > start_time
        )
        if exclude_appointment_id:
            appt_query = appt_query.filter(models.Appointment.id != exclude_appointment_id)

        overlapping_appts = appt_query.all()

        if overlapping_appts:
            return schemas.SlotValidationResponse(
                is_bookable=False,
                reason="Doctor already has an active appointment scheduled during this time slot.",
                doctor_id=doctor.id,
                doctor_name=doctor.full_name,
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type
            )

        # Rule 7: Requested appointment type is compatible
        if doctor.supported_appointment_types:
            supported = [t.strip().upper() for t in doctor.supported_appointment_types.split(",") if t.strip()]
            if appointment_type.upper() not in supported:
                return schemas.SlotValidationResponse(
                    is_bookable=False,
                    reason=f"Appointment type '{appointment_type}' is not supported by Dr. {doctor.full_name}. Supported types: {doctor.supported_appointment_types}.",
                    doctor_id=doctor.id,
                    doctor_name=doctor.full_name,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_type=appointment_type
                )

        # All 7 rules passed!
        return schemas.SlotValidationResponse(
            is_bookable=True,
            reason=None,
            doctor_id=doctor.id,
            doctor_name=doctor.full_name,
            start_time=start_time,
            end_time=end_time,
            appointment_type=appointment_type
        )

    @staticmethod
    def check_availability(
        db: Session,
        doctor_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        appointment_type: str = "IN_PERSON",
        calendar_id: Optional[str] = None,
        tenant_context: Optional[TenantContext] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries and returns all strictly bookable slots for a doctor within a date range,
        validating all 7 rules in real-time.
        """
        SchedulingService.cleanup_expired_holds(db)

        doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail=f"Doctor with ID '{doctor_id}' not found.")

        if tenant_context:
            tenant_context.validate_access(doctor.hospital_id, resource_name="doctor availability")

        # Fast check: If doctor inactive, return empty
        if not doctor.is_active or (doctor.status and doctor.status.upper() != "ACTIVE"):
            return []

        # Parse date boundaries
        from backend.config import get_app_now
        now = get_app_now()
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                start_dt = now
        else:
            start_dt = now

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                end_dt = max(start_dt, now) + timedelta(days=7)
        else:
            end_dt = max(start_dt, now) + timedelta(days=7)

        # Query candidate slots: complete slot datetime must be strictly in the future (start_time > now)
        candidate_slots = db.query(models.TimeSlot).filter(
            models.TimeSlot.doctor_id == doctor.id,
            models.TimeSlot.start_time > now,
            models.TimeSlot.start_time >= start_dt,
            models.TimeSlot.start_time <= end_dt
        ).order_by(models.TimeSlot.start_time.asc()).all()

        bookable_slots = []
        for slot in candidate_slots:
            val = SchedulingService.validate_slot(
                db=db,
                doctor_id=doctor.id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                slot_id=slot.id,
                appointment_type=appointment_type,
                calendar_id=calendar_id,
                tenant_context=tenant_context
            )
            if val.is_bookable:
                bookable_slots.append({
                    "id": slot.id,
                    "doctor_id": doctor.id,
                    "doctor_name": doctor.full_name,
                    "start_time": slot.start_time,
                    "end_time": slot.end_time,
                    "status": slot.status,
                    "appointment_type": appointment_type,
                    "slot_duration_min": doctor.slot_duration_min or 30
                })

        return bookable_slots

    @staticmethod
    def create_reservation(
        db: Session,
        req: schemas.ReservationCreateRequest,
        current_user: models.User
    ) -> models.Appointment:
        """
        Creates an appointment hold / reservation with atomic double-booking prevention,
        verifying tenant isolation, doctor ownership, patient authorization, and the 7 rules.
        """
        SchedulingService.cleanup_expired_holds(db)

        # 1. Fetch Doctor and Patient
        doctor = db.query(models.Doctor).filter(models.Doctor.id == req.doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found.")

        patient = db.query(models.Patient).filter(models.Patient.id == req.patient_id).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found.")

        # 2. Authorization & Tenant Isolation Checks
        is_platform_admin = (current_user.role == UserRole.PLATFORM_ADMIN.value)
        is_hospital_admin = (current_user.role == UserRole.HOSPITAL_ADMIN.value)
        is_patient = (current_user.role == UserRole.PATIENT.value)

        # Tenant isolation
        if is_hospital_admin:
            if current_user.hospital_id != doctor.hospital_id or current_user.hospital_id != req.hospital_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant Isolation Violation: Hospital administrators cannot book appointments for other hospitals."
                )

        if not is_platform_admin and not is_hospital_admin:
            if current_user.hospital_id and current_user.hospital_id != doctor.hospital_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant Isolation Violation: Access forbidden across hospital boundaries."
                )

        if is_patient:
            # Patient can only reserve for their own profile
            patient_matches = (
                patient.user_id == current_user.id or
                (hasattr(current_user, "patient_profile") and current_user.patient_profile and current_user.patient_profile.id == patient.id)
            )
            if not patient_matches and patient.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient Authorization Violation: You cannot reserve appointments for another patient."
                )

        # Service-layer tenant consistency validation
        calendar = doctor.calendars[0] if (hasattr(doctor, "calendars") and doctor.calendars) else None
        validate_appointment_tenant_isolation(
            hospital_id=req.hospital_id,
            doctor_hospital_id=doctor.hospital_id,
            patient_hospital_id=patient.primary_hospital_id,
            calendar_hospital_id=calendar.hospital_id if calendar else None
        )

        # 3. Fetch Slot
        slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == req.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Time slot not found.")

        # 4. Validate All 7 Bookability Rules
        val = SchedulingService.validate_slot(
            db=db,
            doctor_id=doctor.id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            slot_id=slot.id,
            appointment_type=req.appointment_type,
            calendar_id=calendar.id if calendar else None
        )
        if not val.is_bookable:
            # If already booked or held, return 409 Conflict per PRD §7
            if any(term in (val.reason or "").lower() for term in ["booked", "held by another", "active appointment", "existing appointment"]):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=val.reason)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=val.reason)

        # 5. Atomic Mutex & DB Check-and-Set to Prevent Double-Booking
        now = datetime.utcnow()
        hold_expiration = now + timedelta(minutes=settings.SLOT_HOLD_DURATION_MINUTES)

        with slot_lock_manager.lock(slot.id):
            # Atomic update: only succeeds if slot is currently AVAILABLE or hold has expired
            updated_rows = db.query(models.TimeSlot).filter(
                models.TimeSlot.id == slot.id,
                or_(
                    models.TimeSlot.status == "AVAILABLE",
                    and_(models.TimeSlot.status == "HELD", models.TimeSlot.hold_expires_at < now)
                )
            ).update({
                "status": "HELD",
                "hold_expires_at": hold_expiration
            }, synchronize_session=False)

            db.commit()

            if updated_rows == 0:
                # Another concurrent request grabbed the slot first!
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This time slot has already been booked or is held by another patient."
                )

            # Check Idempotency Key
            idempotency_key = req.idempotency_key or str(uuid.uuid4())
            existing_appointment = db.query(models.Appointment).filter(
                models.Appointment.idempotency_key == idempotency_key
            ).first()

            if existing_appointment:
                return existing_appointment

            # 6. Create Appointment Record with status HELD
            appointment = models.Appointment(
                hospital_id=doctor.hospital_id,
                patient_id=patient.id,
                doctor_id=doctor.id,
                calendar_id=calendar.id if calendar else None,
                slot_id=slot.id,
                appointment_type=req.appointment_type,
                start_time=slot.start_time,
                end_time=slot.end_time,
                status="HELD",
                idempotency_key=idempotency_key,
                chief_complaint=req.chief_complaint,
                urgency_level=req.urgency_level
            )
            db.add(appointment)
            db.commit()
            db.refresh(appointment)

            return appointment

    @staticmethod
    def release_slot(
        db: Session,
        appointment_id: str,
        current_user: models.User
    ) -> schemas.ReservationReleaseResponse:
        """
        Releases an appointment reservation, validates caller authorization,
        cancels the appointment, and resets the slot to AVAILABLE if it is still valid.
        """
        appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if not appointment:
            raise HTTPException(status_code=404, detail=f"Appointment with ID '{appointment_id}' not found.")

        # Caller authorization
        is_platform_admin = (current_user.role == UserRole.PLATFORM_ADMIN.value)
        is_hospital_admin = (current_user.role == UserRole.HOSPITAL_ADMIN.value)
        is_doctor = (current_user.role == UserRole.DOCTOR.value)
        is_patient = (current_user.role == UserRole.PATIENT.value)

        if is_hospital_admin:
            if appointment.hospital_id != current_user.hospital_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant Isolation Violation: Hospital administrators cannot cancel appointments from another hospital."
                )

        elif is_doctor:
            doctor_id = current_user.doctor_profile.id if (hasattr(current_user, "doctor_profile") and current_user.doctor_profile) else None
            if appointment.doctor_id != doctor_id and appointment.doctor.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Doctor Authorization Violation: You can only release appointments scheduled with yourself."
                )

        elif is_patient:
            patient_id = current_user.patient_profile.id if (hasattr(current_user, "patient_profile") and current_user.patient_profile) else None
            patient_owner = (
                appointment.patient_id == patient_id or
                (appointment.patient and appointment.patient.user_id == current_user.id)
            )
            if not patient_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patient Authorization Violation: You cannot release another patient's reservation."
                )

        elif not is_platform_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Unauthorized to release reservation.")

        # Update appointment status to CANCELLED
        appointment.status = "CANCELLED"
        db.flush()

        slot_status = "NONE"
        if appointment.slot_id:
            with slot_lock_manager.lock(appointment.slot_id):
                slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == appointment.slot_id).first()
                if slot:
                    # Re-verify if slot is still within valid doctor working hours and not blocked
                    val = SchedulingService.validate_slot(
                        db=db,
                        doctor_id=appointment.doctor_id,
                        start_time=slot.start_time,
                        end_time=slot.end_time,
                        appointment_type=appointment.appointment_type or "IN_PERSON",
                        exclude_appointment_id=appointment.id,
                        ignore_slot_id=appointment.slot_id
                    )
                    if val.is_bookable:
                        slot.status = "AVAILABLE"
                        slot.hold_expires_at = None
                        slot_status = "AVAILABLE"
                    else:
                        slot.status = "BLOCKED"
                        slot.hold_expires_at = None
                        slot_status = "BLOCKED"

                slot_lock_manager.release_hold_sync(appointment.slot_id)

        db.commit()

        return schemas.ReservationReleaseResponse(
            success=True,
            message="Reservation successfully released.",
            appointment_id=appointment.id,
            slot_id=appointment.slot_id,
            slot_status=slot_status
        )
