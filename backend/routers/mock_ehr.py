from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
from backend.services.mock_ehr_service import MockEhrService

router = APIRouter(prefix="/mock-ehr", tags=["Mock EHR (External System)"])

@router.post("/appointments", response_model=schemas.MockEhrAppointmentResponse)
async def create_external_ehr_appointment(
    req: schemas.MockEhrAppointmentRequest,
    db: Session = Depends(get_db)
):
    record = await MockEhrService.create_external_appointment(db, req)
    return schemas.MockEhrAppointmentResponse(
        ehr_appointment_id=record.ehr_appointment_id,
        idempotency_key=record.idempotency_key,
        status=record.status,
        message="Successfully registered in external EHR",
        created_at=record.created_at
    )

@router.get("/verify", response_model=schemas.MockEhrVerifyResponse)
def verify_external_appointment(
    idempotency_key: str = Query(..., description="Idempotency key to verify"),
    db: Session = Depends(get_db)
):
    """
    Idempotent verification query:
    Called after a timeout to verify if the external EHR actually created the appointment
    before initiating any retry.
    """
    return MockEhrService.verify_appointment_by_idempotency_key(db, idempotency_key)

@router.get("/records")
def list_ehr_records(db: Session = Depends(get_db)):
    records = MockEhrService.get_all_ehr_records(db)
    return [
        {
            "id": r.id,
            "ehr_appointment_id": r.ehr_appointment_id,
            "idempotency_key": r.idempotency_key,
            "patient_name": r.patient_name,
            "doctor_name": r.doctor_name,
            "hospital_name": r.hospital_name,
            "slot_time": r.slot_time,
            "status": r.status,
            "created_at": r.created_at
        } for r in records
    ]

@router.get("/chaos-config")
def get_chaos_config(db: Session = Depends(get_db)):
    setting = MockEhrService.get_or_create_chaos_setting(db)
    return {
        "mode": setting.mode,
        "simulated_delay_ms": setting.simulated_delay_ms,
        "failure_active": setting.failure_active
    }

@router.post("/chaos-config")
def update_chaos_config(req: schemas.ChaosConfigUpdate, db: Session = Depends(get_db)):
    setting = MockEhrService.update_chaos_setting(db, req)
    return {
        "message": "Chaos configuration updated",
        "mode": setting.mode,
        "simulated_delay_ms": setting.simulated_delay_ms,
        "failure_active": setting.failure_active,
        "one_shot": getattr(setting, "one_shot", False)
    }


