import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from backend.database import Base

def generate_uuid() -> str:
    """Consistent primary key UUID generator across all entities."""
    return str(uuid.uuid4())

# =====================================================================
# 1. HOSPITAL & TENANT ENTITIES
# =====================================================================

class Hospital(Base):
    """
    Hospital Tenant Entity (PRD Section 5 & 22).
    Represents an onboarded hospital organization with isolated configuration.
    """
    __tablename__ = "hospitals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    address = Column(String(500), nullable=False)
    license_number = Column(String(100), unique=True, nullable=False, index=True)
    contact_email = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False)
    status = Column(String(50), default="DRAFT", index=True)  # DRAFT, SUBMITTED, UNDER_REVIEW, APPROVED, REJECTED, SUSPENDED
    rejection_reason = Column(String(500), nullable=True)
    correction_notes = Column(Text, nullable=True)
    services = Column(JSON, nullable=True)  # e.g. ["Emergency", "Telehealth", "Radiology"]
    operating_hours = Column(JSON, nullable=True)  # e.g. {"Monday-Friday": "08:00-18:00"}
    supported_healthcare_systems = Column(JSON, nullable=True)  # e.g. ["EPIC", "CERNER", "MOCK_EHR"]
    integration_config = Column(JSON, nullable=True)  # Endpoint, auth, environment, timeout_ms
    communication_preferences = Column(JSON, nullable=True)  # Default channels, notification rules
    lifecycle_history = Column(JSON, default=list)  # State transition audit log
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Hospital Structure Relationships
    admins = relationship("HospitalAdmin", back_populates="hospital", cascade="all, delete-orphan")
    departments = relationship("Department", back_populates="hospital", cascade="all, delete-orphan")
    doctors = relationship("Doctor", back_populates="hospital", cascade="all, delete-orphan")
    calendars = relationship("Calendar", back_populates="hospital", cascade="all, delete-orphan")
    availabilities = relationship("Availability", back_populates="hospital", cascade="all, delete-orphan")
    blocked_slots = relationship("BlockedSlot", back_populates="hospital", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="hospital")
    questionnaires = relationship("Questionnaire", back_populates="hospital", cascade="all, delete-orphan")
    questionnaire_responses = relationship("QuestionnaireResponse", back_populates="hospital", cascade="all, delete-orphan")
    workflows = relationship("Workflow", back_populates="hospital", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="hospital", cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="hospital", cascade="all, delete-orphan")
    integration_operations = relationship("IntegrationOperation", back_populates="hospital", cascade="all, delete-orphan")
    verifications = relationship("Verification", back_populates="hospital", cascade="all, delete-orphan")
    reconciliation_records = relationship("ReconciliationRecord", back_populates="hospital", cascade="all, delete-orphan")
    external_mappings = relationship("ExternalIdentifierMapping", back_populates="hospital", cascade="all, delete-orphan")


class User(Base):
    """
    Platform User Account Entity for RBAC authentication.
    """
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # PLATFORM_ADMIN, HOSPITAL_ADMIN, DOCTOR, PATIENT
    phone = Column(String(50), nullable=True)
    password_hash = Column(String(255), nullable=True)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", foreign_keys=[hospital_id])
    admin_profile = relationship("HospitalAdmin", back_populates="user", uselist=False)
    doctor_profile = relationship("Doctor", back_populates="user", uselist=False)
    patient_profile = relationship("Patient", back_populates="user", uselist=False)


class HospitalAdmin(Base):
    """
    Hospital Administrator Entity (PRD Section 4 & 22).
    Authorized staff managing hospital configuration, doctors, and questionnaires.
    """
    __tablename__ = "hospital_admins"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    role_title = Column(String(100), default="Hospital Administrator")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="admins")
    user = relationship("User", back_populates="admin_profile")


class Department(Base):
    """
    Hospital Department Entity (e.g. Surgery, Outpatient, Diagnostics).
    """
    __tablename__ = "departments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("hospital_id", "name", name="uq_hospital_department_name"),
    )

    hospital = relationship("Hospital", back_populates="departments")
    doctors = relationship("Doctor", back_populates="department")


class Specialty(Base):
    """
    Medical Specialty Entity (e.g. Orthopedics, Cardiology, General Medicine, Dermatology).
    """
    __tablename__ = "specialties"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(50), unique=True, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    doctors = relationship("Doctor", back_populates="specialty_rel")
    questionnaires = relationship("Questionnaire", back_populates="specialty")


