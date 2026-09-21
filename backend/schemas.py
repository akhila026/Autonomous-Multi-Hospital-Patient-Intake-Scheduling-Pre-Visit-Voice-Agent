from datetime import datetime
from typing import List, Optional, Any, Dict
import re
from pydantic import BaseModel, Field, field_validator

# --- Authentication & User Schemas ---
class LoginRequest(BaseModel):
    email: str = Field(..., example="alice.morgan@example.com")
    password: str = Field(..., example="Password123!")

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    full_name: str
    hospital_id: Optional[str] = None
    doctor_id: Optional[str] = None
    patient_id: Optional[str] = None

class UserRegisterRequest(BaseModel):
    email: str = Field(..., example="new.user@example.com")
    password: str = Field(..., min_length=6, example="Password123!")
    full_name: str = Field(..., example="Jane Doe")
    role: str = Field("PATIENT", example="PATIENT")  # PLATFORM_ADMIN, HOSPITAL_ADMIN, DOCTOR, PATIENT
    phone: Optional[str] = Field(None, example="+1-555-019-9988")
    hospital_id: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    emergency_contact: Optional[str] = None
    communication_preference: Optional[str] = "SMS"
    external_patient_id: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    phone: Optional[str] = None
    hospital_id: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

# --- Hospital Schemas ---
class HospitalRegisterRequest(BaseModel):
    name: str = Field(..., example="St. Jude Memorial Hospital")
    address: str = Field(..., example="450 Healthcare Blvd, Metro City")
    license_number: str = Field(..., example="MED-LIC-99821")
    contact_email: str = Field(..., example="admin@stjudehealth.org")
    phone: str = Field(..., example="+1-555-019-2834")
    departments: Optional[List[str]] = Field(default_factory=list, example=["Emergency", "Cardiology", "Orthopedics"])
    specialties: Optional[List[str]] = Field(default_factory=list, example=["Orthopedics", "Cardiology", "General Medicine"])
    services: Optional[List[str]] = Field(default_factory=list, example=["Emergency Care", "Inpatient Surgery", "Outpatient Consultation", "Telehealth"])
    operating_hours: Optional[Dict[str, str]] = Field(default_factory=lambda: {"Monday-Friday": "08:00-18:00", "Saturday": "09:00-13:00"})
    admin_name: Optional[str] = Field(None, example="David Miller")
    admin_email: Optional[str] = Field(None, example="david.miller@stjudehealth.org")
    admin_phone: Optional[str] = Field(None, example="+1-555-019-2800")
    admin_role: Optional[str] = Field("Hospital Administrator", example="Hospital Administrator")
    admin_password: Optional[str] = Field(None, min_length=6, example="AdminPass123!")
    supported_healthcare_systems: Optional[List[str]] = Field(default_factory=lambda: ["MOCK_EHR", "EPIC"])
    integration_config: Optional[Dict[str, Any]] = Field(default_factory=lambda: {"environment": "sandbox", "endpoint": "https://api.mockehr.org", "timeout_ms": 3000})
    status: Optional[str] = "DRAFT"  # DRAFT or SUBMITTED

class HospitalUpdateRequest(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    services: Optional[List[str]] = None
    operating_hours: Optional[Dict[str, str]] = None
    supported_healthcare_systems: Optional[List[str]] = None
    integration_config: Optional[Dict[str, Any]] = None
    communication_preferences: Optional[Dict[str, Any]] = None

class HospitalApprovalRequest(BaseModel):
    status: str = Field(..., example="APPROVED")  # APPROVED, REJECTED, SUSPENDED, REACTIVATED, UNDER_REVIEW, DRAFT
    rejection_reason: Optional[str] = None
    correction_notes: Optional[str] = None

class HospitalCorrectionRequest(BaseModel):
    correction_notes: str = Field(..., example="Please upload valid state medical board certification document.")

class HospitalSuspensionRequest(BaseModel):
    reason: str = Field(..., example="Annual license renewal pending review.")

class HospitalResponse(BaseModel):
    id: str
    name: str
    address: str
    license_number: str
    contact_email: str
    phone: str
    status: str
    rejection_reason: Optional[str] = None
    correction_notes: Optional[str] = None
    services: Optional[List[str]] = None
    operating_hours: Optional[Dict[str, str]] = None
    supported_healthcare_systems: Optional[List[str]] = None
    integration_config: Optional[Dict[str, Any]] = None
    communication_preferences: Optional[Dict[str, Any]] = None
    lifecycle_history: Optional[List[Dict[str, Any]]] = None
    created_at: datetime

    class Config:
        from_attributes = True

class HospitalActivityResponse(BaseModel):
    hospital_id: str
    hospital_name: str
    status: str
    total_doctors: int
    active_doctors: int
    total_departments: int
    total_appointments: int
    confirmed_appointments: int
    sync_attempts: int
    sync_timeouts: int
    recent_audit_events: List[Dict[str, Any]] = []

# --- Department & Calendar Schemas ---
class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., example="Orthopedic Surgery")
    code: Optional[str] = Field(None, example="ORTHO-SURG")
    description: Optional[str] = Field(None, example="Specialized surgical procedures and joint reconstruction")

class DepartmentUpdateRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

class DepartmentResponse(BaseModel):
    id: str
    hospital_id: str
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class CalendarCreateRequest(BaseModel):
    doctor_id: str
    name: Optional[str] = "Primary Clinical Calendar"
    timezone: Optional[str] = "UTC"

class CalendarResponse(BaseModel):
    id: str
    hospital_id: str
    doctor_id: str
    name: str
    timezone: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class BlockedSlotCreateRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = "Doctor Unavailable / Blocked Period"

class BlockedSlotResponse(BaseModel):
    id: str
    hospital_id: str
    doctor_id: str
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

# --- Doctor Schemas ---
class DoctorCreateRequest(BaseModel):
    full_name: str = Field(..., example="Dr. Sarah Jenkins, MD")
    specialty: str = Field(..., example="Orthopedics")
    department_id: Optional[str] = None
    specialty_id: Optional[str] = None
    photo_url: Optional[str] = Field(None, example="https://images.unsplash.com/photo-1559839734-2b71ea197ec2")
    qualifications: Optional[str] = Field(None, example="MD, FACS, Board Certified Orthopedic Surgeon")
    experience_years: Optional[int] = Field(12, example=12)
    languages: Optional[str] = Field("English, Spanish", example="English, Spanish")
    bio: Optional[str] = Field(None, example="Specialist in sports medicine and joint care with 12+ years experience.")
    consultation_fee: float = Field(150.0, example=175.0)
    slot_duration_min: int = Field(30, example=30)
    supported_appointment_types: Optional[str] = Field("IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT", example="IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT")
    external_provider_id: Optional[str] = Field(None, example="NPI-99482104")
    status: Optional[str] = Field("ACTIVE", example="ACTIVE")  # INVITED, ACTIVE, INACTIVE, SUSPENDED

class DoctorUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    specialty: Optional[str] = None
    department_id: Optional[str] = None
    specialty_id: Optional[str] = None
    photo_url: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    languages: Optional[str] = None
    bio: Optional[str] = None
    consultation_fee: Optional[float] = None
    slot_duration_min: Optional[int] = None
    supported_appointment_types: Optional[str] = None
    external_provider_id: Optional[str] = None
    status: Optional[str] = None  # INVITED, ACTIVE, INACTIVE, SUSPENDED
    is_active: Optional[bool] = None

class DoctorStatusUpdateRequest(BaseModel):
    status: str = Field(..., example="ACTIVE")  # INVITED, ACTIVE, INACTIVE, SUSPENDED

class AvailabilityScheduleItem(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, example=1)  # 0=Monday
    start_time: str = Field(..., example="09:00")
    end_time: str = Field(..., example="17:00")
    is_active: bool = True

class AvailabilityScheduleSetRequest(BaseModel):
    schedules: List[AvailabilityScheduleItem]

class SlotGenerateRequest(BaseModel):
    days_ahead: int = Field(7, ge=1, le=30, example=7)

class TimeSlotResponse(BaseModel):
    id: str
    doctor_id: str
    start_time: datetime
    end_time: datetime
    status: str
    hold_expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DoctorResponse(BaseModel):
    id: str
    hospital_id: str
    hospital_name: Optional[str] = None
    department_id: Optional[str] = None
    full_name: str
    specialty: str
    photo_url: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = 5
    languages: Optional[str] = "English"
    bio: Optional[str] = None
    consultation_fee: float
    slot_duration_min: int
    supported_appointment_types: Optional[str] = "IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT"
    external_provider_id: Optional[str] = None
    status: str = "ACTIVE"
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

