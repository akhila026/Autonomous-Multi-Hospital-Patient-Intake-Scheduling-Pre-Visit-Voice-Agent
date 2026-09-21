import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend import schemas
from backend.ehr.connector import mock_ehr_connector, EHRAppointmentPayload, EHRVerificationResult


from backend.ehr.exceptions import MockEhrTimeoutException, EHRIntegrationException


class MockEhrRecordDTO:
    """Lightweight DTO for external Mock EHR appointment representation in the main app."""
    def __init__(
        self,
        ehr_appointment_id: str,
        idempotency_key: str,
        patient_name: Optional[str] = None,
        doctor_name: Optional[str] = None,
        hospital_name: Optional[str] = None,
        slot_time: Optional[str] = None,
        chief_complaint: Optional[str] = None,
        status: str = "CONFIRMED_IN_EHR",
        created_at: Optional[datetime] = None
    ):
        self.id = ehr_appointment_id
        self.ehr_appointment_id = ehr_appointment_id
        self.idempotency_key = idempotency_key
        self.patient_name = patient_name
        self.doctor_name = doctor_name
        self.hospital_name = hospital_name
        self.slot_time = slot_time
        self.chief_complaint = chief_complaint
        self.status = status
        self.created_at = created_at or datetime.utcnow()


class ChaosSettingDTO:
    """DTO for external Mock EHR chaos configuration."""
    def __init__(self, mode: str = "TIMEOUT_AFTER_SAVE", simulated_delay_ms: int = 2500, failure_active: bool = True, one_shot: bool = False):
        self.id = 1
        self.mode = mode
        self.simulated_delay_ms = simulated_delay_ms
        self.failure_active = failure_active
        self.one_shot = one_shot


class MockEhrService:
    """
    Adapter service coordinating outbound requests to the standalone Mock EHR service
    strictly through the Integration / Connector layer (mock_ehr_connector).
    No direct database queries or connections to Mock EHR internal database tables.
    """

    @staticmethod
    def get_or_create_chaos_setting(db: Optional[Session] = None) -> ChaosSettingDTO:
        cfg = mock_ehr_connector.get_chaos_config()
        return ChaosSettingDTO(
            mode=cfg.get("mode", "TIMEOUT_AFTER_SAVE"),
            simulated_delay_ms=cfg.get("simulated_delay_ms", 2500),
            failure_active=cfg.get("failure_active", True),
            one_shot=cfg.get("one_shot", False)
        )

    @staticmethod
    def update_chaos_setting(db: Optional[Session], update: schemas.ChaosConfigUpdate) -> ChaosSettingDTO:
        cfg = mock_ehr_connector.set_chaos_config(
            mode=update.mode,
            delay_ms=update.simulated_delay_ms,
            is_active=update.failure_active,
            one_shot=getattr(update, "one_shot", False)
        )
        data = cfg.get("chaos", {})
        return ChaosSettingDTO(
            mode=data.get("mode", update.mode),
            simulated_delay_ms=data.get("simulated_delay_ms", update.simulated_delay_ms),
            failure_active=data.get("failure_active", update.failure_active),
            one_shot=data.get("one_shot", getattr(update, "one_shot", False))
        )

    @staticmethod
    async def create_external_appointment(
        db: Optional[Session],
        req: schemas.MockEhrAppointmentRequest,
        client_timeout_sec: float = 2.5
    ) -> MockEhrRecordDTO:
        """
        Dispatches POST /appointments over HTTP connector to the standalone Mock EHR service.
        Raises MockEhrTimeoutException when external service responds with 504 / timeout.
        """
        payload = EHRAppointmentPayload(
            idempotency_key=req.idempotency_key,
            patient_name=req.patient_name,
            doctor_name=req.doctor_name,
            hospital_name=req.hospital_name,
            slot_time=req.slot_time,
            chief_complaint=req.chief_complaint
        )

        result = await mock_ehr_connector.create_appointment(payload)

        return MockEhrRecordDTO(
            ehr_appointment_id=result.get("id"),
            idempotency_key=result.get("idempotency_key"),
            patient_name=result.get("patient_name"),
            doctor_name=result.get("provider_name"),
            hospital_name=result.get("hospital_name"),
            slot_time=result.get("slot_time"),
            chief_complaint=result.get("chief_complaint"),
            status=result.get("status", "CONFIRMED_IN_EHR")
        )

    @staticmethod
    def verify_appointment_by_idempotency_key(
        db: Optional[Session],
        idempotency_key: str
    ) -> schemas.MockEhrVerifyResponse:
        """
        Queries external verification endpoint via the connector layer without touching any database.
        """
        verif = mock_ehr_connector.verify_appointment_sync(idempotency_key)

        return schemas.MockEhrVerifyResponse(
            found=verif.found,
            ehr_appointment_id=verif.external_appointment_id or verif.ehr_appointment_id,
            status=verif.status,
            created_at=datetime.utcnow() if verif.found else None
        )

    @staticmethod
    def get_all_ehr_records(db: Optional[Session] = None) -> List[MockEhrRecordDTO]:
        """
        Queries external records through the connector layer.
        """
        raw_records = mock_ehr_connector.list_records()
        return [
            MockEhrRecordDTO(
                ehr_appointment_id=r.get("id") or r.get("external_appointment_id"),
                idempotency_key=r.get("idempotency_key"),
                patient_name=r.get("patient_name"),
                doctor_name=r.get("doctor_name"),
                hospital_name=r.get("hospital_name"),
                slot_time=r.get("slot_time"),
                status=r.get("status", "CONFIRMED_IN_EHR")
            ) for r in raw_records
        ]