# =====================================================================
# 2. DOCTOR & SCHEDULING ENTITIES
# =====================================================================

class Doctor(Base):
    """
    Doctor Entity (PRD Section 6 & 22).
    Associated with Hospital, Department, Specialty, and Calendars.
    """
    __tablename__ = "doctors"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    department_id = Column(String(36), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True)
    specialty_id = Column(String(36), ForeignKey("specialties.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    full_name = Column(String(255), nullable=False)
    specialty = Column(String(100), nullable=False, index=True)  # Denormalized for fast search and backward compatibility
    photo_url = Column(String(500), nullable=True)
    qualifications = Column(String(255), nullable=True)
    experience_years = Column(Integer, default=5)
    languages = Column(String(255), default="English")
    bio = Column(Text, nullable=True)
    consultation_fee = Column(Float, default=150.0)
    slot_duration_min = Column(Integer, default=30)
    external_provider_id = Column(String(100), nullable=True, index=True)
    supported_appointment_types = Column(String(255), nullable=True)  # e.g. "IN_PERSON,VIDEO_CONSULT,ROUTINE,URGENT" or subset
    status = Column(String(50), default="ACTIVE")  # Invited, Active, Inactive, Suspended
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_doctors_hospital_specialty", "hospital_id", "specialty"),
    )

    hospital = relationship("Hospital", back_populates="doctors")
    department = relationship("Department", back_populates="doctors")
    specialty_rel = relationship("Specialty", back_populates="doctors")
    calendars = relationship("Calendar", back_populates="doctor", cascade="all, delete-orphan")
    availabilities = relationship("Availability", back_populates="doctor", cascade="all, delete-orphan")
    blocked_slots = relationship("BlockedSlot", back_populates="doctor", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="doctor")
    user = relationship("User", back_populates="doctor_profile")
    
    # Preserved relationships for backward compatibility
    time_slots = relationship("TimeSlot", back_populates="doctor", cascade="all, delete-orphan")
    availability_schedules = relationship("AvailabilitySchedule", back_populates="doctor", cascade="all, delete-orphan")
    questionnaires = relationship("Questionnaire", back_populates="doctor")

    @property
    def email(self) -> str:
        if hasattr(self, "_transient_email") and self._transient_email:
            return self._transient_email
        if self.user and getattr(self.user, "email", None):
            return self.user.email
        return f"doctor.{self.id[:8]}@hospital.org"

    @email.setter
    def email(self, value: str):
        self._transient_email = value

    @property
    def phone(self) -> str:
        if hasattr(self, "_transient_phone") and self._transient_phone:
            return self._transient_phone
        if self.user and getattr(self.user, "phone", None):
            return self.user.phone
        return ""

    @phone.setter
    def phone(self, value: str):
        self._transient_phone = value


class Calendar(Base):
    """
    Doctor / Resource Calendar Entity (PRD Section 6 & 22).
    Represents the operational calendar timezone and schedule owner.
    """
    __tablename__ = "calendars"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), default="Primary Clinical Calendar")
    timezone = Column(String(50), default="UTC")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="calendars")
    doctor = relationship("Doctor", back_populates="calendars")
    availabilities = relationship("Availability", back_populates="calendar", cascade="all, delete-orphan")
    blocked_slots = relationship("BlockedSlot", back_populates="calendar", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="calendar")


class Availability(Base):
    """
    Doctor Availability Entity (PRD Section 7 & 22).
    Represents recurring working hours and configurable bookable slots.
    """
    __tablename__ = "availabilities"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_id = Column(String(36), ForeignKey("calendars.id", ondelete="CASCADE"), nullable=True, index=True)
    day_of_week = Column(Integer, nullable=True)  # 0=Monday ... 6=Sunday for weekly recurring pattern
    start_time = Column(String(10), nullable=False)  # "09:00"
    end_time = Column(String(10), nullable=False)    # "17:00"
    slot_duration_minutes = Column(Integer, default=30)
    specific_date = Column(DateTime, nullable=True)  # Optional date override
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="availabilities")
    doctor = relationship("Doctor", back_populates="availabilities")
    calendar = relationship("Calendar", back_populates="availabilities")