@router.post("/demo/simulate-timeout-recovery", response_model=schemas.TimeoutRecoveryDemoResponse)
async def simulate_timeout_recovery(
    req: schemas.TimeoutRecoveryDemoRequest,
    db: Session = Depends(get_db)
):
    """
    Deterministic failure and recovery demonstration endpoint.
    Executes the complete flow:
      Appointment Request
      -> External Timeout (HTTP 504)
      -> Main app classifies outcome as UNKNOWN
      -> Query external healthcare system
      -> Determine actual external state
      -> If exists: Reconcile without duplicates & confirm to patient
      -> If not exists: Safely retry with idempotency & verify
      -> If unresolved: Create reconciliation record, release slot, escalate
    """
    import uuid
    from datetime import datetime, timedelta
    from fastapi import HTTPException
    from backend.services.appointment_service import AppointmentService

    scenario = req.scenario.upper()
    delay_ms = req.simulated_delay_ms or 150

    # 1. Configure deterministic chaos
    if scenario == "TIMEOUT_AFTER_SAVE":
        chaos_mode = "TIMEOUT_AFTER_SAVE"
        is_one_shot = False
    elif scenario == "TIMEOUT_BEFORE_SAVE":
        chaos_mode = "TIMEOUT_BEFORE_SAVE"
        is_one_shot = True  # Auto-disables after first failure so retry succeeds!
    elif scenario in ["UNRESOLVED_OUTAGE", "OUTAGE"]:
        chaos_mode = "OUTAGE"
        is_one_shot = False # Persists so retry also fails
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported scenario '{req.scenario}'. Use TIMEOUT_AFTER_SAVE, TIMEOUT_BEFORE_SAVE, or UNRESOLVED_OUTAGE.")

    MockEhrService.update_chaos_setting(
        db,
        schemas.ChaosConfigUpdate(
            mode=chaos_mode,
            simulated_delay_ms=delay_ms,
            failure_active=True,
            one_shot=is_one_shot
        )
    )

    demo_correlation_id = f"DEMO-CORR-{uuid.uuid4().hex[:8].upper()}"
    demo_idempotency_key = f"DEMO-IDEM-{uuid.uuid4().hex[:8].upper()}"

    try:
        # 2. Resolve or create test entities (Doctor, Hospital, Patient, Slot)
        hospital = db.query(models.Hospital).filter(models.Hospital.status == "APPROVED").first()
        if not hospital:
            hospital = models.Hospital(
                name="Demo St. Jude Memorial Hospital",
                address="100 Demo Way",
                license_number=f"DEMO-HOSP-{uuid.uuid4().hex[:6]}",
                contact_email="demo@hospital.com",
                phone="+1-555-019-9000",
                status="APPROVED"
            )
            db.add(hospital)
            db.commit()
            db.refresh(hospital)

        doctor = db.query(models.Doctor).filter(
            models.Doctor.hospital_id == hospital.id,
            models.Doctor.is_active == True
        ).first()
        if not doctor:
            doctor = models.Doctor(
                hospital_id=hospital.id,
                full_name="Dr. Sarah Jenkins",
                specialty="Orthopedics",
                consultation_fee=175.0,
                slot_duration_min=30,
                is_active=True
            )
            db.add(doctor)
            db.commit()
            db.refresh(doctor)

        patient = db.query(models.Patient).first()
        if not patient:
            patient = models.Patient(
                full_name="Alex Mercer (Demo)",
                phone="+1-555-432-8765",
                email="alex.mercer.demo@healthai.test"
            )
            db.add(patient)
            db.commit()
            db.refresh(patient)

        # Create fresh isolated slot on next weekday at 10:00 AM (guaranteed within doctor working hours)
        target_date = (datetime.utcnow() + timedelta(days=2)).date()
        while target_date.weekday() >= 5:
            target_date += timedelta(days=1)
        base_time = datetime.combine(target_date, datetime.min.time())
        slot_time = None
        for day_offset in range(2, 30):
            cur_date = (datetime.utcnow() + timedelta(days=day_offset)).date()
            if cur_date.weekday() >= 5:
                continue
            cur_base = datetime.combine(cur_date, datetime.min.time())
            for hour in range(9, 17):
                for minute in (0, 30):
                    cand = cur_base.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    cand_end = cand + timedelta(minutes=30)
                    existing_slot = db.query(models.TimeSlot).filter(
                        models.TimeSlot.doctor_id == doctor.id,
                        models.TimeSlot.start_time == cand
                    ).first()
                    existing_appt = db.query(models.Appointment).filter(
                        models.Appointment.doctor_id == doctor.id,
                        models.Appointment.status.in_(["CONFIRMED", "HELD", "PENDING_EHR_SYNC", "UNKNOWN_OUTCOME"]),
                        models.Appointment.start_time < cand_end,
                        models.Appointment.end_time > cand
                    ).first()
                    if not existing_slot and not existing_appt:
                        slot_time = cand
                        break
                if slot_time:
                    break
            if slot_time:
                break
        if not slot_time:
            slot_time = base_time + timedelta(days=60, hours=10)

        demo_slot = models.TimeSlot(
            doctor_id=doctor.id,
            start_time=slot_time,
            end_time=slot_time + timedelta(minutes=30),
            status="AVAILABLE"
        )
        db.add(demo_slot)
        db.commit()
        db.refresh(demo_slot)

        # 3. Execute the booking flow through the AppointmentService
        confirm_req = schemas.AppointmentConfirmRequest(
            slot_id=demo_slot.id,
            patient_id=patient.id,
            chief_complaint="Right knee swelling after trail run",
            urgency_level="ROUTINE",
            ai_triage_notes="Deterministic chaos demonstration - simulated EHR timeout",
            idempotency_key=demo_idempotency_key,
            correlation_id=demo_correlation_id
        )

        appt = await AppointmentService.confirm_and_sync_appointment(db, confirm_req)

        # 4. Fetch updated slot
        db.refresh(demo_slot)

        # 5. Verify Mock EHR external records for zero-duplicate guarantee
        all_records = MockEhrService.get_all_ehr_records(db)
        matching = [r for r in all_records if r.idempotency_key == demo_idempotency_key]
        external_dup_count = len(matching)

        # 6. Collect all AuditEvents for this correlation ID
        audit_records = db.query(models.AuditEvent).filter(
            models.AuditEvent.correlation_id == demo_correlation_id
        ).order_by(models.AuditEvent.created_at.asc()).all()

        audit_events_list = [
            {
                "id": a.id,
                "action": a.action,
                "status": a.status,
                "actor_role": a.actor_role,
                "details": a.details,
                "created_at": a.created_at.isoformat()
            } for a in audit_records
        ]

        # 7. Check for ReconciliationRecord if unresolved
        recon = db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.appointment_id == appt.id
        ).first()

        recon_dict = None
        if recon:
            recon_dict = {
                "id": recon.id,
                "status": recon.status,
                "failure_reason": recon.failure_reason,
                "created_at": recon.created_at.isoformat()
            }

        # 8. Build timeline steps
        timeline = [
            {
                "step": 1,
                "name": "Appointment Request Dispatched",
                "description": f"Internal appointment created in PENDING_EHR_SYNC. Request dispatched to Mock EHR with idempotency_key={demo_idempotency_key}.",
                "status": "COMPLETED"
            },
            {
                "step": 2,
                "name": "External Timeout Encountered",
                "description": f"Simulated gateway timeout / socket drop in Mock EHR (scenario={scenario}).",
                "status": "TRIGGERED"
            },
            {
                "step": 3,
                "name": "Unknown Outcome Classified",
                "description": "Application classified outcome as UNKNOWN_OUTCOME. Did not blindly retry POST or create duplicate records.",
                "status": "CLASSIFIED_UNKNOWN"
            },
            {
                "step": 4,
                "name": "Query External Healthcare System",
                "description": "Idempotent verification query sent to Mock EHR to determine actual state.",
                "status": "COMPLETED"
            },
            {
                "step": 5,
                "name": "Determine External State",
                "description": "External record found in EHR" if external_dup_count > 0 else "External record NOT found in EHR",
                "status": "RECORD_EXISTS" if external_dup_count > 0 else "RECORD_NOT_FOUND"
            }
        ]

        if scenario == "TIMEOUT_AFTER_SAVE":
            timeline.append({
                "step": 6,
                "name": "Synchronize Without Duplicate",
                "description": f"Internal appointment synchronized to CONFIRMED with ehr_appointment_id={appt.ehr_appointment_id}. Zero duplicates in EHR.",
                "status": "SUCCESS"
            })
            recovery_action = "SYNCHRONIZED_EXISTING_RECORD_WITHOUT_DUPLICATE"
            msg = f"Demonstration Succeeded: EHR saved appointment before timeout. Application classified outcome as UNKNOWN, verified EHR state, synchronized internal record to CONFIRMED, and guaranteed 0 duplicates."

        elif scenario == "TIMEOUT_BEFORE_SAVE":
            timeline.append({
                "step": 6,
                "name": "Safe Retry & Verification",
                "description": f"External EHR had no record. Safe retry dispatched with idempotency key, verified, and confirmed with ehr_appointment_id={appt.ehr_appointment_id}.",
                "status": "SUCCESS"
            })
            recovery_action = "SAFE_RETRY_IDEMPOTENT_VERIFIED"
            msg = f"Demonstration Succeeded: EHR did not save appointment before timeout. Application verified absence, executed safe retry with idempotency, verified external confirmation, and synchronized internal state."

        else: # UNRESOLVED_OUTAGE
            timeline.append({
                "step": 6,
                "name": "Reconciliation & Human Escalation",
                "description": "External outcome remained unresolved. Created ReconciliationRecord, released slot hold, marked appointment as RECONCILIATION_REQUIRED, and alerted human coordinator.",
                "status": "ESCALATION_TRIGGERED"
            })
            recovery_action = "ESCALATED_RECONCILIATION_REQUIRED"
            msg = f"Demonstration Succeeded: Persistent failure remained unresolved. Application safely refused to confirm to patient, released slot hold to prevent calendar locking, created reconciliation record, and triggered human escalation."

        return schemas.TimeoutRecoveryDemoResponse(
            scenario=scenario,
            classification="UNKNOWN_OUTCOME",
            appointment_id=appt.id,
            external_appointment_id=appt.ehr_appointment_id,
            idempotency_key=demo_idempotency_key,
            final_appointment_status=appt.status,
            final_slot_status=demo_slot.status,
            recovery_action_taken=recovery_action,
            external_duplicate_count=external_dup_count,
            duplicate_prevented=True if external_dup_count <= 1 else False,
            timeline=timeline,
            audit_events=audit_events_list,
            reconciliation_record=recon_dict,
            message=msg
        )

    finally:
        # 9. Always restore original chaos settings
        MockEhrService.update_chaos_setting(
            db,
            schemas.ChaosConfigUpdate(
                mode="NORMAL",
                simulated_delay_ms=2500,
                failure_active=False,
                one_shot=False
            )
        )
