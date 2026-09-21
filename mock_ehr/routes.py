import asyncio
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from mock_ehr.database import get_ehr_db
from mock_ehr import models

router = APIRouter(tags=["Mock EHR Service"])

# =====================================================================
# 1. PATIENTS ENDPOINTS
# =====================================================================

@router.post("/patients", response_model=models.PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(payload: models.PatientCreate, db: Session = Depends(get_ehr_db)):
    """Registers a new external patient in the Mock EHR system."""
    patient = models.EhrPatient(
        first_name=payload.first_name,
        last_name=payload.last_name,
        dob=payload.dob,
        contact=payload.contact,
        gender=payload.gender
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient

@router.get("/patients/{id}", response_model=models.PatientResponse)
def get_patient(id: str, db: Session = Depends(get_ehr_db)):
    """Retrieves an external patient record by ID or contact phone number."""
    patient = db.query(models.EhrPatient).filter(
        (models.EhrPatient.id == id) | (models.EhrPatient.contact == id)
    ).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient with ID or contact '{id}' not found in EHR.")
    return patient


# =====================================================================
# 2. PROVIDERS ENDPOINTS
# =====================================================================

@router.get("/providers", response_model=List[models.ProviderResponse])
def get_providers(
    specialty: Optional[str] = Query(None, description="Filter by specialty"),
    db: Session = Depends(get_ehr_db)
):
    """Lists external healthcare providers from the EHR directory."""
    query = db.query(models.EhrProvider)
    if specialty:
        query = query.filter(models.EhrProvider.specialty.ilike(f"%{specialty}%"))
    return query.all()

@router.get("/providers/{id}", response_model=models.ProviderResponse)
def get_provider(id: str, db: Session = Depends(get_ehr_db)):
    """Retrieves external provider details by ID."""
    provider = db.query(models.EhrProvider).filter(models.EhrProvider.id == id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider with ID '{id}' not found in EHR.")
    return provider


# =====================================================================
# 3. AVAILABILITY ENDPOINTS
# =====================================================================

@router.get("/availability", response_model=List[models.AvailabilityResponse])
def get_availability(
    provider_id: Optional[str] = Query(None, description="Provider ID"),
    start_date: Optional[str] = Query(None, description="Filter start date"),
    end_date: Optional[str] = Query(None, description="Filter end date"),
    db: Session = Depends(get_ehr_db)
):
    """Queries provider availability in the external EHR system."""
    query = db.query(models.EhrAvailability).filter(models.EhrAvailability.status == "AVAILABLE")
    if provider_id:
        query = query.filter(models.EhrAvailability.provider_id == provider_id)
    return query.all()


# =====================================================================
# 4. APPOINTMENTS ENDPOINTS
# =====================================================================

@router.post("/appointments", response_model=models.AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(payload: models.AppointmentCreate, db: Session = Depends(get_ehr_db)):
    """
    Creates an appointment record in the external Mock EHR.
    Supports idempotency verification and chaos injection simulation.
    """
    # 1. Idempotency check: if appointment exists, return existing record
    existing = db.query(models.EhrAppointment).filter(
        models.EhrAppointment.idempotency_key == payload.idempotency_key
    ).first()
    if existing:
        return existing

    # 2. Check Chaos settings
    chaos = db.query(models.EhrChaosConfig).first()
    if chaos and chaos.failure_active:
        delay_sec = max(0.05, chaos.simulated_delay_ms / 1000.0)
        is_one_shot = getattr(chaos, "one_shot", False)
        if is_one_shot:
            chaos.failure_active = False
            db.commit()

        if chaos.mode == "TIMEOUT_AFTER_SAVE":
            # Saves in external DB first, then drops connection / times out
            new_id = f"EHR-{uuid.uuid4().hex[:6].upper()}"
            appt = models.EhrAppointment(
                id=new_id,
                idempotency_key=payload.idempotency_key,
                patient_id=payload.patient_id or "P-101",
                patient_name=payload.patient_name,
                provider_id=payload.provider_id or "DOC-501",
                provider_name=payload.doctor_name,
                hospital_name=payload.hospital_name,
                slot_time=payload.slot_time,
                chief_complaint=payload.chief_complaint,
                status="CONFIRMED_IN_EHR"
            )
            db.add(appt)
            db.commit()
            db.refresh(appt)

            await asyncio.sleep(delay_sec)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Gateway Timeout: External EHR socket hangup after save (Simulated)"
            )

        elif chaos.mode == "TIMEOUT_BEFORE_SAVE":
            # Drops before record is saved
            await asyncio.sleep(delay_sec)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Gateway Timeout: External EHR failed before saving (Simulated)"
            )

        elif chaos.mode == "OUTAGE":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="External EHR Service Unavailable: Planned datacenter maintenance (Simulated Outage)"
            )

        elif chaos.mode == "RATE_LIMIT":
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="External EHR Rate Limit Exceeded: 429 Too Many Requests (Simulated)"
            )

        elif chaos.mode == "AUTH_FAILURE":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="External EHR Gateway Authentication Failed: Invalid credentials or expired mTLS cert"
            )

        elif chaos.mode == "VALIDATION_ERROR":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="External EHR Schema Validation Error: Unrecognized HL7/FHIR payload field mapping"
            )

        elif chaos.mode == "HTTP_500":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="External EHR Database Error (Simulated Chaos)"
            )

    # 3. Normal Success Path
    new_id = f"EHR-{uuid.uuid4().hex[:6].upper()}"
    appt = models.EhrAppointment(
        id=new_id,
        idempotency_key=payload.idempotency_key,
        patient_id=payload.patient_id or "P-101",
        patient_name=payload.patient_name,
        provider_id=payload.provider_id or "DOC-501",
        provider_name=payload.doctor_name,
        hospital_name=payload.hospital_name,
        slot_time=payload.slot_time,
        chief_complaint=payload.chief_complaint,
        status="CONFIRMED_IN_EHR"
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


@router.get("/appointments/{id}", response_model=models.AppointmentResponse)
def get_appointment(id: str, db: Session = Depends(get_ehr_db)):
    """Retrieves an external appointment by external ID or idempotency key."""
    appt = db.query(models.EhrAppointment).filter(
        (models.EhrAppointment.id == id) | (models.EhrAppointment.idempotency_key == id)
    ).first()
    if not appt:
        raise HTTPException(status_code=404, detail=f"Appointment '{id}' not found in EHR.")
    return appt


@router.put("/appointments/{id}", response_model=models.AppointmentResponse)
def update_appointment(id: str, payload: models.AppointmentUpdate, db: Session = Depends(get_ehr_db)):
    """Updates an external appointment in the Mock EHR."""
    appt = db.query(models.EhrAppointment).filter(
        (models.EhrAppointment.id == id) | (models.EhrAppointment.idempotency_key == id)
    ).first()
    if not appt:
        raise HTTPException(status_code=404, detail=f"Appointment '{id}' not found in EHR.")

    if payload.slot_time is not None:
        appt.slot_time = payload.slot_time
    if payload.chief_complaint is not None:
        appt.chief_complaint = payload.chief_complaint
    if payload.status is not None:
        appt.status = payload.status

    appt.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(appt)
    return appt


@router.delete("/appointments/{id}")
def delete_appointment(id: str, db: Session = Depends(get_ehr_db)):
    """Cancels/deletes an appointment in the Mock EHR."""
    appt = db.query(models.EhrAppointment).filter(
        (models.EhrAppointment.id == id) | (models.EhrAppointment.idempotency_key == id)
    ).first()
    if not appt:
        raise HTTPException(status_code=404, detail=f"Appointment '{id}' not found in EHR.")

    appt.status = "CANCELLED"
    appt.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"Appointment '{id}' has been cancelled in EHR.", "status": "CANCELLED"}