class BlockedSlot(Base):
    """
    Doctor Blocked Period / Leave Entity (PRD Section 7 & 22).
    Prevents slots from being booked during doctor leaves, surgeries, or breaks.
    """
    __tablename__ = "blocked_slots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_id = Column(String(36), ForeignKey("calendars.id", ondelete="CASCADE"), nullable=True, index=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    reason = Column(String(255), default="Doctor Unavailable / Blocked Period")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="blocked_slots")
    doctor = relationship("Doctor", back_populates="blocked_slots")
    calendar = relationship("Calendar", back_populates="blocked_slots")


# Existing compatibility entities for scheduling & slot service
class AvailabilitySchedule(Base):
    __tablename__ = "availability_schedules"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, ..., 6=Sunday
    start_time = Column(String(10), nullable=False)  # "09:00"
    end_time = Column(String(10), nullable=False)    # "17:00"
    is_active = Column(Boolean, default=True)

    doctor = relationship("Doctor", back_populates="availability_schedules")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    status = Column(String(50), default="AVAILABLE", index=True)  # AVAILABLE, HELD, BOOKED
    hold_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    doctor = relationship("Doctor", back_populates="time_slots")
    appointment = relationship("Appointment", back_populates="slot", uselist=False)


# =====================================================================
# 3. PATIENT & APPOINTMENT ENTITIES
# =====================================================================

class Patient(Base):
    """
    Patient Entity (PRD Section 8 & 22).
    Follows data minimization while maintaining contact and scheduling preferences.
    """
    __tablename__ = "patients"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    primary_hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="SET NULL"), nullable=True, index=True)
    patient_mrn = Column(String(100), nullable=True, index=True)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    date_of_birth = Column(String(50), nullable=True)
    gender = Column(String(50), nullable=True)
    emergency_contact = Column(String(255), nullable=True)
    communication_preference = Column(String(50), default="SMS")  # SMS, EMAIL, WHATSAPP, VOICE
    preferences = Column(JSON, nullable=True)  # Scheduling constraints, language, etc.
    external_patient_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    appointments = relationship("Appointment", back_populates="patient")
    questionnaire_responses = relationship("QuestionnaireResponse", back_populates="patient")
    ai_conversations = relationship("AIConversation", back_populates="patient")
    user = relationship("User", back_populates="patient_profile")

    @property
    def name(self) -> str:
        return self.full_name


class Appointment(Base):
    """
    Appointment Entity (PRD Section 14 & 22).
    Tracks internal/external identifiers, life-cycle states, and supports double-booking prevention.
    """
    __tablename__ = "appointments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    appointment_number = Column(String(50), unique=True, default=lambda: f"APT-{uuid.uuid4().hex[:8].upper()}", index=True)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(String(36), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_id = Column(String(36), ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True)
    slot_id = Column(String(36), ForeignKey("time_slots.id", ondelete="SET NULL"), nullable=True)

    appointment_type = Column(String(50), default="IN_PERSON")  # IN_PERSON, VIDEO_CONSULT, ROUTINE, URGENT
    start_time = Column(DateTime, nullable=True, index=True)
    end_time = Column(DateTime, nullable=True)
    
    # State lifecycle: Requested, Pending, Confirmed, Rescheduled, Cancelled, Completed, No-show, Failed, Synchronization Pending, Reconciliation Required
    status = Column(String(50), default="HELD", index=True)
    
    ehr_appointment_id = Column(String(100), nullable=True, index=True)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    retry_count = Column(Integer, default=0)
    chief_complaint = Column(Text, nullable=True)
    urgency_level = Column(String(50), default="ROUTINE")  # ROUTINE, URGENT, EMERGENCY
    ai_triage_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_appointments_doctor_date", "doctor_id", "start_time"),
        Index("idx_appointments_hospital_status", "hospital_id", "status"),
    )

    hospital = relationship("Hospital", back_populates="appointments")
    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")
    calendar = relationship("Calendar", back_populates="appointments")
    slot = relationship("TimeSlot", back_populates="appointment")
    
    questionnaire_responses = relationship("QuestionnaireResponse", back_populates="appointment", cascade="all, delete-orphan")
    questionnaire = relationship("PreVisitQuestionnaire", back_populates="appointment", uselist=False, cascade="all, delete-orphan")
    reminders = relationship("ReminderLog", back_populates="appointment", cascade="all, delete-orphan")
    sync_logs = relationship("EhrSyncLog", back_populates="appointment", cascade="all, delete-orphan")
    integration_operations = relationship("IntegrationOperation", back_populates="appointment", cascade="all, delete-orphan")
    verifications = relationship("Verification", back_populates="appointment", cascade="all, delete-orphan")
    reconciliation_records = relationship("ReconciliationRecord", back_populates="appointment", cascade="all, delete-orphan")
    workflows = relationship("Workflow", back_populates="appointment", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="appointment", cascade="all, delete-orphan")


