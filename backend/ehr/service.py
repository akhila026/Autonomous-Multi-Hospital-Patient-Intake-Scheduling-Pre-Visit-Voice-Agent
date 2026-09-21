import time
import uuid
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from backend import models
from backend.ehr.connector import (
    connector_registry, EHRAppointmentPayload, EHRVerificationResult
)
from backend.ehr.mapping import mapping_registry
from backend.ehr.exceptions import MockEhrTimeoutException


class EHRIntegrationService:
    """
    High-Level EHR Integration Service (PRD Section 12 & 22).
    Hides all vendor-specific healthcare system integration details.
    Coordinates between the HealthcareSystemConnector, bidirectional identifier
    mappings, and audit/operation logging.
    """

    @staticmethod
    def get_connector(system_name: str = "MOCK_EHR"):
        return connector_registry.get(system_name)

    @staticmethod
    async def sync_appointment_to_ehr(
        db: Session,
        appointment: models.Appointment,
        correlation_id: Optional[str] = None,
        system_name: str = "MOCK_EHR"
    ) -> Dict[str, Any]:
        """
        Dispatches appointment creation to the configured external healthcare system,
        maintains identifier mappings, and records outbound IntegrationOperation logs.
        """
        connector = EHRIntegrationService.get_connector(system_name)
        start_ms = int(time.time() * 1000)

        # 1. Resolve External Identifier Mappings
        patient = appointment.patient
        doctor = appointment.doctor
        hospital = appointment.hospital

        ext_patient_id = (
            mapping_registry.get_external_patient_id(appointment.patient_id, hospital_id=appointment.hospital_id, db=db, system_name=system_name) or
            (patient.external_patient_id if patient else None) or
            "P-101"
        )
        ext_provider_id = (
            mapping_registry.get_external_doctor_id(appointment.doctor_id, hospital_id=appointment.hospital_id, db=db, system_name=system_name) or
            (doctor.external_provider_id if doctor else None) or
            "DOC-501"
        )
        ext_facility_id = (
            mapping_registry.get_external_facility_id(appointment.hospital_id, hospital_id=appointment.hospital_id, db=db, system_name=system_name) or
            "FAC-101"
        )

        slot_time_str = appointment.slot.start_time.strftime("%Y-%m-%d %H:%M") if (appointment.slot and appointment.slot.start_time) else (
            appointment.start_time.strftime("%Y-%m-%d %H:%M") if appointment.start_time else "2026-10-01 10:00"
        )

        payload = EHRAppointmentPayload(
            external_patient_id=ext_patient_id,
            external_provider_id=ext_provider_id,
            patient_name=patient.full_name if patient else "Patient",
            doctor_name=doctor.full_name if doctor else "Doctor",
            hospital_name=hospital.name if hospital else "Hospital",
            slot_time=slot_time_str,
            idempotency_key=appointment.idempotency_key,
            chief_complaint=appointment.chief_complaint
        )

        try:
            result = await connector.create_appointment(payload)
            elapsed_ms = int(time.time() * 1000) - start_ms

            ext_appt_id = result.get("id") or result.get("external_appointment_id") or f"EHR-{uuid.uuid4().hex[:6].upper()}"

            # 2. Persist Bidirectional Identifier Mappings
            mapping_registry.map_appointment(
                internal_id=appointment.id,
                external_id=ext_appt_id,
                hospital_id=appointment.hospital_id,
                db=db,
                system_name=system_name
            )
            mapping_registry.map_patient(
                internal_id=appointment.patient_id,
                external_id=ext_patient_id,
                hospital_id=appointment.hospital_id,
                db=db,
                system_name=system_name
            )
            mapping_registry.map_doctor(
                internal_id=appointment.doctor_id,
                external_id=ext_provider_id,
                hospital_id=appointment.hospital_id,
                db=db,
                system_name=system_name
            )
            mapping_registry.map_facility(
                internal_id=appointment.hospital_id,
                external_id=ext_facility_id,
                hospital_id=appointment.hospital_id,
                db=db,
                system_name=system_name
            )

            # 3. Record Outbound Integration Operation & EhrSyncLog
            op = models.IntegrationOperation(
                hospital_id=appointment.hospital_id,
                appointment_id=appointment.id,
                system_name=system_name,
                operation_type="CREATE_APPOINTMENT",
                idempotency_key=appointment.idempotency_key,
                status="SUCCESS",
                request_payload=payload.model_dump_json(),
                response_payload=f"EHR ID: {ext_appt_id}",
                response_time_ms=elapsed_ms,
                correlation_id=correlation_id
            )
            db.add(op)

            sync_log = models.EhrSyncLog(
                appointment_id=appointment.id,
                idempotency_key=appointment.idempotency_key,
                action="CREATE",
                status="SUCCESS",
                request_payload=payload.model_dump_json(),
                response_payload=f"EHR ID: {ext_appt_id}",
                response_time_ms=elapsed_ms
            )
            db.add(sync_log)
            db.commit()

            return result

        except MockEhrTimeoutException as ex:
            elapsed_ms = int(time.time() * 1000) - start_ms
            op = models.IntegrationOperation(
                hospital_id=appointment.hospital_id,
                appointment_id=appointment.id,
                system_name=system_name,
                operation_type="CREATE_APPOINTMENT",
                idempotency_key=appointment.idempotency_key,
                status="TIMEOUT",
                request_payload=payload.model_dump_json(),
                response_time_ms=elapsed_ms,
                error_message=str(ex),
                correlation_id=correlation_id
            )
            db.add(op)

            sync_log = models.EhrSyncLog(
                appointment_id=appointment.id,
                idempotency_key=appointment.idempotency_key,
                action="CREATE",
                status="TIMEOUT",
                request_payload=payload.model_dump_json(),
                response_time_ms=elapsed_ms,
                error_message=str(ex)
            )
            db.add(sync_log)
            db.commit()
            raise ex

        except Exception as ex:
            from backend.ehr.exceptions import (
                EHRAuthenticationException, EHRRateLimitException, EHROutageException, EHRValidationException
            )
            elapsed_ms = int(time.time() * 1000) - start_ms
            status_code = "FAILED"
            if isinstance(ex, EHRAuthenticationException):
                status_code = "AUTH_ERROR"
            elif isinstance(ex, EHRRateLimitException):
                status_code = "RATE_LIMITED"
            elif isinstance(ex, EHROutageException):
                status_code = "OUTAGE"
            elif isinstance(ex, EHRValidationException):
                status_code = "VALIDATION_ERROR"

            op = models.IntegrationOperation(
                hospital_id=appointment.hospital_id,
                appointment_id=appointment.id,
                system_name=system_name,
                operation_type="CREATE_APPOINTMENT",
                idempotency_key=appointment.idempotency_key,
                status=status_code,
                request_payload=payload.model_dump_json(),
                response_time_ms=elapsed_ms,
                error_message=str(ex),
                correlation_id=correlation_id
            )
            db.add(op)

            sync_log = models.EhrSyncLog(
                appointment_id=appointment.id,
                idempotency_key=appointment.idempotency_key,
                action="CREATE",
                status=status_code,
                request_payload=payload.model_dump_json(),
                response_time_ms=elapsed_ms,
                error_message=str(ex)
            )
            db.add(sync_log)
            db.commit()
            raise ex

    @staticmethod
    def verify_appointment_in_ehr(
        db: Session,
        idempotency_key: str,
        appointment_id: Optional[str] = None,
        hospital_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        system_name: str = "MOCK_EHR"
    ) -> EHRVerificationResult:
        """
        Executes external verification query through the connector without accessing external DB.
        Records Verification record and updates identifier mappings if found.
        """
        connector = EHRIntegrationService.get_connector(system_name)
        start_ms = int(time.time() * 1000)

        # Check synchronous or async
        if hasattr(connector, "verify_appointment_sync"):
            result = connector.verify_appointment_sync(idempotency_key)
        else:
            import asyncio
            result = asyncio.run(connector.verify_appointment(idempotency_key))

        elapsed_ms = int(time.time() * 1000) - start_ms

        if result.found and appointment_id and hospital_id:
            ext_id = result.external_appointment_id or result.ehr_appointment_id

            # Update mapping
            if ext_id:
                mapping_registry.map_appointment(
                    internal_id=appointment_id,
                    external_id=ext_id,
                    hospital_id=hospital_id,
                    db=db,
                    system_name=system_name
                )

            # Record Verification entity
            verif_rec = models.Verification(
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                idempotency_key=idempotency_key,
                external_appointment_id=ext_id,
                status="FOUND",
                details=result.details
            )
            db.add(verif_rec)

            op = models.IntegrationOperation(
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                system_name=system_name,
                operation_type="VERIFY",
                idempotency_key=idempotency_key,
                status="SUCCESS",
                response_payload=f"Verified external existence: {ext_id}",
                response_time_ms=elapsed_ms,
                correlation_id=correlation_id
            )
            db.add(op)

            sync_log = models.EhrSyncLog(
                appointment_id=appointment_id,
                idempotency_key=idempotency_key,
                action="VERIFY_RECOVERY",
                status="SUCCESS",
                request_payload=f"Query verification for key={idempotency_key}",
                response_payload=f"Verified external existence: {ext_id}",
                response_time_ms=elapsed_ms
            )
            db.add(sync_log)
            db.commit()

        elif appointment_id and hospital_id:
            verif_rec = models.Verification(
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                idempotency_key=idempotency_key,
                external_appointment_id=None,
                status="NOT_FOUND",
                details=result.details
            )
            db.add(verif_rec)

            op = models.IntegrationOperation(
                hospital_id=hospital_id,
                appointment_id=appointment_id,
                system_name=system_name,
                operation_type="VERIFY",
                idempotency_key=idempotency_key,
                status="NOT_FOUND",
                response_payload="External record not found during verification",
                response_time_ms=elapsed_ms,
                correlation_id=correlation_id
            )
            db.add(op)

            sync_log = models.EhrSyncLog(
                appointment_id=appointment_id,
                idempotency_key=idempotency_key,
                action="VERIFY_QUERY",
                status="NOT_FOUND",
                request_payload=f"Query verification for key={idempotency_key}",
                response_payload="Not found in external system",
                response_time_ms=elapsed_ms
            )
            db.add(sync_log)
            db.commit()

        return result