# --- Patient Schemas ---
class PatientRegisterRequest(BaseModel):
    full_name: str = Field(..., example="Alice Morgan")
    phone: str = Field(..., example="+1-555-832-1920")
    email: str = Field(..., example="alice.morgan@example.com")
    password: Optional[str] = Field(None, min_length=6, example="Password123!")
    date_of_birth: Optional[str] = Field(None, example="1992-04-15")
    gender: Optional[str] = Field(None, example="Female")
    emergency_contact: Optional[str] = Field(None, example="Bob Morgan (+1-555-832-1921)")
    communication_preference: Optional[str] = Field("SMS", example="SMS")
    preferences: Optional[Dict[str, Any]] = None
    external_patient_id: Optional[str] = None
    hospital_id: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned):
            raise ValueError("Invalid email format. Please provide a valid email address (e.g. user@example.com).")
        return cleaned

class PatientResponse(BaseModel):
    id: str
    full_name: str
    phone: str
    email: str
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    emergency_contact: Optional[str] = None
    communication_preference: Optional[str] = "SMS"
    preferences: Optional[Dict[str, Any]] = None
    external_patient_id: Optional[str] = None
    patient_mrn: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class PatientUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, example="Alice Morgan")
    phone: Optional[str] = Field(None, example="+1-555-832-1920")
    email: Optional[str] = Field(None, example="alice.morgan@example.com")
    date_of_birth: Optional[str] = Field(None, example="1992-04-15")
    gender: Optional[str] = Field(None, example="Female")
    emergency_contact: Optional[str] = Field(None, example="Bob Morgan (+1-555-832-1921)")
    communication_preference: Optional[str] = Field(None, example="SMS")
    preferences: Optional[Dict[str, Any]] = None
    external_patient_id: Optional[str] = None

# --- AI & Voice Schemas ---
class AITriageMessage(BaseModel):
    role: str  # user, assistant, system
    content: str

class AITriageChatRequest(BaseModel):
    message: str = Field(..., example="I tripped while running yesterday and my right knee is swollen and hurts to walk.")
    history: Optional[List[AITriageMessage]] = []
    patient_id: Optional[str] = None

class AITriageChatResponse(BaseModel):
    reply: str
    specialty_recommended: Optional[str] = None
    urgency_level: str = "ROUTINE"  # ROUTINE, URGENT, EMERGENCY
    is_emergency: bool = False
    chief_complaint: Optional[str] = None
    suggested_doctors: Optional[List[DoctorResponse]] = []
    available_slots: Optional[List[TimeSlotResponse]] = []

# --- Appointment Schemas ---
class SlotHoldRequest(BaseModel):
    slot_id: str
    patient_id: str
    chief_complaint: Optional[str] = None
    urgency_level: str = "ROUTINE"

class SlotValidationRequest(BaseModel):
    slot_id: Optional[str] = None
    doctor_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    appointment_type: str = "IN_PERSON"

class SlotValidationResponse(BaseModel):
    is_bookable: bool
    reason: Optional[str] = None
    doctor_id: str
    doctor_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    appointment_type: str = "IN_PERSON"

class ReservationCreateRequest(BaseModel):
    slot_id: str
    patient_id: str
    doctor_id: str
    hospital_id: str
    appointment_type: str = "IN_PERSON"
    chief_complaint: Optional[str] = None
    urgency_level: str = "ROUTINE"
    idempotency_key: Optional[str] = None

class ReservationReleaseResponse(BaseModel):
    success: bool
    message: str
    appointment_id: str
    slot_id: Optional[str] = None
    slot_status: str

class AppointmentConfirmRequest(BaseModel):
    slot_id: str
    patient_id: Optional[str] = None
    chief_complaint: Optional[str] = None
    urgency_level: str = "ROUTINE"
    appointment_type: Optional[str] = "IN_PERSON"
    ai_triage_notes: Optional[str] = None
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None

class AppointmentRescheduleRequest(BaseModel):
    new_slot_id: str = Field(..., description="Target new TimeSlot UUID")
    reason: Optional[str] = Field(None, description="Reason for rescheduling")

class AppointmentCancelRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Reason for cancellation")

class AppointmentStatusUpdateRequest(BaseModel):
    status: str = Field(..., example="COMPLETED", description="REQUESTED, PENDING, CONFIRMED, RESCHEDULED, CANCELLED, COMPLETED, NO_SHOW, FAILED, SYNC_PENDING, RECONCILIATION_REQUIRED")
    notes: Optional[str] = None

class AppointmentReconcileRequest(BaseModel):
    external_status: Optional[str] = Field(None, example="CONFIRMED")
    resolution_notes: Optional[str] = Field(None, example="Manual reconciliation verified with EHR records.")