# =====================================================================
# 4. QUESTIONNAIRE ENTITIES
# =====================================================================

class Questionnaire(Base):
    """
    Questionnaire Template Entity (PRD Section 15 & 22).
    Configurable pre-visit questionnaires associated with hospital, specialty, doctor,
    appointment type, or condition/category.
    """
    __tablename__ = "questionnaires"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    specialty_id = Column(String(36), ForeignKey("specialties.id", ondelete="SET NULL"), nullable=True, index=True)
    doctor_id = Column(String(36), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    appointment_type = Column(String(50), default="ALL", nullable=True, index=True)  # IN_PERSON, VIDEO_CONSULT, ALL
    condition_category = Column(String(100), default="GENERAL_INTAKE", nullable=True, index=True)  # CARDIOLOGY, ORTHO, GENERAL_INTAKE, etc.
    questions = Column(JSON, nullable=False)  # List of questions: {id, question, type, options, required, placeholder, urgency_rules}
    is_active = Column(Boolean, default=True)
    is_approved = Column(Boolean, default=True)
    approved_by = Column(String(100), nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="questionnaires")
    specialty = relationship("Specialty", back_populates="questionnaires")
    doctor = relationship("Doctor", back_populates="questionnaires")
    responses = relationship("QuestionnaireResponse", back_populates="questionnaire", cascade="all, delete-orphan")


class QuestionnaireResponse(Base):
    """
    Questionnaire Response Entity (PRD Section 15 & 22).
    Stores structured responses, urgency flags, and patient-reported intake summaries for doctor review.
    """
    __tablename__ = "questionnaire_responses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    questionnaire_id = Column(String(36), ForeignKey("questionnaires.id", ondelete="SET NULL"), nullable=True, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(String(36), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    answers = Column(JSON, nullable=True)  # Key-value answers: {question_id: answer_val}
    patient_intake_summary = Column(Text, nullable=True)
    status = Column(String(50), default="ASSIGNED", index=True)  # ASSIGNED, IN_PROGRESS, SUBMITTED, URGENT_ESCALATED, REVIEWED
    is_urgent = Column(Boolean, default=False, index=True)
    urgent_reasons = Column(JSON, nullable=True)  # List of strings explaining triggered urgency flags
    reviewed_by = Column(String(36), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    doctor_notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def ai_clinical_summary(self) -> Optional[str]:
        return self.patient_intake_summary

    @ai_clinical_summary.setter
    def ai_clinical_summary(self, value: Optional[str]):
        self.patient_intake_summary = value

    questionnaire = relationship("Questionnaire", back_populates="responses")
    appointment = relationship("Appointment", back_populates="questionnaire_responses")
    patient = relationship("Patient", back_populates="questionnaire_responses")
    hospital = relationship("Hospital", back_populates="questionnaire_responses")
    reviewer = relationship("Doctor", foreign_keys=[reviewed_by])


# Preserved for existing questionnaire service compatibility
class PreVisitQuestionnaire(Base):
    __tablename__ = "pre_visit_questionnaires"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(String(36), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    questions = Column(JSON, nullable=False)
    answers = Column(JSON, nullable=True)
    patient_intake_summary = Column(Text, nullable=True)
    status = Column(String(50), default="ASSIGNED")  # ASSIGNED, IN_PROGRESS, SUBMITTED, URGENT_ESCALATED, REVIEWED
    is_urgent = Column(Boolean, default=False)
    urgent_reasons = Column(JSON, nullable=True)
    reviewed_by = Column(String(36), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    doctor_notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def ai_clinical_summary(self) -> Optional[str]:
        return self.patient_intake_summary

    @ai_clinical_summary.setter
    def ai_clinical_summary(self, value: Optional[str]):
        self.patient_intake_summary = value

    appointment = relationship("Appointment", back_populates="questionnaire")


# =====================================================================
# 5. AI AGENT & CONVERSATION ENTITIES
# =====================================================================

class AIConversation(Base):
    """
    AI Conversation Session Entity (PRD Section 10 & 22).
    Tracks active conversation lifecycle across web voice, chat, and telephone channels.
    """
    __tablename__ = "ai_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(100), unique=True, nullable=False, index=True)
    patient_id = Column(String(36), ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="SET NULL"), nullable=True, index=True)
    channel = Column(String(50), default="web_voice")  # web_voice, telephone, chat
    status = Column(String(50), default="ACTIVE", index=True)  # ACTIVE, COMPLETED, ESCALATED_TO_HUMAN, ABANDONED
    transcript = Column(JSON, default=list)  # Turns: [{role, content, timestamp}]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    patient = relationship("Patient", back_populates="ai_conversations")
    ai_context = relationship("AIContext", back_populates="conversation", uselist=False, cascade="all, delete-orphan")


class AIContext(Base):
    """
    Structured AI Conversational Context Entity (PRD Section 10 & 23).
    Durable representation of active intent, selected doctor, slot, and preferences.
    """
    __tablename__ = "ai_contexts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE"), unique=True, nullable=False)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="SET NULL"), nullable=True)
    current_intent = Column(String(100), nullable=True)
    selected_hospital_id = Column(String(36), nullable=True)
    selected_doctor_id = Column(String(36), nullable=True)
    selected_slot_id = Column(String(36), nullable=True)
    current_appointment_id = Column(String(36), nullable=True)
    extracted_symptoms = Column(JSON, default=list)
    urgency_level = Column(String(50), default="ROUTINE")
    preferences = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    conversation = relationship("AIConversation", back_populates="ai_context")


