import os
import httpx
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from mock_ehr.main import app as mock_ehr_app


class EHRPatientPayload(BaseModel):
    first_name: str
    last_name: str
    dob: Optional[str] = None
    contact: str
    gender: Optional[str] = None


class EHRAppointmentPayload(BaseModel):
    external_patient_id: Optional[str] = "P-101"
    external_provider_id: Optional[str] = "DOC-501"
    patient_name: Optional[str] = "Jane Doe"
    doctor_name: Optional[str] = None
    hospital_name: Optional[str] = "St. Jude Memorial Hospital"
    slot_time: str
    idempotency_key: str
    chief_complaint: Optional[str] = None


class EHRVerificationResult(BaseModel):
    found: bool
    ehr_appointment_id: Optional[str] = None
    external_appointment_id: Optional[str] = None
    status: Optional[str] = None
    reconciliation_required: bool = False
    details: Optional[Dict[str, Any]] = None


# =====================================================================
# 1. HEALTHCARE SYSTEM CONNECTOR ABSTRACTION INTERFACE
# =====================================================================

class HealthcareSystemConnector(ABC):
    """
    Vendor-agnostic interface for external healthcare system integrations
    (Mock EHR, Epic, Cerner, FHIR, etc.).
    Hides all vendor-specific implementation details from the core platform.
    """

    @abstractmethod
    async def lookup_patient(self, identifier: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Lookup patient by external identifier, contact, or MRN."""
        pass

    @abstractmethod
    async def lookup_provider(self, provider_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Lookup healthcare provider / doctor by external provider ID."""
        pass

    @abstractmethod
    async def lookup_facility(self, facility_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Lookup medical facility, hospital, or clinic by external facility ID."""
        pass

    @abstractmethod
    async def lookup_calendar(self, calendar_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Lookup clinical schedule or calendar resource where applicable."""
        pass

    @abstractmethod
    async def check_availability(
        self,
        provider_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Check bookable availability intervals for a provider."""
        pass

    @abstractmethod
    async def create_appointment(self, payload: EHRAppointmentPayload, **kwargs) -> Dict[str, Any]:
        """Atomically create appointment in external healthcare system with idempotency protection."""
        pass

    @abstractmethod
    async def update_appointment(
        self,
        external_appointment_id: str,
        updates: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """Update appointment details in external healthcare system."""
        pass

    @abstractmethod
    async def cancel_appointment(
        self,
        external_appointment_id: str,
        reason: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Cancel/void appointment in external healthcare system."""
        pass

    @abstractmethod
    async def reschedule_appointment(
        self,
        external_appointment_id: str,
        new_slot_time: str,
        idempotency_key: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Reschedule existing appointment to a new slot in external healthcare system."""
        pass

    @abstractmethod
    async def get_appointment(self, external_appointment_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Retrieve external appointment record by ID or idempotency key."""
        pass

    @abstractmethod
    async def verify_appointment(self, idempotency_key: str, **kwargs) -> EHRVerificationResult:
        """
        Idempotent verification query to confirm if appointment was persisted
        during a network timeout or unknown outcome before retrying.
        """
        pass


# Backwards compatibility alias
BaseEHRConnector = HealthcareSystemConnector


# =====================================================================
# 2. MOCK EHR CONNECTOR IMPLEMENTATION
# =====================================================================

class MockEHRConnector(HealthcareSystemConnector):
    """
    Concrete implementation of HealthcareSystemConnector communicating with
    the standalone Mock EHR service over HTTP APIs.
    """
    def __init__(self, base_url: Optional[str] = None):
        raw_url = base_url or os.getenv("MOCK_EHR_BASE_URL", "")
        self.base_url = raw_url.rstrip("/") if raw_url else ""
        self._asgi_transport = httpx.ASGITransport(app=mock_ehr_app)
        self._direct_http_available: Optional[bool] = True if self.base_url else False

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0
    ) -> httpx.Response:
        """Executes asynchronous HTTP request with live-network or in-process fallback."""
        if self.base_url and self._direct_http_available is not False:
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(timeout, connect=0.25)
                ) as client:
                    res = await client.request(method, path, json=json, params=params)
                    self._direct_http_available = True
                    return res
            except (httpx.ConnectError, httpx.ConnectTimeout):
                self._direct_http_available = False

        async with httpx.AsyncClient(transport=self._asgi_transport, base_url="http://mock-ehr", timeout=timeout) as client:
            return await client.request(method, path, json=json, params=params)

    def _request_sync(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0
    ) -> Any:
        """Executes synchronous HTTP request with live-network or in-process fallback."""
        if self.base_url and self._direct_http_available is not False:
            try:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(timeout, connect=0.25)
                ) as client:
                    res = client.request(method, path, json=json, params=params)
                    self._direct_http_available = True
                    return res
            except (httpx.ConnectError, httpx.ConnectTimeout):
                self._direct_http_available = False

        from starlette.testclient import TestClient
        with TestClient(mock_ehr_app) as client:
            return client.request(method, path, json=json, params=params)

    # 1. lookup_patient()
    async def lookup_patient(self, identifier: str, **kwargs) -> Optional[Dict[str, Any]]:
        res = await self._request("GET", f"/patients/{identifier}")
        return res.json() if res.status_code == 200 else None

    # Helper: create_patient()
    async def create_patient(self, payload: EHRPatientPayload) -> Dict[str, Any]:
        res = await self._request("POST", "/patients", json=payload.model_dump())
        if res.status_code >= 400:
            raise Exception(f"EHR Error {res.status_code}: {res.text}")
        return res.json()

    # 2. lookup_provider()
    async def lookup_provider(self, provider_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        res = await self._request("GET", f"/providers/{provider_id}")
        return res.json() if res.status_code == 200 else None

    async def get_providers(self, specialty: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"specialty": specialty} if specialty else None
        res = await self._request("GET", "/providers", params=params)
        return res.json() if res.status_code == 200 else []

    # 3. lookup_facility()
    async def lookup_facility(self, facility_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        res = await self._request("GET", f"/facilities/{facility_id}")
        return res.json() if res.status_code == 200 else None

    # 4. lookup_calendar()
    async def lookup_calendar(self, calendar_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        res = await self._request("GET", f"/calendars/{calendar_id}")
        return res.json() if res.status_code == 200 else None

    # 5. check_availability()
    async def check_availability(
        self,
        provider_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        params = {"provider_id": provider_id}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        res = await self._request("GET", "/availability", params=params)
        return res.json() if res.status_code == 200 else []

    # 6. create_appointment()
    async def create_appointment(self, payload: EHRAppointmentPayload, **kwargs) -> Dict[str, Any]:
        data = {
            "idempotency_key": payload.idempotency_key,
            "patient_id": payload.external_patient_id,
            "patient_name": payload.patient_name,
            "provider_id": payload.external_provider_id,
            "doctor_name": payload.doctor_name,
            "hospital_name": payload.hospital_name,
            "slot_time": payload.slot_time,
            "chief_complaint": payload.chief_complaint
        }
        res = await self._request("POST", "/appointments", json=data)

        if res.status_code == 504:
            from backend.services.mock_ehr_service import MockEhrTimeoutException
            raise MockEhrTimeoutException("Mock EHR HTTP 504 Gateway Timeout (Simulated)", saved_internally=True)
        elif res.status_code in [401, 403]:
            from backend.ehr.exceptions import EHRAuthenticationException
            raise EHRAuthenticationException(f"EHR Authentication Failure ({res.status_code}): {res.text}")
        elif res.status_code == 429:
            from backend.ehr.exceptions import EHRRateLimitException
            raise EHRRateLimitException(f"EHR Rate Limit Exceeded (429): {res.text}")
        elif res.status_code == 503:
            from backend.ehr.exceptions import EHROutageException
            raise EHROutageException(f"EHR Outage / Unavailable (503): {res.text}")
        elif res.status_code in [400, 422]:
            from backend.ehr.exceptions import EHRValidationException
            raise EHRValidationException(f"EHR Validation / Mapping Error ({res.status_code}): {res.text}")
        elif res.status_code >= 400:
            from backend.ehr.exceptions import EHRIntegrationException
            raise EHRIntegrationException(f"EHR Error ({res.status_code}): {res.text}")

        return res.json()

    # 7. update_appointment()
    async def update_appointment(
        self,
        external_appointment_id: str,
        updates: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        res = await self._request("PUT", f"/appointments/{external_appointment_id}", json=updates)
        if res.status_code >= 400:
            raise Exception(f"EHR Error {res.status_code}: {res.text}")
        return res.json()

    # 8. cancel_appointment()
    async def cancel_appointment(
        self,
        external_appointment_id: str,
        reason: Optional[str] = None,
        **kwargs
    ) -> bool:
        res = await self._request("DELETE", f"/appointments/{external_appointment_id}")
        return res.status_code == 200

    # 9. reschedule_appointment()
    async def reschedule_appointment(
        self,
        external_appointment_id: str,
        new_slot_time: str,
        idempotency_key: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        updates = {"slot_time": new_slot_time, "status": "RESCHEDULED"}
        return await self.update_appointment(external_appointment_id, updates)

    # 10. get_appointment()
    async def get_appointment(self, external_appointment_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        res = await self._request("GET", f"/appointments/{external_appointment_id}")
        return res.json() if res.status_code == 200 else None

    # 11. verify_appointment()
    async def verify_appointment(self, idempotency_key: str, **kwargs) -> EHRVerificationResult:
        res = await self._request("POST", f"/appointments/{idempotency_key}/verify")
        if res.status_code != 200:
            res = await self._request("GET", "/appointments/verify", params={"idempotency_key": idempotency_key})

        if res.status_code == 200:
            data = res.json()
            appt_id = data.get("appointment_id") or data.get("external_appointment_id")
            return EHRVerificationResult(
                found=data.get("found", False),
                ehr_appointment_id=appt_id,
                external_appointment_id=appt_id,
                status=data.get("status"),
                details=data.get("details")
            )
        return EHRVerificationResult(found=False)

    def verify_appointment_sync(self, idempotency_key: str) -> EHRVerificationResult:
        """Synchronous verification query for recovery checks."""
        res = self._request_sync("POST", f"/appointments/{idempotency_key}/verify")
        if res.status_code != 200:
            res = self._request_sync("GET", "/appointments/verify", params={"idempotency_key": idempotency_key})

        if res.status_code == 200:
            data = res.json()
            appt_id = data.get("appointment_id") or data.get("external_appointment_id")
            return EHRVerificationResult(
                found=data.get("found", False),
                ehr_appointment_id=appt_id,
                external_appointment_id=appt_id,
                status=data.get("status"),
                details=data.get("details")
            )
        return EHRVerificationResult(found=False)

    # Diagnostic & Chaos helpers
    def get_chaos_config(self) -> Dict[str, Any]:
        res = self._request_sync("GET", "/chaos")
        return res.json() if res.status_code == 200 else {}

    def set_chaos_config(self, mode: str, delay_ms: int = 2500, is_active: bool = True, one_shot: bool = False) -> Dict[str, Any]:
        payload = {"mode": mode, "simulated_delay_ms": delay_ms, "failure_active": is_active, "one_shot": one_shot}
        res = self._request_sync("POST", "/chaos", json=payload)
        return res.json() if res.status_code == 200 else {}

    def list_records(self) -> List[Dict[str, Any]]:
        res = self._request_sync("GET", "/records")
        return res.json() if res.status_code == 200 else []


# =====================================================================
# 3. CONNECTOR REGISTRY / FACTORY (PLUGGABLE ARCHITECTURE)
# =====================================================================

class HealthcareSystemConnectorRegistry:
    """
    Registry allowing the Mock EHR connector to be plugged in or replaced
    by real healthcare system connectors (Epic, Cerner, FHIR) without modifying core services.
    """
    def __init__(self):
        self._connectors: Dict[str, HealthcareSystemConnector] = {}

    def register(self, system_name: str, connector: HealthcareSystemConnector):
        self._connectors[system_name.upper()] = connector

    def get(self, system_name: str = "MOCK_EHR") -> HealthcareSystemConnector:
        return self._connectors.get(
            system_name.upper(),
            self._connectors.get("MOCK_EHR", mock_ehr_connector)
        )


# Global instances
mock_ehr_connector = MockEHRConnector()
connector_registry = HealthcareSystemConnectorRegistry()
connector_registry.register("MOCK_EHR", mock_ehr_connector)

def get_ehr_connector(system_name: str = "MOCK_EHR") -> HealthcareSystemConnector:
    return connector_registry.get(system_name)