@router.post("/appointments/{id}/verify", response_model=models.VerifyResponse)
def verify_appointment_post(id: str, db: Session = Depends(get_ehr_db)):
    """
    POST verification endpoint:
    Verifies if appointment was saved in the external EHR (matches by ID or idempotency key).
    """
    appt = db.query(models.EhrAppointment).filter(
        (models.EhrAppointment.id == id) | (models.EhrAppointment.idempotency_key == id)
    ).first()
    if appt:
        return models.VerifyResponse(
            found=True,
            appointment_id=appt.id,
            idempotency_key=appt.idempotency_key,
            status=appt.status,
            details={
                "slot_time": appt.slot_time,
                "provider_id": appt.provider_id,
                "patient_id": appt.patient_id,
                "created_at": str(appt.created_at)
            }
        )
    return models.VerifyResponse(
        found=False,
        appointment_id=None,
        idempotency_key=id,
        status=None,
        details=None
    )


# =====================================================================
# 5. DIAGNOSTIC & COMPATIBILITY ENDPOINTS
# =====================================================================

@router.get("/appointments/verify", response_model=models.VerifyResponse)
def verify_appointment_get(
    idempotency_key: Optional[str] = Query(None),
    id: Optional[str] = Query(None),
    db: Session = Depends(get_ehr_db)
):
    """GET verification endpoint for backward compatibility."""
    key = idempotency_key or id
    if not key:
        raise HTTPException(status_code=400, detail="idempotency_key or id required.")
    return verify_appointment_post(key, db)