# =====================================================================
# 6. INTEGRATION, VERIFICATION & RECONCILIATION ENTITIES
# =====================================================================

class IntegrationOperation(Base):
    """
    EHR Integration Operation Entity (PRD Section 12 & 22).
    Records outbound requests to external healthcare systems / Mock EHR.
    """
    __tablename__ = "integration_operations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True)
    system_name = Column(String(100), default="MOCK_EHR")
    operation_type = Column(String(50), nullable=False)  # CREATE_APPOINTMENT, VERIFY, CANCEL, RESCHEDULE, LOOKUP_PATIENT
    idempotency_key = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)  # SUCCESS, TIMEOUT, FAILED, RETRIED
    request_payload = Column(Text, nullable=True)
    response_payload = Column(Text, nullable=True)
    response_time_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="integration_operations")
    appointment = relationship("Appointment", back_populates="integration_operations")


class ExternalIdentifierMapping(Base):
    """
    External Identifier Mapping Entity (PRD Section 12 & 22).
    Maintains cross-system mappings: Internal Entity <-> External System Entity.
    """
    __tablename__ = "external_identifier_mappings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    system_name = Column(String(100), default="MOCK_EHR", nullable=False)
    entity_type = Column(String(50), nullable=False)  # PATIENT, DOCTOR, APPOINTMENT, FACILITY
    internal_id = Column(String(100), nullable=False, index=True)
    external_id = Column(String(100), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("system_name", "entity_type", "internal_id", name="uq_sys_entity_internal"),
        Index("idx_ext_mapping_lookup", "system_name", "entity_type", "external_id"),
    )

    hospital = relationship("Hospital", back_populates="external_mappings")


class Verification(Base):
    """
    External Verification Entity (PRD Section 13 & 22).
    Documents that an external EHR record was verified after a creation or timeout.
    """
    __tablename__ = "verifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String(100), nullable=False, index=True)
    external_appointment_id = Column(String(100), nullable=True, index=True)
    status = Column(String(50), nullable=False)  # FOUND, NOT_FOUND, ERROR
    details = Column(JSON, nullable=True)
    verified_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="verifications")
    appointment = relationship("Appointment", back_populates="verifications")


class ReconciliationRecord(Base):
    """
    Reconciliation Record Entity (PRD Section 13 & 22).
    Stores unresolved or recovered failure cases requiring auditing or human escalation.
    """
    __tablename__ = "reconciliation_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key = Column(String(100), nullable=False, index=True)
    failure_reason = Column(Text, nullable=False)
    status = Column(String(50), default="RECONCILIATION_REQUIRED", index=True)  # RECONCILED, ESCALATED_TO_HUMAN, RETRY_PENDING, RECONCILIATION_REQUIRED
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="reconciliation_records")
    appointment = relationship("Appointment", back_populates="reconciliation_records")


# Preserved for existing sync logging
class EhrSyncLog(Base):
    __tablename__ = "ehr_sync_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)  # CREATE, VERIFY, RETRY, VERIFY_RECOVERY
    status = Column(String(50), nullable=False)  # SUCCESS, TIMEOUT, ERROR
    request_payload = Column(Text, nullable=True)
    response_payload = Column(Text, nullable=True)
    response_time_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    appointment = relationship("Appointment", back_populates="sync_logs")


