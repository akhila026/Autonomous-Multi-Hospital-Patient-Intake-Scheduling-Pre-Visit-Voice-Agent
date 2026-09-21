import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from backend import models, schemas

logger = logging.getLogger(__name__)
from backend.services.slot_service import SlotService
from backend.services.mock_ehr_service import MockEhrService, MockEhrTimeoutException
from backend.ehr.service import EHRIntegrationService
from backend.ehr.mapping import mapping_registry
from backend.auth.tenant import validate_appointment_tenant_isolation
from backend.services.observability_service import ObservabilityService, BookingTraceStage

class AppointmentService:
    @staticmethod
    def validate_tenant_consistency(
        hospital_id: str,
        doctor: models.Doctor,
        patient: models.Patient,
        calendar: Optional[models.Calendar] = None
    ):
        """Service-layer tenant isolation validation ensuring resources belong to the same hospital."""
        return validate_appointment_tenant_isolation(
            hospital_id=hospital_id,
            doctor_hospital_id=doctor.hospital_id if doctor else "",
            patient_hospital_id=patient.primary_hospital_id if patient else None,
            calendar_hospital_id=calendar.hospital_id if calendar else None
        )

    @staticmethod
    def create_slot_hold(db: Session, req: schemas.SlotHoldRequest) -> models.Appointment:
        # Atomic slot hold
        slot = SlotService.hold_slot(db, req.slot_id, hold_minutes=5)

        doctor = db.query(models.Doctor).filter(models.Doctor.id == slot.doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found for slot")
        if doctor.hospital and doctor.hospital.status != "APPROVED":
            raise HTTPException(status_code=400, detail=f"Cannot hold slot in unapproved hospital (status: '{doctor.hospital.status}').")
        if doctor.status != "ACTIVE" or not doctor.is_active:
            raise HTTPException(status_code=400, detail=f"Cannot hold slot for doctor with status '{doctor.status}'. Doctor must be ACTIVE.")

        patient = db.query(models.Patient).filter(models.Patient.id == req.patient_id).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Enforce tenant isolation
        calendar = doctor.calendars[0] if (hasattr(doctor, 'calendars') and doctor.calendars) else None
        AppointmentService.validate_tenant_consistency(
            hospital_id=doctor.hospital_id,
            doctor=doctor,
            patient=patient,
            calendar=calendar
        )

        idempotency_key = str(uuid.uuid4())

        appointment = models.Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            calendar_id=calendar.id if calendar else None,
            slot_id=slot.id,
            hospital_id=doctor.hospital_id,
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
    async def confirm_and_sync_appointment(
        db: Session,
        req: schemas.AppointmentConfirmRequest
    ) -> models.Appointment:
        """
        Orchestrates appointment confirmation and external EHR synchronization.
        Handles Mock EHR timeout failures using idempotent verification recovery.
        """
        idempotency_key = req.idempotency_key or str(uuid.uuid4())

        # 0. Check if appointment already exists with this idempotency key (duplicate request prevention)
        existing_appt = db.query(models.Appointment).filter(
            models.Appointment.idempotency_key == idempotency_key
        ).first()

        # If not found by idempotency key, check if there is an active provisional HELD appointment for this slot
        if not existing_appt and req.slot_id:
            held_appt = db.query(models.Appointment).filter(
                models.Appointment.slot_id == req.slot_id,
                models.Appointment.status == "HELD"
            ).first()
            if held_appt:
                existing_appt = held_appt
                # Update idempotency key to current request key if provided
                if req.idempotency_key:
                    existing_appt.idempotency_key = req.idempotency_key

        if existing_appt and existing_appt.status == "CONFIRMED" and existing_appt.ehr_appointment_id:
            return existing_appt

        # 1. Fetch or hold slot
        slot = db.query(models.TimeSlot).filter(models.TimeSlot.id == req.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found")

        if slot.status == "BOOKED":
            raise HTTPException(status_code=409, detail="This slot has already been booked by someone else.")

        doctor = db.query(models.Doctor).filter(models.Doctor.id == slot.doctor_id).first()
        if not req.patient_id:
            patient = db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first() or db.query(models.Patient).first()
        else:
            patient = db.query(models.Patient).filter(models.Patient.id == req.patient_id).first()
            if not patient:
                patient = db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first() or db.query(models.Patient).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient profile could not be resolved.")
        hospital = db.query(models.Hospital).filter(models.Hospital.id == doctor.hospital_id).first()

        # Enforce service-layer tenant isolation
        calendar = doctor.calendars[0] if (hasattr(doctor, 'calendars') and doctor.calendars) else None
        AppointmentService.validate_tenant_consistency(
            hospital_id=doctor.hospital_id,
            doctor=doctor,
            patient=patient,
            calendar=calendar
        )

        appointment = existing_appt

        # Revalidate the selected slot immediately before booking against the 7 scheduling rules
        from backend.scheduling.service import SchedulingService
        validation = SchedulingService.validate_slot(
            db=db,
            doctor_id=slot.doctor_id,
            slot_id=slot.id,
            appointment_type=getattr(req, "appointment_type", None) or "IN_PERSON",
            ignore_slot_id=slot.id if (slot.status == "HELD" and appointment and appointment.slot_id == slot.id) else None,
            exclude_appointment_id=appointment.id if appointment else None
        )
        if not validation.is_bookable:
            raise HTTPException(status_code=409, detail=f"Slot is not bookable: {validation.reason}")

        if not appointment:
            appointment = models.Appointment(
                patient_id=patient.id,
                doctor_id=doctor.id,
                calendar_id=calendar.id if calendar else None,
                slot_id=slot.id,
                hospital_id=doctor.hospital_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                appointment_type=getattr(req, "appointment_type", None) or "IN_PERSON",
                status="PENDING_EHR_SYNC",
                idempotency_key=idempotency_key,
                chief_complaint=req.chief_complaint,
                urgency_level=req.urgency_level,
                ai_triage_notes=req.ai_triage_notes
            )
            db.add(appointment)
            db.commit()
            db.refresh(appointment)
        else:
            if not appointment.start_time:
                appointment.start_time = slot.start_time
            if not appointment.end_time:
                appointment.end_time = slot.end_time
            if not appointment.calendar_id and calendar:
                appointment.calendar_id = calendar.id
            if not appointment.hospital_id:
                appointment.hospital_id = doctor.hospital_id
            appointment.status = "PENDING_EHR_SYNC"
            db.commit()

        # Mark slot as held while sync is executing
        slot.status = "HELD"
        slot.hold_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db.commit()

        # Define correlation ID for complete end-to-end trace
        correlation_id = getattr(req, "correlation_id", None) or f"TX-{uuid.uuid4().hex[:10]}"

        # Stage 4: SCHEDULING Trace Record
        ObservabilityService.record_stage(
            correlation_id=correlation_id,
            stage=BookingTraceStage.SCHEDULING,
            status="COMPLETED",
            details={
                "slot_id": slot.id,
                "appointment_id": appointment.id,
                "doctor_id": doctor.id,
                "start_time": str(slot.start_time),
                "status": "HELD"
            },
            actor_id=patient.id,
            actor_role="PATIENT",
            tenant_id=appointment.hospital_id,
            db=db
        )

        # Audit Event 1: Outbound Dispatch
        db.add(models.AuditEvent(
            correlation_id=correlation_id,
            hospital_id=appointment.hospital_id,
            actor_id=patient.id,
            actor_role="PATIENT",
            action="APPOINTMENT_REQUEST_DISPATCHED",
            resource_type="APPOINTMENT",
            resource_id=appointment.id,
            status="PENDING_EHR_SYNC",
            details={
                "idempotency_key": idempotency_key,
                "doctor_id": doctor.id,
                "slot_id": slot.id
            }
        ))
        db.commit()

        # 2. Dispatch to External Healthcare System via Integration Service
        sync_success = False
        external_ehr_id = None
        failure_reason = None
        recovery_action_note = None

        from backend.ehr.exceptions import (
            MockEhrTimeoutException, EHRAuthenticationException, EHRRateLimitException,
            EHROutageException, EHRValidationException, EHRNetworkException, EHRIntegrationException
        )

        from backend.workflows.events import event_dispatcher, DomainEvent, EventType
        try:
            # Emit EHR_OPERATION_STARTED
            await event_dispatcher.dispatch(DomainEvent(
                event_type=EventType.EHR_OPERATION_STARTED,
                entity_id=appointment.id,
                tenant_id=appointment.hospital_id,
                correlation_id=correlation_id,
                payload={"idempotency_key": idempotency_key, "action": "CREATE_APPOINTMENT"}
            ))

            # 2a. Dispatch appointment creation through EHR integration layer
            sync_res = await EHRIntegrationService.sync_appointment_to_ehr(
                db=db, appointment=appointment, correlation_id=correlation_id
            )
            
            # Step 7 & 8: DO NOT TREAT THE EXTERNAL RESPONSE ALONE AS CONFIRMATION!
            # Call explicit idempotent verification query against the external system.
            verify_res = EHRIntegrationService.verify_appointment_in_ehr(
                db=db,
                idempotency_key=idempotency_key,
                appointment_id=appointment.id,
                hospital_id=appointment.hospital_id,
                correlation_id=correlation_id
            )

            if verify_res.found and (verify_res.external_appointment_id or verify_res.ehr_appointment_id):
                external_ehr_id = verify_res.external_appointment_id or verify_res.ehr_appointment_id
                sync_success = True

                # Stage 5: EHR_OPERATION (Success)
                ObservabilityService.record_stage(
                    correlation_id=correlation_id,
                    stage=BookingTraceStage.EHR_OPERATION,
                    status="COMPLETED",
                    details={"idempotency_key": idempotency_key, "action": "CREATE_APPOINTMENT", "external_id": external_ehr_id},
                    actor_id="INTEGRATION_CONNECTOR",
                    actor_role="SYSTEM",
                    tenant_id=appointment.hospital_id,
                    db=db
                )
                # Stage 6: VERIFICATION (Success)
                ObservabilityService.record_stage(
                    correlation_id=correlation_id,
                    stage=BookingTraceStage.VERIFICATION,
                    status="COMPLETED",
                    details={"external_ehr_id": external_ehr_id, "found": True, "verification_type": "EXPLICIT_QUERY"},
                    actor_id="VERIFICATION_SERVICE",
                    actor_role="SYSTEM",
                    tenant_id=appointment.hospital_id,
                    db=db
                )

                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.EXTERNAL_VERIFICATION_COMPLETED,
                    entity_id=appointment.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={"external_ehr_id": external_ehr_id, "found": True}
                ))
                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.EHR_OPERATION_COMPLETED,
                    entity_id=appointment.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={"external_ehr_id": external_ehr_id, "status": "CONFIRMED"}
                ))
            else:
                # External response was returned but verification query could not find it!
                failure_reason = "EHR response received but external verification check returned NOT_FOUND"
                sync_success = False

                ObservabilityService.record_stage(
                    correlation_id=correlation_id,
                    stage=BookingTraceStage.VERIFICATION,
                    status="FAILED",
                    details={"failure_reason": failure_reason, "found": False},
                    actor_id="VERIFICATION_SERVICE",
                    actor_role="SYSTEM",
                    tenant_id=appointment.hospital_id,
                    db=db
                )
                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.EHR_OPERATION_FAILED,
                    entity_id=appointment.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={"failure_reason": failure_reason}
                ))

        except MockEhrTimeoutException as timeout_ex:
            # === CRITICAL TIMEOUT RECOVERY FLOW ===
            # Step 1: External timeout detected -> The main application MUST classify this as an UNKNOWN outcome!
            # DO NOT blindly create another appointment!
            appointment.status = "UNKNOWN_OUTCOME"
            db.commit()

            # Record Audit Event: EXTERNAL_TIMEOUT_DETECTED (classified as UNKNOWN_OUTCOME)
            db.add(models.AuditEvent(
                correlation_id=correlation_id,
                hospital_id=appointment.hospital_id,
                actor_id="SYSTEM",
                actor_role="INTEGRATION_ENGINE",
                action="EXTERNAL_TIMEOUT_DETECTED",
                resource_type="APPOINTMENT",
                resource_id=appointment.id,
                status="UNKNOWN_OUTCOME",
                details={
                    "classification": "UNKNOWN_OUTCOME",
                    "idempotency_key": idempotency_key,
                    "error": str(timeout_ex),
                    "policy": "DO_NOT_BLINDLY_CREATE_APPOINTMENT_INITIATE_RECOVERY"
                }
            ))
            db.commit()

            # Step 2: Query external healthcare system to determine actual external state
            db.add(models.AuditEvent(
                correlation_id=correlation_id,
                hospital_id=appointment.hospital_id,
                actor_id="SYSTEM",
                actor_role="INTEGRATION_ENGINE",
                action="QUERY_EXTERNAL_STATE",
                resource_type="APPOINTMENT",
                resource_id=appointment.id,
                status="DISPATCHED",
                details={
                    "idempotency_key": idempotency_key,
                    "query_type": "IDEMPOTENT_VERIFICATION"
                }
            ))
            db.commit()

            verify_res = EHRIntegrationService.verify_appointment_in_ehr(
                db=db,
                idempotency_key=idempotency_key,
                appointment_id=appointment.id,
                hospital_id=appointment.hospital_id,
                correlation_id=correlation_id
            )

            # Step 3: Determine actual external state
            if verify_res.found and (verify_res.external_appointment_id or verify_res.ehr_appointment_id):
                # CASE A: If the appointment exists:
                # - synchronize the internal appointment
                # - verify the external appointment
                # - complete the booking safely
                # - confirm to the patient
                external_ehr_id = verify_res.external_appointment_id or verify_res.ehr_appointment_id
                sync_success = True
                recovery_action_note = "RECONCILED_WITHOUT_DUPLICATE"

                db.add(models.AuditEvent(
                    correlation_id=correlation_id,
                    hospital_id=appointment.hospital_id,
                    actor_id="SYSTEM",
                    actor_role="INTEGRATION_ENGINE",
                    action="DETERMINE_EXTERNAL_STATE",
                    resource_type="APPOINTMENT",
                    resource_id=appointment.id,
                    status="RECORD_EXISTS",
                    details={
                        "idempotency_key": idempotency_key,
                        "external_appointment_id": external_ehr_id,
                        "external_status": verify_res.status,
                        "resolution_plan": "SYNCHRONIZE_WITHOUT_DUPLICATE"
                    }
                ))
                db.commit()

            else:
                # CASE B: If the appointment does not exist:
                # - retry safely using the appropriate idempotency/reference mechanism
                # - verify the result
                # - synchronize the internal state
                db.add(models.AuditEvent(
                    correlation_id=correlation_id,
                    hospital_id=appointment.hospital_id,
                    actor_id="SYSTEM",
                    actor_role="INTEGRATION_ENGINE",
                    action="DETERMINE_EXTERNAL_STATE",
                    resource_type="APPOINTMENT",
                    resource_id=appointment.id,
                    status="RECORD_NOT_FOUND",
                    details={
                        "idempotency_key": idempotency_key,
                        "resolution_plan": "SAFE_RETRY_WITH_IDEMPOTENCY"
                    }
                ))
                db.commit()

                appointment.retry_count += 1
                db.add(models.AuditEvent(
                    correlation_id=correlation_id,
                    hospital_id=appointment.hospital_id,
                    actor_id="SYSTEM",
                    actor_role="INTEGRATION_ENGINE",
                    action="RETRY_APPOINTMENT_CREATION",
                    resource_type="APPOINTMENT",
                    resource_id=appointment.id,
                    status="DISPATCHED",
                    details={
                        "retry_count": appointment.retry_count,
                        "idempotency_key": idempotency_key
                    }
                ))
                db.commit()

                try:
                    retry_res = await EHRIntegrationService.sync_appointment_to_ehr(
                        db=db, appointment=appointment, correlation_id=correlation_id
                    )
                    # verify the result
                    post_retry_verif = EHRIntegrationService.verify_appointment_in_ehr(
                        db=db,
                        idempotency_key=idempotency_key,
                        appointment_id=appointment.id,
                        hospital_id=appointment.hospital_id,
                        correlation_id=correlation_id
                    )
                    if post_retry_verif.found and (post_retry_verif.external_appointment_id or post_retry_verif.ehr_appointment_id):
                        external_ehr_id = post_retry_verif.external_appointment_id or post_retry_verif.ehr_appointment_id
                        sync_success = True
                        recovery_action_note = "RETRY_VERIFIED_SUCCESS"
                    else:
                        failure_reason = "Retry completed but external verification returned NOT_FOUND"
                        sync_success = False
                except Exception as retry_err:
                    failure_reason = f"Retry creation failed: {str(retry_err)}"
                    sync_success = False

        except (EHRAuthenticationException, EHRRateLimitException, EHROutageException,
                EHRValidationException, EHRNetworkException, EHRIntegrationException, Exception) as ex:
            # === UNRECOVERABLE / FATAL FAILURE (PRD Section 13 & 28 Option C) ===
            failure_reason = f"{type(ex).__name__}: {str(ex)}"
            sync_success = False

        # 3. Finalize internal appointment & slot state
        if sync_success and external_ehr_id:
            # Step 9: Only after successful external verification:
            # - synchronize the internal appointment state
            # - mark the appointment as confirmed
            appointment.status = "CONFIRMED"
            appointment.ehr_appointment_id = external_ehr_id
            if not appointment.start_time and slot:
                appointment.start_time = slot.start_time
            if not appointment.end_time and slot:
                appointment.end_time = slot.end_time
            slot.status = "BOOKED"
            slot.hold_expires_at = None

            # Maintain Internal Appointment <-> External Appointment bidirectional mapping
            mapping_registry.map_appointment(
                internal_id=appointment.id,
                external_id=external_ehr_id,
                hospital_id=appointment.hospital_id,
                db=db,
                system_name="MOCK_EHR"
            )

            # Stage 7: SYNCHRONIZATION Trace Record
            ObservabilityService.record_stage(
                correlation_id=correlation_id,
                stage=BookingTraceStage.SYNCHRONIZATION,
                status="COMPLETED",
                details={
                    "appointment_id": appointment.id,
                    "ehr_appointment_id": external_ehr_id,
                    "slot_id": slot.id,
                    "status": "CONFIRMED"
                },
                actor_id=patient.id,
                actor_role="PATIENT",
                tenant_id=appointment.hospital_id,
                db=db
            )

            # Record Audit Event: Confirmation to patient
            confirm_status = recovery_action_note or "SUCCESS"
            db.add(models.AuditEvent(
                correlation_id=correlation_id,
                hospital_id=appointment.hospital_id,
                actor_id=patient.id,
                actor_role="PATIENT",
                action="APPOINTMENT_CONFIRMED",
                resource_type="APPOINTMENT",
                resource_id=appointment.id,
                status=confirm_status,
                details={
                    "ehr_appointment_id": external_ehr_id,
                    "idempotency_key": idempotency_key,
                    "recovery_action": recovery_action_note,
                    "retry_count": appointment.retry_count
                }
            ))

            # 4. Generate & Assign Pre-Visit Questionnaire through QuestionnaireService
            from backend.services.questionnaire_service import QuestionnaireService
            QuestionnaireService.assign_questionnaire_to_appointment(db, appointment)

            # 5. Schedule Automated Reminders
            AppointmentService._schedule_reminders(db, appointment, slot.start_time)

            db.commit()
            db.refresh(appointment)

            # 6. Dispatch Domain Event: APPOINTMENT_BOOKED
            try:
                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.APPOINTMENT_BOOKED,
                    entity_id=appointment.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={
                        "appointment_id": appointment.id,
                        "hospital_id": appointment.hospital_id,
                        "doctor_id": appointment.doctor_id,
                        "doctor_name": doctor.full_name if doctor else "Doctor",
                        "patient_name": patient.name if patient else "Patient",
                        "patient_phone": patient.phone if patient else "+1-555-0100",
                        "slot_time": str(slot.start_time),
                        "ehr_appointment_id": external_ehr_id,
                        "chief_complaint": appointment.chief_complaint
                    }
                ))
            except Exception as ev_err:
                logger.warning(f"Failed to dispatch APPOINTMENT_BOOKED event: {ev_err}")

            return appointment
        else:
            # Step 10: If outcome remains unresolved:
            # - create a reconciliation record
            # - mark the operation as requiring reconciliation
            # - do not confirm the appointment
            # - allow human escalation
            appointment.status = "RECONCILIATION_REQUIRED"
            slot.status = "AVAILABLE"  # Release slot so calendar is not permanently locked
            slot.hold_expires_at = None

            # Create Reconciliation Record (PRD Section 13 & 22)
            recon = models.ReconciliationRecord(
                hospital_id=appointment.hospital_id,
                appointment_id=appointment.id,
                idempotency_key=idempotency_key,
                failure_reason=failure_reason or "Outcome remained unresolved after verification/retry",
                status="RECONCILIATION_REQUIRED"
            )
            db.add(recon)

            # Record Audit Event: Escalation Triggered
            db.add(models.AuditEvent(
                correlation_id=correlation_id,
                hospital_id=appointment.hospital_id,
                actor_id="SYSTEM",
                actor_role="INTEGRATION_ENGINE",
                action="CREATE_RECONCILIATION_RECORD",
                resource_type="APPOINTMENT",
                resource_id=appointment.id,
                status="ESCALATION_TRIGGERED",
                details={
                    "idempotency_key": idempotency_key,
                    "failure_reason": failure_reason,
                    "slot_released": True,
                    "human_escalation_required": True
                }
            ))
            db.commit()
            db.refresh(appointment)

            # Dispatch Domain Events: RECONCILIATION_REQUIRED & HUMAN_ESCALATION
            try:
                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.RECONCILIATION_REQUIRED,
                    entity_id=recon.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={
                        "appointment_id": appointment.id,
                        "hospital_id": appointment.hospital_id,
                        "failure_reason": recon.failure_reason,
                        "idempotency_key": idempotency_key
                    }
                ))
                await event_dispatcher.dispatch(DomainEvent(
                    event_type=EventType.HUMAN_ESCALATION,
                    entity_id=recon.id,
                    tenant_id=appointment.hospital_id,
                    correlation_id=correlation_id,
                    payload={
                        "reason": f"EHR synchronization failed: {recon.failure_reason}",
                        "appointment_id": appointment.id,
                        "reconciliation_id": recon.id
                    }
                ))
            except Exception as ev_err:
                logger.warning(f"Failed to dispatch RECONCILIATION_REQUIRED event: {ev_err}")

            return appointment

    @staticmethod
    def _build_default_questions(chief_complaint: str, specialty: str) -> list:
        return [
            {
                "id": "q1",
                "question": f"How long have you been experiencing these symptoms related to '{chief_complaint}'?",
                "type": "choice",
                "options": ["Less than 24 hours", "2-3 days", "1-2 weeks", "More than a month"]
            },
            {
                "id": "q2",
                "question": "On a scale of 1 to 10, how severe is your discomfort right now?",
                "type": "scale",
                "min": 1,
                "max": 10
            },
            {
                "id": "q3",
                "question": "Have you taken any over-the-counter or prescribed medications for this?",
                "type": "text",
                "placeholder": "e.g. Ibuprofen 400mg, ice pack, etc."
            },
            {
                "id": "q4",
                "question": f"Do you have any known allergies or chronic conditions relevant to {specialty}?",
                "type": "text",
                "placeholder": "e.g. Penicillin allergy, hypertension, none"
            }
        ]

    @staticmethod
    def _schedule_reminders(db: Session, appointment: models.Appointment, slot_start: datetime):
        # 1. Instant Confirmation reminder
        r1 = models.ReminderLog(
            appointment_id=appointment.id,
            reminder_type="CONFIRMATION",
            scheduled_for=datetime.utcnow(),
            status="SENT",
            channel="SMS",
            message_content=f"Appointment Confirmed with {appointment.doctor.full_name} for {slot_start.strftime('%b %d at %I:%M %p')}. Ref: {appointment.ehr_appointment_id}",
            sent_at=datetime.utcnow()
        )
        # 2. 24h Pre-visit reminder
        r2 = models.ReminderLog(
            appointment_id=appointment.id,
            reminder_type="PRE_VISIT_24H",
            scheduled_for=slot_start - timedelta(hours=24),
            status="SCHEDULED",
            channel="SMS",
            message_content="Reminder: Upcoming consultation tomorrow. Please complete your pre-visit health questionnaire."
        )
        # 3. 1h reminder
        r3 = models.ReminderLog(
            appointment_id=appointment.id,
            reminder_type="PRE_VISIT_1H",
            scheduled_for=slot_start - timedelta(hours=1),
            status="SCHEDULED",
            channel="SMS",
            message_content="Your consultation is in 1 hour. Please be ready 5 minutes prior."
        )
        db.add_all([r1, r2, r3])

    @staticmethod
    def get_appointment_by_id(db: Session, appointment_id: str) -> models.Appointment:
        appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        return appt

    @staticmethod
    def get_doctor_queue(db: Session, doctor_id: str) -> List[models.Appointment]:
        return db.query(models.Appointment).filter(
            models.Appointment.doctor_id == doctor_id
        ).order_by(models.Appointment.created_at.desc()).all()

    @staticmethod
    def create_appointment(
        db: Session,
        hospital_id: str,
        doctor_id: str,
        patient_id: str,
        calendar_id: Optional[str] = None,
        slot_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        status: str = "CONFIRMED",
        chief_complaint: Optional[str] = None,
        urgency_level: str = "ROUTINE",
        idempotency_key: Optional[str] = None,
        ai_triage_notes: Optional[str] = None,
        ehr_appointment_id: Optional[str] = None
    ) -> models.Appointment:
        """
        Creates an appointment with strict service-layer tenant isolation validation.
        Enforces:
        - appointment.hospital_id == doctor.hospital_id
        - appointment.hospital_id == patient.hospital_id (where applicable)
        - appointment.hospital_id == calendar.hospital_id (where applicable)
        Rejects cross-hospital assignments with a clear validation error.
        """
        doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        calendar = None
        if calendar_id:
            calendar = db.query(models.Calendar).filter(models.Calendar.id == calendar_id).first()
            if not calendar:
                raise HTTPException(status_code=404, detail="Calendar not found")

        # Service-layer tenant validation
        AppointmentService.validate_tenant_consistency(
            hospital_id=hospital_id,
            doctor=doctor,
            patient=patient,
            calendar=calendar
        )

        appointment = models.Appointment(
            hospital_id=hospital_id,
            doctor_id=doctor_id,
            patient_id=patient_id,
            calendar_id=calendar_id,
            slot_id=slot_id,
            start_time=start_time,
            end_time=end_time,
            status=status,
            chief_complaint=chief_complaint,
            urgency_level=urgency_level,
            ai_triage_notes=ai_triage_notes,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            ehr_appointment_id=ehr_appointment_id
        )
        db.add(appointment)
        db.commit()
        db.refresh(appointment)
        return appointment

    @staticmethod
    def update_appointment(
        db: Session,
        appointment_id: str,
        hospital_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        calendar_id: Optional[str] = None,
        **updates
    ) -> models.Appointment:
        """
        Updates an appointment with strict service-layer tenant isolation validation.
        Enforces:
        - appointment.hospital_id == doctor.hospital_id
        - appointment.hospital_id == patient.hospital_id (where applicable)
        - appointment.hospital_id == calendar.hospital_id (where applicable)
        Rejects cross-hospital assignments with a clear validation error.
        """
        appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        target_hospital_id = hospital_id or appointment.hospital_id
        target_doctor_id = doctor_id or appointment.doctor_id
        target_patient_id = patient_id or appointment.patient_id
        target_calendar_id = calendar_id if calendar_id is not None else appointment.calendar_id

        doctor = db.query(models.Doctor).filter(models.Doctor.id == target_doctor_id).first()
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        patient = db.query(models.Patient).filter(models.Patient.id == target_patient_id).first()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        calendar = None
        if target_calendar_id:
            calendar = db.query(models.Calendar).filter(models.Calendar.id == target_calendar_id).first()
            if not calendar:
                raise HTTPException(status_code=404, detail="Calendar not found")

        # Service-layer tenant validation
        AppointmentService.validate_tenant_consistency(
            hospital_id=target_hospital_id,
            doctor=doctor,
            patient=patient,
            calendar=calendar
        )

        # Apply updates
        appointment.hospital_id = target_hospital_id
        appointment.doctor_id = target_doctor_id
        appointment.patient_id = target_patient_id
        appointment.calendar_id = target_calendar_id
        for k, v in updates.items():
            if hasattr(appointment, k):
                setattr(appointment, k, v)

        db.commit()
        db.refresh(appointment)
        return appointment

    @staticmethod
    def update_appointment_status(
        db: Session,
        appointment_id: str,
        new_status: str,
        notes: Optional[str] = None,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None
    ) -> models.Appointment:
        """
        Transitions appointment through its explicit PRD lifecycle:
        REQUESTED, PENDING, CONFIRMED, RESCHEDULED, CANCELLED, COMPLETED, NO_SHOW, FAILED, SYNC_PENDING, RECONCILIATION_REQUIRED.
        """
        allowed_statuses = [
            "REQUESTED", "PENDING", "CONFIRMED", "RESCHEDULED", "CANCELLED",
            "COMPLETED", "NO_SHOW", "FAILED", "SYNC_PENDING", "RECONCILIATION_REQUIRED"
        ]
        status_upper = new_status.upper()
        if status_upper not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status}'. Allowed: {', '.join(allowed_statuses)}"
            )

        appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        old_status = appointment.status
        appointment.status = status_upper
        appointment.updated_at = datetime.utcnow()

        # Slot state synchronization
        if status_upper in ("CANCELLED", "FAILED") and appointment.slot:
            appointment.slot.status = "AVAILABLE"
            appointment.slot.hold_expires_at = None
        elif status_upper in ("CONFIRMED", "RESCHEDULED") and appointment.slot:
            appointment.slot.status = "BOOKED"

        # Audit Event
        db.add(models.AuditEvent(
            correlation_id=str(uuid.uuid4()),
            hospital_id=appointment.hospital_id,
            actor_id=actor_id or "SYSTEM",
            actor_role=actor_role or "SYSTEM",
            action="APPOINTMENT_STATUS_UPDATED",
            resource_type="APPOINTMENT",
            resource_id=appointment.id,
            status="SUCCESS",
            details={
                "from_status": old_status,
                "to_status": status_upper,
                "notes": notes
            }
        ))
        db.commit()
        db.refresh(appointment)
        return appointment

    @staticmethod
    def mark_completed(db: Session, appointment_id: str, notes: Optional[str] = None) -> models.Appointment:
        return AppointmentService.update_appointment_status(db, appointment_id, "COMPLETED", notes=notes)

    @staticmethod
    def mark_no_show(db: Session, appointment_id: str, notes: Optional[str] = None) -> models.Appointment:
        return AppointmentService.update_appointment_status(db, appointment_id, "NO_SHOW", notes=notes)

    @staticmethod
    def reconcile_appointment(
        db: Session,
        appointment_id: str,
        external_status: Optional[str] = None,
        resolution_notes: Optional[str] = None
    ) -> models.Appointment:
        """
        Reconciles appointment state with external EHR record.
        Resolves unknown states into CONFIRMED, CANCELLED, or creates a ReconciliationRecord if manual review needed.
        """
        appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        target_status = external_status.upper() if external_status else None
        if not target_status:
            # Check external EHR verification if record exists
            verification = db.query(models.Verification).filter(
                models.Verification.appointment_id == appointment.id
            ).order_by(models.Verification.created_at.desc()).first()
            if verification and verification.status == "FOUND":
                target_status = "CONFIRMED"
            elif verification and verification.status == "NOT_FOUND":
                target_status = "FAILED"
            else:
                target_status = "RECONCILIATION_REQUIRED"

        # Record reconciliation record if needed
        recon = db.query(models.ReconciliationRecord).filter(
            models.ReconciliationRecord.appointment_id == appointment.id
        ).first()
        if not recon:
            recon = models.ReconciliationRecord(
                hospital_id=appointment.hospital_id,
                appointment_id=appointment.id,
                idempotency_key=appointment.idempotency_key,
                failure_reason=resolution_notes or f"Reconciled state to {target_status}",
                status="RECONCILED" if target_status in ("CONFIRMED", "CANCELLED") else "RECONCILIATION_REQUIRED",
                resolution_notes=resolution_notes or f"External status resolved as {target_status}",
                resolved_at=datetime.utcnow() if target_status in ("CONFIRMED", "CANCELLED") else None
            )
            db.add(recon)
        else:
            recon.status = "RECONCILED" if target_status in ("CONFIRMED", "CANCELLED") else "RECONCILIATION_REQUIRED"
            recon.resolution_notes = resolution_notes or f"Updated external status to {target_status}"
            if target_status in ("CONFIRMED", "CANCELLED"):
                recon.resolved_at = datetime.utcnow()

        appointment.status = target_status
        appointment.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(appointment)
        return appointment

