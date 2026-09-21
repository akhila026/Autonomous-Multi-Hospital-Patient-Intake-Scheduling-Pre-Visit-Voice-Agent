import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, ForeignKey
from mock_ehr.database import EhrBase

# =====================================================================
# 1. SQLALCHEMY DATABASE MODELS (ISOLATED TO MOCK EHR)
# =====================================================================

class EhrPatient(EhrBase):
    __tablename__ = "ehr_patients"

    id = Column(String(50), primary_key=True, default=lambda: f"P-{uuid.uuid4().hex[:6].upper()}")
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    dob = Column(String(50), nullable=True)
    contact = Column(String(100), nullable=True)
    gender = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EhrProvider(EhrBase):
    __tablename__ = "ehr_providers"

    id = Column(String(50), primary_key=True, default=lambda: f"DOC-{uuid.uuid4().hex[:6].upper()}")
    name = Column(String(255), nullable=False)
    specialty = Column(String(100), nullable=False, index=True)
    department = Column(String(100), nullable=False)
    hospital_name = Column(String(255), default="St. Jude Memorial Hospital")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EhrAvailability(EhrBase):
    __tablename__ = "ehr_availability"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_id = Column(String(50), ForeignKey("ehr_providers.id"), nullable=False, index=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    status = Column(String(50), default="AVAILABLE")  # AVAILABLE, BOOKED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EhrAppointment(EhrBase):
    __tablename__ = "ehr_appointments"

    id = Column(String(50), primary_key=True, default=lambda: f"EHR-{uuid.uuid4().hex[:6].upper()}")
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    patient_id = Column(String(50), nullable=False, index=True)
    patient_name = Column(String(255), nullable=True)
    provider_id = Column(String(50), nullable=False, index=True)
    provider_name = Column(String(255), nullable=True)
    hospital_name = Column(String(255), nullable=True)
    slot_time = Column(String(100), nullable=False)
    chief_complaint = Column(Text, nullable=True)
    status = Column(String(50), default="CONFIRMED_IN_EHR")  # CONFIRMED_IN_EHR, CANCELLED, RESCHEDULED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EhrChaosConfig(EhrBase):
    __tablename__ = "ehr_chaos_config"

    id = Column(Integer, primary_key=True, default=1)
    mode = Column(String(50), default="TIMEOUT_AFTER_SAVE")  # TIMEOUT_AFTER_SAVE, TIMEOUT_BEFORE_SAVE, HTTP_500, NORMAL
    simulated_delay_ms = Column(Integer, default=2500)
    failure_active = Column(Boolean, default=True)
    one_shot = Column(Boolean, default=False)


# =====================================================================
# 2. PYDANTIC DTO SCHEMAS
# =====================================================================

class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    dob: Optional[str] = None
    contact: str
    gender: Optional[str] = None

class PatientResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    dob: Optional[str] = None
    contact: Optional[str] = None
    gender: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ProviderResponse(BaseModel):
    id: str
    name: str
    specialty: str
    department: str
    hospital_name: str
    is_active: bool

    class Config:
        from_attributes = True

class AvailabilityResponse(BaseModel):
    id: str
    provider_id: str
    start_time: datetime
    end_time: datetime
    status: str

    class Config:
        from_attributes = True

class AppointmentCreate(BaseModel):
    idempotency_key: str
    patient_id: Optional[str] = "P-101"
    patient_name: Optional[str] = "John Doe"
    provider_id: Optional[str] = "DOC-501"
    doctor_name: Optional[str] = None
    hospital_name: Optional[str] = "St. Jude Memorial Hospital"
    slot_time: str
    chief_complaint: Optional[str] = None

class AppointmentUpdate(BaseModel):
    slot_time: Optional[str] = None
    chief_complaint: Optional[str] = None
    status: Optional[str] = None

class AppointmentResponse(BaseModel):
    id: str
    idempotency_key: str
    patient_id: str
    patient_name: Optional[str] = None
    provider_id: str
    provider_name: Optional[str] = None
    hospital_name: Optional[str] = None
    slot_time: str
    chief_complaint: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class VerifyResponse(BaseModel):
    found: bool
    appointment_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    status: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