# =====================================================================
# 7. WORKFLOW & NOTIFICATION ENTITIES
# =====================================================================

class Workflow(Base):
    """
    Workflow Entity (PRD Section 16 & 22).
    Tracks asynchronous and scheduled multi-step workflows with delays,
    conditional branching, retries, idempotency, and execution history.
    """
    __tablename__ = "workflows"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True)
    workflow_type = Column(String(100), nullable=False)  # POST_BOOKING_WORKFLOW, APPOINTMENT_CANCELLATION_WORKFLOW, EHR_RECOVERY_WORKFLOW, CLINICAL_URGENCY_WORKFLOW
    status = Column(String(50), default="RUNNING", index=True)  # PENDING, RUNNING, COMPLETED, FAILED, RETRIED, PAUSED
    current_step = Column(Integer, default=0)
    idempotency_key = Column(String(100), nullable=True, index=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    scheduled_for = Column(DateTime, nullable=True, index=True)
    payload = Column(JSON, nullable=True)
    execution_history = Column(JSON, default=list)
    error_message = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="workflows")
    appointment = relationship("Appointment", back_populates="workflows")


class Notification(Base):
    """
    Notification Entity (PRD Section 17 & 22).
    Stores scheduled, sent, and failed multi-party notifications across channels.
    """
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=True, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True)
    recipient_type = Column(String(50), nullable=False)  # PATIENT, DOCTOR, HOSPITAL_ADMIN, HOSPITAL
    recipient_contact = Column(String(255), nullable=False)
    channel = Column(String(50), default="SMS")  # SMS, EMAIL, WHATSAPP, VOICE
    template = Column(String(100), nullable=False)
    message_content = Column(Text, nullable=True)
    idempotency_key = Column(String(100), nullable=True, index=True)
    retry_count = Column(Integer, default=0)
    payload = Column(JSON, nullable=True)
    scheduled_for = Column(DateTime, nullable=False, index=True)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="SCHEDULED", index=True)  # SCHEDULED, SENT, FAILED
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    hospital = relationship("Hospital", back_populates="notifications")
    appointment = relationship("Appointment", back_populates="notifications")


# Preserved for existing reminder service compatibility
class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False)
    reminder_type = Column(String(50), nullable=False)  # CONFIRMATION, PRE_VISIT_24H, PRE_VISIT_1H
    scheduled_for = Column(DateTime, nullable=False)
    status = Column(String(50), default="SCHEDULED")  # SCHEDULED, SENT, FAILED
    channel = Column(String(50), default="SMS")       # SMS, EMAIL, WHATSAPP
    message_content = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    appointment = relationship("Appointment", back_populates="reminders")


# =====================================================================
# 8. AUDIT ENTITY
# =====================================================================

class AuditEvent(Base):
    """
    AuditEvent Entity (PRD Section 19, 21 & 22).
    Maintains immutable operational and security audit records with correlation IDs.
    """
    __tablename__ = "audit_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    correlation_id = Column(String(100), nullable=True, index=True)
    hospital_id = Column(String(36), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=True, index=True)
    actor_id = Column(String(100), nullable=True)
    actor_role = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False, index=True)
    resource_id = Column(String(100), nullable=True)
    status = Column(String(50), default="SUCCESS")
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("idx_audit_correlation_tenant", "correlation_id", "hospital_id"),
    )

    hospital = relationship("Hospital", back_populates="audit_events")


# =====================================================================
# 9. MOCK EHR & CHAOS SUPPORT ENTITIES
# =====================================================================

class MockEhrRecord(Base):
    __tablename__ = "mock_ehr_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    ehr_appointment_id = Column(String(100), unique=True, nullable=False)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    patient_name = Column(String(255), nullable=False)
    doctor_name = Column(String(255), nullable=False)
    hospital_name = Column(String(255), nullable=False)
    slot_time = Column(String(100), nullable=False)
    chief_complaint = Column(Text, nullable=True)
    status = Column(String(50), default="CONFIRMED_IN_EHR")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChaosSetting(Base):
    __tablename__ = "chaos_settings"

    id = Column(Integer, primary_key=True, default=1)
    mode = Column(String(50), default="TIMEOUT_AFTER_SAVE")
    simulated_delay_ms = Column(Integer, default=3200)
    failure_active = Column(Boolean, default=True)
    one_shot = Column(Boolean, default=False)