class EhrSyncLogResponse(BaseModel):
    id: str
    action: str
    status: str
    request_payload: Optional[str] = None
    response_payload: Optional[str] = None
    response_time_ms: int
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class AppointmentResponse(BaseModel):
    id: str
    patient_id: str
    patient_name: Optional[str] = None
    doctor_id: str
    doctor_name: Optional[str] = None
    doctor_specialty: Optional[str] = None
    hospital_id: str
    hospital_name: Optional[str] = None
    slot_id: Optional[str] = None
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    status: str
    ehr_appointment_id: Optional[str] = None
    idempotency_key: str
    retry_count: int
    chief_complaint: Optional[str] = None
    urgency_level: str
    ai_triage_notes: Optional[str] = None
    cancellation_reason: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    sync_logs: List[EhrSyncLogResponse] = []

    class Config:
        from_attributes = True

# --- Mock EHR Schemas ---
class MockEhrAppointmentRequest(BaseModel):
    idempotency_key: str
    patient_name: str
    doctor_name: str
    hospital_name: str
    slot_time: str
    chief_complaint: Optional[str] = None

class MockEhrAppointmentResponse(BaseModel):
    ehr_appointment_id: str
    idempotency_key: str
    status: str
    message: str
    created_at: datetime

class MockEhrVerifyResponse(BaseModel):
    found: bool
    ehr_appointment_id: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None

class ChaosConfigUpdate(BaseModel):
    mode: str = Field(..., example="TIMEOUT_AFTER_SAVE")
    simulated_delay_ms: int = Field(3200, example=3200)
    failure_active: bool = True
    one_shot: bool = False

class TimeoutRecoveryDemoRequest(BaseModel):
    scenario: str = Field("TIMEOUT_AFTER_SAVE", description="TIMEOUT_AFTER_SAVE, TIMEOUT_BEFORE_SAVE, or UNRESOLVED_OUTAGE")
    simulated_delay_ms: int = Field(150, description="Chaos latency in ms")

class TimeoutRecoveryDemoResponse(BaseModel):
    scenario: str
    classification: str
    appointment_id: str
    external_appointment_id: Optional[str] = None
    idempotency_key: str
    final_appointment_status: str
    final_slot_status: str
    recovery_action_taken: str
    external_duplicate_count: int
    duplicate_prevented: bool
    timeline: List[Dict[str, Any]]
    audit_events: List[Dict[str, Any]]
    reconciliation_record: Optional[Dict[str, Any]] = None
    message: str

# --- Pre-Visit Questionnaire Schemas ---
SUPPORTED_QUESTION_TYPES = [
    "yes_no",
    "choice",
    "multiple_choice",
    "numeric",
    "date",
    "short_text",
    "long_text",
    "structured_fields"
]

class QuestionItemSchema(BaseModel):
    id: str
    question: str
    type: str  # yes_no, choice, multiple_choice, numeric, date, short_text, long_text, structured_fields (also text, scale)
    options: Optional[List[str]] = None
    required: bool = True
    placeholder: Optional[str] = None
    min: Optional[int] = None
    max: Optional[int] = None
    fields: Optional[List[Dict[str, Any]]] = None  # for structured_fields
    urgency_rules: Optional[Dict[str, Any]] = None  # e.g. {"trigger_values": ["yes"], "min_threshold": 8, "reason": "Severe pain"}

class QuestionnaireTemplateCreate(BaseModel):
    hospital_id: str
    specialty_id: Optional[str] = None
    doctor_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    appointment_type: Optional[str] = "ALL"  # IN_PERSON, VIDEO_CONSULT, ALL
    condition_category: Optional[str] = "GENERAL_INTAKE"
    questions: List[Dict[str, Any]]
    is_approved: Optional[bool] = True
    approved_by: Optional[str] = None

class QuestionnaireTemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    appointment_type: Optional[str] = None
    condition_category: Optional[str] = None
    questions: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None
    is_approved: Optional[bool] = None
    approved_by: Optional[str] = None

class QuestionnaireTemplateResponse(BaseModel):
    id: str
    hospital_id: str
    specialty_id: Optional[str] = None
    doctor_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    appointment_type: Optional[str] = "ALL"
    condition_category: Optional[str] = "GENERAL_INTAKE"
    questions: List[Dict[str, Any]]
    is_active: bool
    is_approved: bool
    approved_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class QuestionnaireSubmitRequest(BaseModel):
    answers: Dict[str, Any]

