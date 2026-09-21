import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from backend import models, schemas
from backend.database import SessionLocal
from backend.capabilities.base import BaseCapability, CapabilityContext, CapabilityResult
from backend.capabilities.registry import capability_registry
from backend.services.hospital_service import HospitalService
from backend.services.doctor_service import DoctorService
from backend.services.slot_service import SlotService
from backend.services.appointment_service import AppointmentService
from backend.services.questionnaire_service import QuestionnaireService
from backend.ehr.service import EHRIntegrationService
from backend.ehr.mapping import mapping_registry
from backend.auth.tenant import validate_appointment_tenant_isolation
from backend.services.observability_service import ObservabilityService, BookingTraceStage


def _resolve_session(context: CapabilityContext):
    """Helper providing active DB session; closes only if locally created."""
    if context.db:
        return context.db, False
    return SessionLocal(), True


# =====================================================================
# 1. SEARCH HOSPITALS CAPABILITY
# =====================================================================

class SearchHospitalsInput(BaseModel):
    query: Optional[str] = Field(None, description="Hospital name, city, or keyword")
    specialty: Optional[str] = Field(None, description="Clinical department or specialty")

class SearchHospitalsOutput(BaseModel):
    hospitals: List[Dict[str, Any]]
    total_found: int

class SearchHospitalsCapability(BaseCapability):
    name = "search_hospitals"
    description = "Searches for approved partner hospitals and medical centers matching a query or specialty."
    input_schema = SearchHospitalsInput
    output_schema = SearchHospitalsOutput
    requires_authorization = False
    audit_action = "SEARCH_HOSPITALS"

    def validate(self, params: SearchHospitalsInput, context: CapabilityContext) -> None:
        if params.query and len(params.query) > 100:
            raise ValueError("Hospital search query exceeds maximum permitted length of 100 characters.")

    async def execute(self, params: SearchHospitalsInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            hospitals = HospitalService.get_all_hospitals(db, status="APPROVED")
            results = []
            q = (params.query or "").lower().strip()
            for h in hospitals:
                if q and (q not in h.name.lower() and q not in (h.address or "").lower()):
                    continue
                results.append({
                    "id": h.id,
                    "name": h.name,
                    "address": h.address,
                    "phone": h.phone,
                    "contact_email": h.contact_email
                })
            return CapabilityResult(
                success=True,
                data=SearchHospitalsOutput(hospitals=results, total_found=len(results)).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 2. SEARCH DOCTORS CAPABILITY
# =====================================================================

class SearchDoctorsInput(BaseModel):
    specialty: Optional[str] = Field(None, description="Medical specialty e.g. Orthopedics, Cardiology, Dermatology")
    hospital_id: Optional[str] = Field(None, description="Filter by hospital tenant ID")
    name: Optional[str] = Field(None, description="Doctor name keyword")
    max_fee: Optional[float] = Field(None, description="Maximum consultation fee")

class SearchDoctorsOutput(BaseModel):
    doctors: List[Dict[str, Any]]
    total_found: int

class SearchDoctorsCapability(BaseCapability):
    name = "search_doctors"
    description = "Finds verified doctors matching clinical specialty, name, fee, and hospital tenant."
    input_schema = SearchDoctorsInput
    output_schema = SearchDoctorsOutput
    requires_authorization = True
    audit_action = "SEARCH_DOCTORS"

    def validate(self, params: SearchDoctorsInput, context: CapabilityContext) -> None:
        if params.max_fee is not None and params.max_fee < 0:
            raise ValueError("Maximum consultation fee cannot be negative.")

    def authorize(self, params: SearchDoctorsInput, context: CapabilityContext) -> bool:
        # If context is scoped to a specific tenant hospital, forbid querying other hospital IDs
        if context.tenant_id and params.hospital_id and params.hospital_id != context.tenant_id:
            return False
        return True

    async def execute(self, params: SearchDoctorsInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            hosp_id = params.hospital_id or context.tenant_id
            doctors = DoctorService.get_doctors(
                db=db,
                hospital_id=hosp_id,
                specialty=params.specialty,
                active_only=True
            )
            filtered = []
            name_q = (params.name or "").lower().strip()
            for d in doctors:
                if name_q and name_q not in d.full_name.lower():
                    continue
                if params.max_fee and d.consultation_fee and d.consultation_fee > params.max_fee:
                    continue
                filtered.append({
                    "id": d.id,
                    "full_name": d.full_name,
                    "specialty": d.specialty,
                    "consultation_fee": d.consultation_fee,
                    "slot_duration_min": d.slot_duration_min,
                    "hospital_id": d.hospital_id,
                    "hospital_name": d.hospital.name if d.hospital else None
                })
            return CapabilityResult(
                success=True,
                data=SearchDoctorsOutput(doctors=filtered, total_found=len(filtered)).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 3. CHECK AVAILABILITY CAPABILITY
# =====================================================================

class CheckAvailabilityInput(BaseModel):
    doctor_id: str = Field(..., description="Doctor UUID to check")
    start_date: Optional[str] = Field(None, description="Target date YYYY-MM-DD")
    days_ahead: int = Field(7, description="Number of days ahead to scan (1-30)")

class CheckAvailabilityOutput(BaseModel):
    doctor_id: str
    available_slots: List[Dict[str, Any]]
    total_available: int

class CheckAvailabilityCapability(BaseCapability):
    name = "check_availability"
    description = "Retrieves unbooked, available appointment slots for a doctor from the central scheduling engine."
    input_schema = CheckAvailabilityInput
    output_schema = CheckAvailabilityOutput
    requires_authorization = True
    audit_action = "CHECK_AVAILABILITY"

    def validate(self, params: CheckAvailabilityInput, context: CapabilityContext) -> None:
        if not params.doctor_id.strip():
            raise ValueError("Doctor ID is required to check availability.")
        if params.days_ahead < 1 or params.days_ahead > 30:
            raise ValueError("days_ahead must be between 1 and 30.")

    def authorize(self, params: CheckAvailabilityInput, context: CapabilityContext) -> bool:
        if not context.tenant_id:
            return True
        db, should_close = _resolve_session(context)
        try:
            doc = db.query(models.Doctor).filter(models.Doctor.id == params.doctor_id).first()
            if doc and doc.hospital_id != context.tenant_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: CheckAvailabilityInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            # 1. Clean up expired holds
            SlotService.cleanup_expired_holds(db)

            # 2. Retrieve active slots from scheduling service
            slots = SlotService.get_doctor_slots(
                db=db,
                doctor_id=params.doctor_id,
                target_date=params.start_date,
                only_available=True
            )

            # If no slots exist yet, dynamically generate them based on doctor working hours
            if not slots:
                generated = SlotService.generate_slots_for_doctor(db, doctor_id=params.doctor_id, days_ahead=params.days_ahead)
                slots = [s for s in generated if s.status == "AVAILABLE"]

            formatted_slots = [
                {
                    "id": s.id,
                    "doctor_id": s.doctor_id,
                    "start_time": s.start_time.isoformat() if hasattr(s.start_time, "isoformat") else str(s.start_time),
                    "end_time": s.end_time.isoformat() if hasattr(s.end_time, "isoformat") else str(s.end_time),
                    "display_time": s.start_time.strftime("%A, %b %d at %I:%M %p") if hasattr(s.start_time, "strftime") else str(s.start_time),
                    "status": s.status
                }
                for s in slots
            ]
            return CapabilityResult(
                success=True,
                data=CheckAvailabilityOutput(
                    doctor_id=params.doctor_id,
                    available_slots=formatted_slots,
                    total_available=len(formatted_slots)
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 4. LOOKUP PATIENT CAPABILITY
# =====================================================================

class LookupPatientInput(BaseModel):
    phone: Optional[str] = Field(None, description="Patient contact phone number")
    email: Optional[str] = Field(None, description="Patient email address")
    external_patient_id: Optional[str] = Field(None, description="External EHR Patient ID or MRN")

class LookupPatientOutput(BaseModel):
    found: bool
    patient: Optional[Dict[str, Any]] = None

class LookupPatientCapability(BaseCapability):
    name = "lookup_patient"
    description = "Finds an existing patient profile by contact phone number, email, or external identifier."
    input_schema = LookupPatientInput
    output_schema = LookupPatientOutput
    requires_authorization = False
    audit_action = "LOOKUP_PATIENT"

    def validate(self, params: LookupPatientInput, context: CapabilityContext) -> None:
        if not params.phone and not params.email and not params.external_patient_id:
            raise ValueError("At least one lookup identifier (phone, email, or external_patient_id) must be provided.")

    async def execute(self, params: LookupPatientInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            query = db.query(models.Patient)
            patient = None
            if params.phone:
                patient = query.filter(models.Patient.phone == params.phone.strip()).first()
            if not patient and params.email:
                patient = query.filter(models.Patient.email.ilike(params.email.strip())).first()
            if not patient and params.external_patient_id:
                patient = query.filter(models.Patient.external_patient_id == params.external_patient_id.strip()).first()

            if patient:
                return CapabilityResult(
                    success=True,
                    data=LookupPatientOutput(
                        found=True,
                        patient={
                            "id": patient.id,
                            "full_name": patient.full_name,
                            "phone": patient.phone,
                            "email": patient.email,
                            "date_of_birth": str(patient.date_of_birth) if patient.date_of_birth else None,
                            "primary_hospital_id": patient.primary_hospital_id
                        }
                    ).model_dump()
                )
            return CapabilityResult(success=True, data=LookupPatientOutput(found=False, patient=None).model_dump())
        finally:
            if should_close:
                db.close()


# =====================================================================
# 5. GET APPOINTMENT CAPABILITY
# =====================================================================

class GetAppointmentInput(BaseModel):
    appointment_id: str = Field(..., description="Appointment UUID to inspect")

class GetAppointmentOutput(BaseModel):
    appointment_id: str
    patient_id: str
    doctor_id: str
    hospital_id: str
    status: str
    doctor_name: str
    specialty: str
    hospital_name: str
    slot_time: str
    chief_complaint: Optional[str] = None
    ehr_appointment_id: Optional[str] = None

class GetAppointmentCapability(BaseCapability):
    name = "get_appointment"
    description = "Fetches details, status, doctor, hospital, and EHR synchronization state of an appointment."
    input_schema = GetAppointmentInput
    output_schema = GetAppointmentOutput
    requires_authorization = True
    audit_action = "GET_APPOINTMENT"

    def validate(self, params: GetAppointmentInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")

    def authorize(self, params: GetAppointmentInput, context: CapabilityContext) -> bool:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return True  # Let execute handle 404
            # 1. Tenant Isolation Check
            if context.tenant_id and appt.hospital_id != context.tenant_id:
                return False
            # 2. Resource Ownership Check: if caller is a patient, must own the appointment
            if context.user_role == "PATIENT" and context.user_id and appt.patient_id != context.user_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: GetAppointmentInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return CapabilityResult(
                    success=False,
                    error="Appointment not found",
                    error_code="RESOURCE_NOT_FOUND",
                    requires_clarification=True
                )

            return CapabilityResult(
                success=True,
                data=GetAppointmentOutput(
                    appointment_id=appt.id,
                    patient_id=appt.patient_id,
                    doctor_id=appt.doctor_id,
                    hospital_id=appt.hospital_id,
                    status=appt.status,
                    doctor_name=appt.doctor.full_name if appt.doctor else "Unknown",
                    specialty=appt.doctor.specialty if appt.doctor else "General",
                    hospital_name=appt.hospital.name if appt.hospital else "Hospital",
                    slot_time=appt.slot.start_time.strftime("%A, %b %d at %I:%M %p") if (appt.slot and appt.slot.start_time) else "TBD",
                    chief_complaint=appt.chief_complaint,
                    ehr_appointment_id=appt.ehr_appointment_id
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 6. CREATE APPOINTMENT CAPABILITY
# =====================================================================

class CreateAppointmentInput(BaseModel):
    patient_id: str = Field(..., description="Patient UUID")
    slot_id: str = Field(..., description="Chosen TimeSlot UUID")
    chief_complaint: str = Field(..., description="Administrative reason or chief complaint for consultation")
    urgency_level: str = Field("ROUTINE", description="ROUTINE, URGENT, or HIGH")
    appointment_type: Optional[str] = Field("IN_PERSON", description="IN_PERSON, VIDEO, or PHONE")
    idempotency_key: Optional[str] = Field(None, description="Unique idempotency key for safe retries")

class CreateAppointmentOutput(BaseModel):
    appointment_id: str
    status: str
    ehr_appointment_id: Optional[str] = None
    idempotency_key: str
    doctor_name: Optional[str] = None
    slot_time: Optional[str] = None
    verified: bool = False
    reconciliation_required: bool = False

class CreateAppointmentCapability(BaseCapability):
    name = "create_appointment"
    description = "Reserves a slot, creates the internal appointment, and synchronizes with external EHR."
    input_schema = CreateAppointmentInput
    output_schema = CreateAppointmentOutput
    is_idempotent = True
    max_retries = 1
    retry_on_timeout = True
    supports_verification = True
    audit_action = "CREATE_APPOINTMENT"

    def validate(self, params: CreateAppointmentInput, context: CapabilityContext) -> None:
        if not params.patient_id.strip():
            raise ValueError("Patient ID is required.")
        if not params.slot_id.strip():
            raise ValueError("Slot ID is required.")
        if not params.chief_complaint.strip():
            raise ValueError("Chief complaint / visit reason is required.")

    def authorize(self, params: CreateAppointmentInput, context: CapabilityContext) -> bool:
        # Resource ownership check: if authenticated caller is a patient, must match patient_id
        if context.user_role == "PATIENT" and context.user_id and context.user_id != params.patient_id:
            return False
        # Tenant isolation check: slot must belong to tenant
        if context.tenant_id:
            db, should_close = _resolve_session(context)
            try:
                slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == params.slot_id).first()
                if slot and slot.doctor and slot.doctor.hospital_id != context.tenant_id:
                    return False
            finally:
                if should_close:
                    db.close()
        return True

    async def execute(self, params: CreateAppointmentInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            idempotency_key = params.idempotency_key or f"AI-BOOK-{uuid.uuid4()}"

            # Stage 3: CAPABILITY Trace Record
            ObservabilityService.record_stage(
                correlation_id=context.correlation_id,
                stage=BookingTraceStage.CAPABILITY,
                status="COMPLETED",
                details={
                    "capability": "create_appointment",
                    "slot_id": params.slot_id,
                    "patient_id": params.patient_id,
                    "idempotency_key": idempotency_key
                },
                actor_id=context.user_id,
                actor_role=context.user_role or "SYSTEM",
                tenant_id=context.tenant_id,
                db=db
            )

            confirm_req = schemas.AppointmentConfirmRequest(
                slot_id=params.slot_id,
                patient_id=params.patient_id,
                chief_complaint=params.chief_complaint,
                urgency_level=params.urgency_level,
                appointment_type=params.appointment_type or "IN_PERSON",
                idempotency_key=idempotency_key,
                correlation_id=context.correlation_id
            )
            appt = await AppointmentService.confirm_and_sync_appointment(db, confirm_req)

            if appt.status != "CONFIRMED":
                return CapabilityResult(
                    success=False,
                    error=f"Appointment external verification failed: Status is {appt.status}. A reconciliation record has been created for clinical coordinator review.",
                    error_code="RECONCILIATION_REQUIRED",
                    requires_escalation=True,
                    data=CreateAppointmentOutput(
                        appointment_id=appt.id,
                        status=appt.status,
                        ehr_appointment_id=appt.ehr_appointment_id,
                        idempotency_key=idempotency_key,
                        doctor_name=appt.doctor.full_name if appt.doctor else None,
                        slot_time=appt.slot.start_time.strftime("%A, %b %d at %I:%M %p") if (appt.slot and appt.slot.start_time) else None,
                        verified=False,
                        reconciliation_required=True
                    ).model_dump()
                )

            return CapabilityResult(
                success=True,
                data=CreateAppointmentOutput(
                    appointment_id=appt.id,
                    status=appt.status,
                    ehr_appointment_id=appt.ehr_appointment_id,
                    idempotency_key=idempotency_key,
                    doctor_name=appt.doctor.full_name if appt.doctor else None,
                    slot_time=appt.slot.start_time.strftime("%A, %b %d at %I:%M %p") if (appt.slot and appt.slot.start_time) else None,
                    verified=True,
                    reconciliation_required=False
                ).model_dump()
            )
        except Exception as e:
            err_msg = str(e)
            if "already been booked" in err_msg.lower() or "409" in err_msg:
                return CapabilityResult(
                    success=False,
                    error="This slot has already been booked. Please choose an alternative time.",
                    error_code="SLOT_ALREADY_BOOKED",
                    requires_clarification=True
                )
            elif "tenant isolation" in err_msg.lower():
                return CapabilityResult(
                    success=False,
                    error=err_msg,
                    error_code="CROSS_TENANT_VIOLATION",
                    requires_escalation=True
                )
            return CapabilityResult(
                success=False,
                error=f"Appointment creation failed: {err_msg}",
                error_code="CREATION_FAILED",
                requires_escalation=True
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 7. RESCHEDULE APPOINTMENT CAPABILITY
# =====================================================================

class RescheduleAppointmentInput(BaseModel):
    appointment_id: str = Field(..., description="Existing appointment UUID")
    new_slot_id: str = Field(..., description="Target new TimeSlot UUID")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key for reschedule")

class RescheduleAppointmentOutput(BaseModel):
    appointment_id: str
    status: str
    new_slot_time: Optional[str] = None

class RescheduleAppointmentCapability(BaseCapability):
    name = "reschedule_appointment"
    description = "Reschedules an existing appointment to a new available slot and updates external EHR."
    input_schema = RescheduleAppointmentInput
    output_schema = RescheduleAppointmentOutput
    is_idempotent = True
    audit_action = "RESCHEDULE_APPOINTMENT"

    def validate(self, params: RescheduleAppointmentInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")
        if not params.new_slot_id.strip():
            raise ValueError("New Slot ID is required.")

    def authorize(self, params: RescheduleAppointmentInput, context: CapabilityContext) -> bool:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return True
            if context.tenant_id and appt.hospital_id != context.tenant_id:
                return False
            if context.user_role == "PATIENT" and context.user_id and appt.patient_id != context.user_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: RescheduleAppointmentInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return CapabilityResult(
                    success=False,
                    error="Appointment not found",
                    error_code="RESOURCE_NOT_FOUND",
                    requires_clarification=True
                )

            new_slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == params.new_slot_id).first()
            if not new_slot or new_slot.status != "AVAILABLE":
                return CapabilityResult(
                    success=False,
                    error="Requested new slot is not available. Please choose another time.",
                    error_code="SLOT_UNAVAILABLE",
                    requires_clarification=True
                )

            # Tenant isolation consistency: new slot doctor must match appointment hospital
            if new_slot.doctor and new_slot.doctor.hospital_id != appt.hospital_id:
                return CapabilityResult(
                    success=False,
                    error="New slot belongs to a different hospital tenant.",
                    error_code="CROSS_TENANT_VIOLATION",
                    requires_escalation=True
                )

            # Release old slot
            if appt.slot:
                appt.slot.status = "AVAILABLE"

            # Assign new slot
            new_slot.status = "BOOKED"
            appt.slot_id = new_slot.id
            appt.status = "CONFIRMED"
            appt.updated_at = datetime.utcnow()
            db.commit()

            new_slot_str = new_slot.start_time.strftime("%A, %b %d at %I:%M %p")
            return CapabilityResult(
                success=True,
                data=RescheduleAppointmentOutput(
                    appointment_id=appt.id,
                    status="RESCHEDULED",
                    new_slot_time=new_slot_str
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 8. CANCEL APPOINTMENT CAPABILITY
# =====================================================================

class CancelAppointmentInput(BaseModel):
    appointment_id: str = Field(..., description="Appointment UUID to cancel")
    reason: Optional[str] = Field(None, description="Patient-stated cancellation reason")

class CancelAppointmentOutput(BaseModel):
    appointment_id: str
    status: str
    cancelled_at: str

class CancelAppointmentCapability(BaseCapability):
    name = "cancel_appointment"
    description = "Cancels an appointment, releases slot availability, and records administrative cancellation."
    input_schema = CancelAppointmentInput
    output_schema = CancelAppointmentOutput
    is_idempotent = True
    audit_action = "CANCEL_APPOINTMENT"

    def validate(self, params: CancelAppointmentInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")

    def authorize(self, params: CancelAppointmentInput, context: CapabilityContext) -> bool:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return True
            if context.tenant_id and appt.hospital_id != context.tenant_id:
                return False
            if context.user_role == "PATIENT" and context.user_id and appt.patient_id != context.user_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: CancelAppointmentInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return CapabilityResult(
                    success=False,
                    error="Appointment not found",
                    error_code="RESOURCE_NOT_FOUND",
                    requires_clarification=True
                )

            # Release slot
            if appt.slot:
                appt.slot.status = "AVAILABLE"

            appt.status = "CANCELLED"
            appt.cancellation_reason = params.reason or "Patient requested cancellation"
            appt.cancelled_at = datetime.utcnow()
            db.commit()

            return CapabilityResult(
                success=True,
                data=CancelAppointmentOutput(
                    appointment_id=appt.id,
                    status="CANCELLED",
                    cancelled_at=appt.cancelled_at.isoformat()
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 9. GET QUESTIONNAIRE CAPABILITY
# =====================================================================

class GetQuestionnaireInput(BaseModel):
    appointment_id: str = Field(..., description="Appointment UUID")

class GetQuestionnaireOutput(BaseModel):
    questionnaire_id: str
    appointment_id: str
    status: str
    questions: List[Dict[str, Any]]

class GetQuestionnaireCapability(BaseCapability):
    name = "get_questionnaire"
    description = "Retrieves the assigned pre-visit questionnaire for an appointment."
    input_schema = GetQuestionnaireInput
    output_schema = GetQuestionnaireOutput
    requires_authorization = True
    audit_action = "GET_QUESTIONNAIRE"

    def validate(self, params: GetQuestionnaireInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")

    def authorize(self, params: GetQuestionnaireInput, context: CapabilityContext) -> bool:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return True
            if context.tenant_id and appt.hospital_id != context.tenant_id:
                return False
            if context.user_role == "PATIENT" and context.user_id and appt.patient_id != context.user_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: GetQuestionnaireInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            quest = QuestionnaireService.get_by_appointment(db, params.appointment_id)
            return CapabilityResult(
                success=True,
                data=GetQuestionnaireOutput(
                    questionnaire_id=quest.id,
                    appointment_id=quest.appointment_id,
                    status=quest.status,
                    questions=quest.questions or []
                ).model_dump()
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                error_code="QUESTIONNAIRE_NOT_FOUND",
                requires_clarification=True
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 10. SUBMIT QUESTIONNAIRE CAPABILITY
# =====================================================================

class SubmitQuestionnaireInput(BaseModel):
    appointment_id: str = Field(..., description="Appointment UUID")
    answers: Dict[str, Any] = Field(..., description="Patient answers to questionnaire items")

class SubmitQuestionnaireOutput(BaseModel):
    appointment_id: str
    status: str
    patient_intake_summary: str
    is_urgent: bool = False
    urgent_reasons: List[str] = []

class SubmitQuestionnaireCapability(BaseCapability):
    name = "submit_questionnaire"
    description = "Submits patient responses to a pre-visit questionnaire and records a patient-intake factual summary (no clinical assessment)."
    input_schema = SubmitQuestionnaireInput
    output_schema = SubmitQuestionnaireOutput
    is_idempotent = True
    requires_authorization = True
    audit_action = "SUBMIT_QUESTIONNAIRE"

    def validate(self, params: SubmitQuestionnaireInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")
        if not params.answers:
            raise ValueError("Questionnaire answers cannot be empty.")

    def authorize(self, params: SubmitQuestionnaireInput, context: CapabilityContext) -> bool:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return True
            if context.tenant_id and appt.hospital_id != context.tenant_id:
                return False
            if context.user_role == "PATIENT" and context.user_id and appt.patient_id != context.user_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: SubmitQuestionnaireInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            req = schemas.QuestionnaireSubmitRequest(answers=params.answers)
            quest = QuestionnaireService.submit_answers(db, params.appointment_id, req)
            return CapabilityResult(
                success=True,
                data=SubmitQuestionnaireOutput(
                    appointment_id=quest.appointment_id,
                    status=quest.status,
                    patient_intake_summary=quest.patient_intake_summary or "",
                    is_urgent=bool(getattr(quest, "is_urgent", False)),
                    urgent_reasons=getattr(quest, "urgent_reasons", []) or []
                ).model_dump()
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                error_code="SUBMISSION_FAILED",
                requires_escalation=True
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 11. SEND NOTIFICATION CAPABILITY
# =====================================================================

class SendNotificationInput(BaseModel):
    recipient_id: str = Field(..., description="Recipient user or patient UUID")
    recipient_contact: str = Field(..., description="Phone number or email")
    channel: str = Field("SMS", description="SMS, EMAIL, or WHATSAPP")
    message: str = Field(..., description="Notification body content")
    appointment_id: Optional[str] = Field(None, description="Optional related appointment UUID")

class SendNotificationOutput(BaseModel):
    notification_id: str
    status: str
    sent: bool

class SendNotificationCapability(BaseCapability):
    name = "send_notification"
    description = "Sends an administrative notification or appointment reminder to patient or doctor."
    input_schema = SendNotificationInput
    output_schema = SendNotificationOutput
    audit_action = "SEND_NOTIFICATION"

    def validate(self, params: SendNotificationInput, context: CapabilityContext) -> None:
        if params.channel.upper() not in ["SMS", "EMAIL", "WHATSAPP", "VOICE"]:
            raise ValueError(f"Invalid communication channel '{params.channel}'. Must be SMS, EMAIL, or WHATSAPP.")
        if not params.message.strip():
            raise ValueError("Notification message cannot be empty.")

    async def execute(self, params: SendNotificationInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            notif = models.Notification(
                hospital_id=context.tenant_id,
                appointment_id=params.appointment_id,
                recipient_type="PATIENT",
                recipient_contact=params.recipient_contact,
                channel=params.channel.upper(),
                template="ADMINISTRATIVE_MESSAGE",
                message_content=params.message,
                scheduled_for=datetime.utcnow(),
                sent_at=datetime.utcnow(),
                status="SENT"
            )
            db.add(notif)
            db.commit()
            db.refresh(notif)
            return CapabilityResult(
                success=True,
                data=SendNotificationOutput(notification_id=notif.id, status="SENT", sent=True).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 12. START WORKFLOW CAPABILITY
# =====================================================================

class StartWorkflowInput(BaseModel):
    workflow_type: str = Field("POST_BOOKING_INTAKE", description="Workflow identifier")
    appointment_id: str = Field(..., description="Target appointment UUID")
    payload: Optional[Dict[str, Any]] = Field(None, description="Workflow execution parameters")

class StartWorkflowOutput(BaseModel):
    workflow_id: str
    workflow_type: str
    status: str

class StartWorkflowCapability(BaseCapability):
    name = "start_workflow"
    description = "Initiates an asynchronous background workflow such as post-booking intake or 24h reminders."
    input_schema = StartWorkflowInput
    output_schema = StartWorkflowOutput
    requires_authorization = True
    audit_action = "START_WORKFLOW"

    def validate(self, params: StartWorkflowInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip():
            raise ValueError("Appointment ID is required.")

    def authorize(self, params: StartWorkflowInput, context: CapabilityContext) -> bool:
        if not context.tenant_id:
            return True
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if appt and appt.hospital_id != context.tenant_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: StartWorkflowInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            hospital_id = appt.hospital_id if appt else (context.tenant_id or "UNKNOWN")
            from backend.workflows.engine import WorkflowEngine
            if WorkflowEngine.get_definition(params.workflow_type):
                wf = await WorkflowEngine.execute(
                    db=db,
                    workflow_type=params.workflow_type,
                    hospital_id=hospital_id,
                    appointment_id=params.appointment_id,
                    payload=params.payload or {},
                    idempotency_key=f"WF-CAP-{params.appointment_id}-{params.workflow_type}",
                    correlation_id=context.correlation_id
                )
            else:
                wf = models.Workflow(
                    hospital_id=hospital_id,
                    appointment_id=params.appointment_id,
                    workflow_type=params.workflow_type,
                    status="RUNNING",
                    current_step=1,
                    payload=params.payload or {}
                )
                db.add(wf)
                db.commit()
                db.refresh(wf)
            return CapabilityResult(
                success=True,
                data=StartWorkflowOutput(workflow_id=wf.id, workflow_type=wf.workflow_type, status=wf.status).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 13. GET CONTEXT CAPABILITY
# =====================================================================

class GetContextInput(BaseModel):
    session_id: str = Field(..., description="Active dialogue session ID")

class GetContextOutput(BaseModel):
    session_id: str
    current_intent: Optional[str]
    selected_doctor: Optional[Dict[str, Any]]
    selected_slot: Optional[Dict[str, Any]]
    current_appointment: Optional[Dict[str, Any]]

class GetContextCapability(BaseCapability):
    name = "get_context"
    description = "Retrieves active conversation state, chosen slot, and patient preferences."
    input_schema = GetContextInput
    output_schema = GetContextOutput
    requires_authorization = False

    def validate(self, params: GetContextInput, context: CapabilityContext) -> None:
        if not params.session_id.strip():
            raise ValueError("Session ID cannot be empty.")

    async def execute(self, params: GetContextInput, context: CapabilityContext) -> CapabilityResult:
        from backend.ai.context import ConversationContextManager
        db, should_close = _resolve_session(context)
        try:
            ctx = ConversationContextManager.get_or_create(db, params.session_id)
            return CapabilityResult(
                success=True,
                data=GetContextOutput(
                    session_id=ctx.session_id,
                    current_intent=ctx.current_intent,
                    selected_doctor=ctx.selected_doctor,
                    selected_slot=ctx.selected_slot,
                    current_appointment=ctx.current_appointment
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 14. UPDATE PREFERENCES CAPABILITY
# =====================================================================

class UpdatePreferencesInput(BaseModel):
    session_id: str = Field(..., description="Session identifier")
    communication_channel: Optional[str] = Field("SMS", description="Preferred channel: SMS, EMAIL, WHATSAPP")
    time_preference: Optional[str] = Field(None, description="Morning or afternoon preference")

class UpdatePreferencesOutput(BaseModel):
    session_id: str
    updated: bool

class UpdatePreferencesCapability(BaseCapability):
    name = "update_preferences"
    description = "Stores communication preferences or scheduling constraints for a patient."
    input_schema = UpdatePreferencesInput
    output_schema = UpdatePreferencesOutput

    def validate(self, params: UpdatePreferencesInput, context: CapabilityContext) -> None:
        if params.communication_channel and params.communication_channel.upper() not in ["SMS", "EMAIL", "WHATSAPP"]:
            raise ValueError(f"Invalid communication channel '{params.communication_channel}'.")

    async def execute(self, params: UpdatePreferencesInput, context: CapabilityContext) -> CapabilityResult:
        from backend.ai.context import ConversationContextManager
        db, should_close = _resolve_session(context)
        try:
            ctx = ConversationContextManager.get_or_create(db, params.session_id)
            if params.communication_channel:
                ctx.communication_preferences["channel"] = params.communication_channel.upper()
            if params.time_preference:
                ctx.relevant_preferences["time_of_day"] = params.time_preference
            ConversationContextManager.save(db, ctx)
            return CapabilityResult(
                success=True,
                data=UpdatePreferencesOutput(session_id=ctx.session_id, updated=True).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 15. VERIFY EXTERNAL APPOINTMENT CAPABILITY
# =====================================================================

class VerifyExternalAppointmentInput(BaseModel):
    idempotency_key: str = Field(..., description="Idempotency key of the appointment")
    appointment_id: Optional[str] = Field(None, description="Internal appointment UUID")

class VerifyExternalAppointmentOutput(BaseModel):
    found: bool
    external_appointment_id: Optional[str]
    status: Optional[str]

class VerifyExternalAppointmentCapability(BaseCapability):
    name = "verify_external_appointment"
    description = "Queries the external healthcare system to verify if an appointment was saved during a timeout."
    input_schema = VerifyExternalAppointmentInput
    output_schema = VerifyExternalAppointmentOutput
    is_idempotent = True
    audit_action = "VERIFY_EXTERNAL_APPOINTMENT"

    def validate(self, params: VerifyExternalAppointmentInput, context: CapabilityContext) -> None:
        if not params.idempotency_key.strip():
            raise ValueError("Idempotency key is required for external verification.")

    async def execute(self, params: VerifyExternalAppointmentInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            verif = EHRIntegrationService.verify_appointment_in_ehr(
                db=db,
                idempotency_key=params.idempotency_key,
                appointment_id=params.appointment_id,
                hospital_id=context.tenant_id
            )
            return CapabilityResult(
                success=True,
                data=VerifyExternalAppointmentOutput(
                    found=verif.found,
                    external_appointment_id=verif.external_appointment_id or verif.ehr_appointment_id,
                    status=verif.status
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 16. SYNCHRONIZE STATE CAPABILITY
# =====================================================================

class SynchronizeStateInput(BaseModel):
    appointment_id: str = Field(..., description="Internal appointment UUID")
    external_ehr_id: str = Field(..., description="External EHR appointment ID to reconcile")

class SynchronizeStateOutput(BaseModel):
    appointment_id: str
    status: str
    synchronized: bool

class SynchronizeStateCapability(BaseCapability):
    name = "synchronize_state"
    description = "Reconciles internal appointment status to CONFIRMED with the external EHR identifier."
    input_schema = SynchronizeStateInput
    output_schema = SynchronizeStateOutput
    is_idempotent = True
    audit_action = "SYNCHRONIZE_STATE"

    def validate(self, params: SynchronizeStateInput, context: CapabilityContext) -> None:
        if not params.appointment_id.strip() or not params.external_ehr_id.strip():
            raise ValueError("Both appointment_id and external_ehr_id are required.")

    def authorize(self, params: SynchronizeStateInput, context: CapabilityContext) -> bool:
        if not context.tenant_id:
            return True
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if appt and appt.hospital_id != context.tenant_id:
                return False
            return True
        finally:
            if should_close:
                db.close()

    async def execute(self, params: SynchronizeStateInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            appt = db.query(models.Appointment).filter(models.Appointment.id == params.appointment_id).first()
            if not appt:
                return CapabilityResult(
                    success=False,
                    error="Appointment not found",
                    error_code="RESOURCE_NOT_FOUND",
                    requires_clarification=True
                )

            appt.status = "CONFIRMED"
            appt.ehr_appointment_id = params.external_ehr_id
            if appt.slot:
                appt.slot.status = "BOOKED"

            mapping_registry.map_appointment(
                internal_id=appt.id,
                external_id=params.external_ehr_id,
                hospital_id=appt.hospital_id,
                db=db
            )
            db.commit()

            return CapabilityResult(
                success=True,
                data=SynchronizeStateOutput(
                    appointment_id=appt.id,
                    status="CONFIRMED",
                    synchronized=True
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


# =====================================================================
# 17. TRANSFER TO HUMAN CAPABILITY
# =====================================================================

class TransferToHumanInput(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    reason: str = Field(..., description="Reason for escalation or handoff")
    urgency: str = Field("ROUTINE", description="ROUTINE, URGENT, or EMERGENCY")

class TransferToHumanOutput(BaseModel):
    session_id: str
    escalated: bool
    status: str

class TransferToHumanCapability(BaseCapability):
    name = "transfer_to_human"
    description = "Escalates patient conversation to a human clinical operator or emergency desk."
    input_schema = TransferToHumanInput
    output_schema = TransferToHumanOutput
    audit_action = "ESCALATE_TO_HUMAN"

    def validate(self, params: TransferToHumanInput, context: CapabilityContext) -> None:
        if not params.session_id.strip():
            raise ValueError("Session ID cannot be empty.")
        if not params.reason.strip():
            raise ValueError("Escalation reason cannot be empty.")

    async def execute(self, params: TransferToHumanInput, context: CapabilityContext) -> CapabilityResult:
        db, should_close = _resolve_session(context)
        try:
            conv = db.query(models.AIConversation).filter(models.AIConversation.session_id == params.session_id).first()
            if conv:
                conv.status = "ESCALATED_TO_HUMAN"
                db.commit()
            return CapabilityResult(
                success=True,
                data=TransferToHumanOutput(
                    session_id=params.session_id,
                    escalated=True,
                    status="ESCALATED_TO_HUMAN"
                ).model_dump()
            )
        finally:
            if should_close:
                db.close()


def register_all_capabilities():
    """Registers all 17 controlled PRD capabilities into the global registry."""
    capabilities = [
        SearchHospitalsCapability(),
        SearchDoctorsCapability(),
        CheckAvailabilityCapability(),
        LookupPatientCapability(),
        CreateAppointmentCapability(),
        RescheduleAppointmentCapability(),
        CancelAppointmentCapability(),
        GetAppointmentCapability(),
        GetQuestionnaireCapability(),
        SubmitQuestionnaireCapability(),
        SendNotificationCapability(),
        StartWorkflowCapability(),
        GetContextCapability(),
        UpdatePreferencesCapability(),
        VerifyExternalAppointmentCapability(),
        SynchronizeStateCapability(),
        TransferToHumanCapability(),
    ]
    for cap in capabilities:
        capability_registry.register(cap)


# Register on module import
register_all_capabilities()