@router.get("/chaos")
def get_chaos(db: Session = Depends(get_ehr_db)):
    """Retrieves current chaos configuration."""
    chaos = db.query(models.EhrChaosConfig).first()
    if not chaos:
        chaos = models.EhrChaosConfig(id=1, mode="TIMEOUT_AFTER_SAVE", simulated_delay_ms=2500, failure_active=True, one_shot=False)
        db.add(chaos)
        db.commit()
        db.refresh(chaos)
    return {
        "mode": chaos.mode,
        "simulated_delay_ms": chaos.simulated_delay_ms,
        "failure_active": chaos.failure_active,
        "one_shot": getattr(chaos, "one_shot", False)
    }


@router.post("/chaos")
def set_chaos(payload: Dict[str, Any], db: Session = Depends(get_ehr_db)):
    """Updates chaos settings for external failure testing."""
    chaos = db.query(models.EhrChaosConfig).first()
    if not chaos:
        chaos = models.EhrChaosConfig(id=1)
        db.add(chaos)

    if "mode" in payload:
        chaos.mode = payload["mode"]
    if "simulated_delay_ms" in payload:
        chaos.simulated_delay_ms = payload["simulated_delay_ms"]
    if "failure_active" in payload:
        chaos.failure_active = payload["failure_active"]
    if "one_shot" in payload:
        chaos.one_shot = payload["one_shot"]

    db.commit()
    db.refresh(chaos)
    return {"message": "Chaos configuration updated", "chaos": {
        "mode": chaos.mode,
        "simulated_delay_ms": chaos.simulated_delay_ms,
        "failure_active": chaos.failure_active,
        "one_shot": getattr(chaos, "one_shot", False)
    }}


@router.get("/records")
def list_records(db: Session = Depends(get_ehr_db)):
    """Lists all external EHR appointment records."""
    records = db.query(models.EhrAppointment).order_by(models.EhrAppointment.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "external_appointment_id": r.id,
            "idempotency_key": r.idempotency_key,
            "patient_id": r.patient_id,
            "patient_name": r.patient_name,
            "provider_id": r.provider_id,
            "doctor_name": r.provider_name,
            "hospital_name": r.hospital_name,
            "slot_time": r.slot_time,
            "status": r.status,
            "created_at": r.created_at
        } for r in records
    ]


# =====================================================================
# 6. FACILITIES & CALENDARS ENDPOINTS
# =====================================================================

@router.get("/facilities")
def get_facilities(db: Session = Depends(get_ehr_db)):
    """Lists external facilities / clinics in the healthcare system."""
    return [
        {
            "id": "FAC-101",
            "name": "St. Jude Memorial Hospital Main Campus",
            "address": "450 Healthcare Blvd, Metro City, NY",
            "phone": "+1-555-019-2834",
            "is_active": True
        },
        {
            "id": "FAC-102",
            "name": "Metro Health General Clinic",
            "address": "120 University Ave, Metro City, NY",
            "phone": "+1-555-019-9920",
            "is_active": True
        }
    ]

@router.get("/facilities/{id}")
def get_facility(id: str, db: Session = Depends(get_ehr_db)):
    """Retrieves facility details by external facility ID."""
    facilities = {
        "FAC-101": {
            "id": "FAC-101",
            "name": "St. Jude Memorial Hospital Main Campus",
            "address": "450 Healthcare Blvd, Metro City, NY",
            "phone": "+1-555-019-2834",
            "is_active": True
        },
        "FAC-102": {
            "id": "FAC-102",
            "name": "Metro Health General Clinic",
            "address": "120 University Ave, Metro City, NY",
            "phone": "+1-555-019-9920",
            "is_active": True
        }
    }
    if id in facilities:
        return facilities[id]
    return {
        "id": id,
        "name": f"Healthcare Facility {id}",
        "address": "Medical Center Blvd",
        "phone": "+1-555-000-0000",
        "is_active": True
    }

@router.get("/calendars/{id}")
def get_calendar(id: str, db: Session = Depends(get_ehr_db)):
    """Retrieves external calendar / scheduling resource details."""
    return {
        "id": id,
        "name": f"Clinical Schedule Resource {id}",
        "timezone": "America/New_York",
        "is_active": True
    }