class QuestionnaireConversationalTurnRequest(BaseModel):
    appointment_id: str
    patient_utterance: str

class QuestionnaireConversationalTurnResponse(BaseModel):
    appointment_id: str
    extracted_answers: Dict[str, Any]
    all_answers: Dict[str, Any]
    missing_questions: List[Dict[str, Any]]
    next_question: Optional[Dict[str, Any]] = None
    ai_prompt_message: str
    is_complete: bool
    is_urgent: bool = False
    urgent_reasons: List[str] = []

class DoctorReviewRequest(BaseModel):
    doctor_notes: Optional[str] = None

class DoctorReviewResponse(BaseModel):
    id: str
    appointment_id: str
    patient_id: str
    doctor_id: str
    reviewed_at: datetime
    status: str
    doctor_notes: Optional[str] = None

class QuestionnaireResponse(BaseModel):
    id: str
    appointment_id: str
    patient_id: str
    questionnaire_id: Optional[str] = None
    questions: List[Dict[str, Any]]
    answers: Optional[Dict[str, Any]] = None
    patient_intake_summary: Optional[str] = None
    ai_clinical_summary: Optional[str] = None
    status: str
    is_urgent: bool = False
    urgent_reasons: Optional[List[str]] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    doctor_notes: Optional[str] = None
    submitted_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- Reminder Schemas ---
class ReminderLogResponse(BaseModel):
    id: str
    appointment_id: str
    reminder_type: str
    scheduled_for: datetime
    status: str
    channel: str
    message_content: Optional[str] = None
    sent_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- Extended Entity Schemas (PRD Core Architecture) ---
class DepartmentResponse(BaseModel):
    id: str
    hospital_id: str
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True

class SpecialtyResponse(BaseModel):
    id: str
    name: str
    code: Optional[str] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True

class CalendarResponse(BaseModel):
    id: str
    hospital_id: str
    doctor_id: str
    name: str
    timezone: str
    is_active: bool

    class Config:
        from_attributes = True

class BlockedSlotResponse(BaseModel):
    id: str
    hospital_id: str
    doctor_id: str
    calendar_id: Optional[str] = None
    start_time: datetime
    end_time: datetime
    reason: str

    class Config:
        from_attributes = True

class AIConversationResponse(BaseModel):
    id: str
    session_id: str
    patient_id: Optional[str] = None
    hospital_id: Optional[str] = None
    channel: str
    status: str
    transcript: List[Dict[str, Any]] = []
    created_at: datetime

    class Config:
        from_attributes = True

class AIContextResponse(BaseModel):
    id: str
    conversation_id: str
    current_intent: Optional[str] = None
    selected_doctor_id: Optional[str] = None
    selected_slot_id: Optional[str] = None
    urgency_level: str
    updated_at: datetime

    class Config:
        from_attributes = True

class IntegrationOperationResponse(BaseModel):
    id: str
    hospital_id: str
    appointment_id: Optional[str] = None
    system_name: str
    operation_type: str
    idempotency_key: str
    status: str
    response_time_ms: int
    created_at: datetime

    class Config:
        from_attributes = True

class ExternalIdentifierMappingResponse(BaseModel):
    id: str
    hospital_id: str
    system_name: str
    entity_type: str
    internal_id: str
    external_id: str

    class Config:
        from_attributes = True

class VerificationResponse(BaseModel):
    id: str
    hospital_id: str
    appointment_id: str
    idempotency_key: str
    external_appointment_id: Optional[str] = None
    status: str
    verified_at: datetime

    class Config:
        from_attributes = True

class ReconciliationRecordResponse(BaseModel):
    id: str
    hospital_id: str
    appointment_id: Optional[str] = None
    idempotency_key: str
    failure_reason: str
    status: str
    resolution_notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class WorkflowResponse(BaseModel):
    id: str
    hospital_id: str
    appointment_id: Optional[str] = None
    workflow_type: str
    status: str
    current_step: int
    created_at: datetime

    class Config:
        from_attributes = True

class NotificationResponse(BaseModel):
    id: str
    hospital_id: Optional[str] = None
    appointment_id: Optional[str] = None
    recipient_type: str
    recipient_contact: str
    channel: str
    template: str
    scheduled_for: datetime
    sent_at: Optional[datetime] = None
    status: str

    class Config:
        from_attributes = True

class AuditEventResponse(BaseModel):
    id: str
    correlation_id: Optional[str] = None
    hospital_id: Optional[str] = None
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

