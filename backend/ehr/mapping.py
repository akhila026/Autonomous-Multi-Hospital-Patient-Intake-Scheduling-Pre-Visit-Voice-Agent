from typing import Optional, Dict, Tuple
from sqlalchemy.orm import Session
from backend import models


class IdentifierMappingRegistry:
    """
    Maintains cross-system bidirectional identifier mappings (PRD Section 12 & 22):
    - Internal Patient <-> External Patient
    - Internal Doctor <-> External Provider
    - Internal Appointment <-> External Appointment
    - Internal Facility <-> External Facility

    Provides dual-tier resolution:
    1. Database-backed persistence in models.ExternalIdentifierMapping
    2. High-speed in-memory bidirectional caching
    """
    def __init__(self):
        # (system_name, entity_type, internal_id) -> external_id
        self._int_to_ext: Dict[Tuple[str, str, str], str] = {}
        # (system_name, entity_type, external_id) -> internal_id
        self._ext_to_int: Dict[Tuple[str, str, str], str] = {}

    def set_mapping(
        self,
        entity_type: str,
        internal_id: str,
        external_id: str,
        hospital_id: Optional[str] = None,
        db: Optional[Session] = None,
        system_name: str = "MOCK_EHR"
    ):
        """Stores mapping in cache and persists to DB if session provided."""
        sys = system_name.upper()
        etype = entity_type.upper()

        self._int_to_ext[(sys, etype, internal_id)] = external_id
        self._ext_to_int[(sys, etype, external_id)] = internal_id

        if db and hospital_id:
            # Check if mapping record exists in DB
            rec = db.query(models.ExternalIdentifierMapping).filter(
                models.ExternalIdentifierMapping.hospital_id == hospital_id,
                models.ExternalIdentifierMapping.system_name == sys,
                models.ExternalIdentifierMapping.entity_type == etype,
                models.ExternalIdentifierMapping.internal_id == internal_id
            ).first()

            if not rec:
                rec = models.ExternalIdentifierMapping(
                    hospital_id=hospital_id,
                    system_name=sys,
                    entity_type=etype,
                    internal_id=internal_id,
                    external_id=external_id
                )
                db.add(rec)
            else:
                rec.external_id = external_id
            db.commit()

    def get_external_id(
        self,
        entity_type: str,
        internal_id: str,
        hospital_id: Optional[str] = None,
        db: Optional[Session] = None,
        system_name: str = "MOCK_EHR"
    ) -> Optional[str]:
        """Resolves external ID from internal ID."""
        sys = system_name.upper()
        etype = entity_type.upper()

        # Check cache first
        if (sys, etype, internal_id) in self._int_to_ext:
            return self._int_to_ext[(sys, etype, internal_id)]

        # Check DB
        if db:
            q = db.query(models.ExternalIdentifierMapping).filter(
                models.ExternalIdentifierMapping.system_name == sys,
                models.ExternalIdentifierMapping.entity_type == etype,
                models.ExternalIdentifierMapping.internal_id == internal_id
            )
            if hospital_id:
                q = q.filter(models.ExternalIdentifierMapping.hospital_id == hospital_id)
            rec = q.first()
            if rec:
                self._int_to_ext[(sys, etype, internal_id)] = rec.external_id
                self._ext_to_int[(sys, etype, rec.external_id)] = internal_id
                return rec.external_id

        return None

    def get_internal_id(
        self,
        entity_type: str,
        external_id: str,
        hospital_id: Optional[str] = None,
        db: Optional[Session] = None,
        system_name: str = "MOCK_EHR"
    ) -> Optional[str]:
        """Resolves internal ID from external ID."""
        sys = system_name.upper()
        etype = entity_type.upper()

        # Check cache first
        if (sys, etype, external_id) in self._ext_to_int:
            return self._ext_to_int[(sys, etype, external_id)]

        # Check DB
        if db:
            q = db.query(models.ExternalIdentifierMapping).filter(
                models.ExternalIdentifierMapping.system_name == sys,
                models.ExternalIdentifierMapping.entity_type == etype,
                models.ExternalIdentifierMapping.external_id == external_id
            )
            if hospital_id:
                q = q.filter(models.ExternalIdentifierMapping.hospital_id == hospital_id)
            rec = q.first()
            if rec:
                self._int_to_ext[(sys, etype, rec.internal_id)] = external_id
                self._ext_to_int[(sys, etype, external_id)] = rec.internal_id
                return rec.internal_id

        return None

    # --- 1. PATIENT MAPPINGS ---
    def map_patient(self, internal_id: str, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR"):
        self.set_mapping("PATIENT", internal_id, external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_external_patient_id(self, internal_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_external_id("PATIENT", internal_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_internal_patient_id(self, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_internal_id("PATIENT", external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    # --- 2. DOCTOR / PROVIDER MAPPINGS ---
    def map_doctor(self, internal_id: str, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR"):
        self.set_mapping("DOCTOR", internal_id, external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_external_doctor_id(self, internal_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_external_id("DOCTOR", internal_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_internal_doctor_id(self, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_internal_id("DOCTOR", external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    # --- 3. APPOINTMENT MAPPINGS ---
    def map_appointment(self, internal_id: str, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR"):
        self.set_mapping("APPOINTMENT", internal_id, external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_external_appointment_id(self, internal_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_external_id("APPOINTMENT", internal_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_internal_appointment_id(self, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_internal_id("APPOINTMENT", external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    # --- 4. FACILITY MAPPINGS ---
    def map_facility(self, internal_id: str, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR"):
        self.set_mapping("FACILITY", internal_id, external_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_external_facility_id(self, internal_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_external_id("FACILITY", internal_id, hospital_id=hospital_id, db=db, system_name=system_name)

    def get_internal_facility_id(self, external_id: str, hospital_id: Optional[str] = None, db: Optional[Session] = None, system_name: str = "MOCK_EHR") -> Optional[str]:
        return self.get_internal_id("FACILITY", external_id, hospital_id=hospital_id, db=db, system_name=system_name)


mapping_registry = IdentifierMappingRegistry()
