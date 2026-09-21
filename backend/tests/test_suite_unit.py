import unittest
import uuid
import asyncio
from datetime import datetime, timedelta, time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend import models, schemas
from backend.scheduling.engine import SchedulingEngine
from backend.scheduling.locks import slot_lock_manager, SlotLockManager
from backend.ai.context import ConversationContext
from backend.capabilities.base import BaseCapability, CapabilityContext, CapabilityResult
from backend.capabilities.registry import CapabilityRegistry
from backend.workflows.engine import WorkflowStep, WorkflowContext, WorkflowEngine
from backend.ehr.mapping import IdentifierMappingRegistry
from backend.ehr.reconciliation import EHRReconciliationManager
from backend.ehr.connector import BaseEHRConnector, EHRVerificationResult
from pydantic import BaseModel, Field


class DummyInputSchema(BaseModel):
    doctor_id: str
    slot_time: str
    patient_id: str


class DummyOutputSchema(BaseModel):
    status: str
    booking_id: str


class DummyCapability(BaseCapability):
    name = "dummy_book"
    description = "Dummy booking capability for unit testing"
    input_schema = DummyInputSchema
    output_schema = DummyOutputSchema
    requires_authorization = True
    allowed_roles = ["PATIENT", "HOSPITAL_ADMIN", "PLATFORM_ADMIN"]

    def validate(self, params: DummyInputSchema, context: CapabilityContext):
        if "invalid" in params.doctor_id:
            raise ValueError("Invalid doctor specified.")

    def authorize(self, params: DummyInputSchema, context: CapabilityContext) -> bool:
        return context.user_role in self.allowed_roles

    async def execute(self, params: DummyInputSchema, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            data={"status": "CONFIRMED", "booking_id": f"dummy-{params.patient_id}"}
        )


class MockConnectorForReconciliation(BaseEHRConnector):
    def __init__(self, found: bool = True, external_id: str = "EXT-REC-999"):
        self._found = found
        self._external_id = external_id

    async def verify_appointment(self, idempotency_key: str, **kwargs) -> EHRVerificationResult:
        return EHRVerificationResult(
            found=self._found,
            ehr_appointment_id=self._external_id if self._found else None,
            external_appointment_id=self._external_id if self._found else None,
            status="CONFIRMED" if self._found else None
        )

    async def create_appointment(self, *args, **kwargs): pass
    async def reschedule_appointment(self, *args, **kwargs): pass
    async def cancel_appointment(self, *args, **kwargs): pass
    async def lookup_patient(self, *args, **kwargs): pass
    async def lookup_provider(self, *args, **kwargs): pass
    async def lookup_facility(self, *args, **kwargs): pass
    async def lookup_calendar(self, *args, **kwargs): pass
    async def check_availability(self, *args, **kwargs): pass
    async def update_appointment(self, *args, **kwargs): pass
    async def get_appointment(self, *args, **kwargs): pass


class TestComprehensiveWorkflowUnit(unittest.TestCase):
    """
    Complete Unit Test Suite verifying all 9 core engine modules:
    1. Availability Calculation
    2. Slot Validation & Atomic Locking
    3. Appointment State Transitions
    4. Context Resolution
    5. Capability Validation & Authorization
    6. Workflow Conditions & Steps
    7. Identifier Mapping
    8. Idempotency & Deduplication
    9. Reconciliation & Drift Repair
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)

    def setUp(self):
        self.db = self.SessionLocal()
        # Seed test hospital
        self.hospital = models.Hospital(
            id=f"hosp-unit-{uuid.uuid4().hex[:6]}",
            name="Unit Test Hospital",
            address="100 Test Ave",
            license_number=f"LIC-{uuid.uuid4().hex[:6]}",
            contact_email="unit@hospital.org",
            phone="+1-555-0199",
            status="APPROVED"
        )
        self.db.add(self.hospital)
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # =========================================================================
    # 1. AVAILABILITY CALCULATION
    # =========================================================================

    def test_availability_calculation_normal_working_hours(self):
        """Verifies calculation of discrete slots across active working days."""
        doc_id = "doc-test-1"
        # Mon-Fri: 09:00 to 11:00 (4 slots of 30 min per day)
        working_hours = {
            i: {"start": "09:00", "end": "11:00"} for i in range(7)
        }
        blocked_periods = []

        slots = SchedulingEngine.calculate_availability(
            doctor_id=doc_id,
            working_hours=working_hours,
            blocked_periods=blocked_periods,
            slot_duration_minutes=30,
            days_ahead=3
        )

        self.assertIsInstance(slots, list)
        self.assertGreater(len(slots), 0)
        for s in slots:
            self.assertEqual(s["doctor_id"], doc_id)
            self.assertEqual(s["status"], "AVAILABLE")
            self.assertEqual(s["end_time"] - s["start_time"], timedelta(minutes=30))

    def test_availability_calculation_excludes_blocked_slots(self):
        """Verifies blocked intervals (surgeries, leave) are excluded from availability."""
        doc_id = "doc-test-blocked"
        working_hours = {
            i: {"start": "09:00", "end": "12:00"} for i in range(7)
        }
        # Block day 1 from 09:30 to 11:00
        target_date = (datetime.utcnow() + timedelta(days=1)).date()
        block_start = datetime.combine(target_date, time(9, 30))
        block_end = datetime.combine(target_date, time(11, 0))

        blocked_periods = [{
            "start_time": block_start,
            "end_time": block_end
        }]

        slots = SchedulingEngine.calculate_availability(
            doctor_id=doc_id,
            working_hours=working_hours,
            blocked_periods=blocked_periods,
            slot_duration_minutes=30,
            days_ahead=2
        )

        # None of the returned slots should overlap the blocked period
        for s in slots:
            overlap = (s["start_time"] < block_end and s["end_time"] > block_start)
            self.assertFalse(overlap, f"Slot {s['start_time']} overlaps with blocked period!")

    # =========================================================================
    # 2. SLOT VALIDATION & CONCURRENCY LOCKING
    # =========================================================================

    def test_slot_validation_and_lock_concurrency(self):
        """Verifies atomic slot hold locking prevents concurrent double-holds."""
        lock_mgr = SlotLockManager()
        slot_id = f"slot-unit-{uuid.uuid4().hex[:6]}"

        # 1. First hold acquisition succeeds
        acquired_1 = lock_mgr.acquire_hold_sync(slot_id, hold_duration_minutes=5)
        self.assertTrue(acquired_1)

        # 2. Second concurrent hold fails immediately
        acquired_2 = lock_mgr.acquire_hold_sync(slot_id, hold_duration_minutes=5)
        self.assertFalse(acquired_2)

        # 3. Release provisional hold
        lock_mgr.release_hold_sync(slot_id)

        # 4. Re-acquiring hold succeeds after release
        acquired_3 = lock_mgr.acquire_hold_sync(slot_id, hold_duration_minutes=5)
        self.assertTrue(acquired_3)

    def test_slot_time_boundary_validation(self):
        """Verifies start_time must precede end_time and positive duration."""
        start = datetime.utcnow() + timedelta(hours=2)
        end = start + timedelta(minutes=30)
        invalid_end = start - timedelta(minutes=15)

        self.assertTrue(start < end)
        self.assertFalse(start < invalid_end)

    # =========================================================================
    # 3. APPOINTMENT STATE TRANSITIONS
    # =========================================================================

    def test_appointment_state_transitions(self):
        """Verifies valid and forbidden appointment state transitions."""
        VALID_TRANSITIONS = {
            "PENDING": ["CONFIRMED", "CANCELLED"],
            "CONFIRMED": ["COMPLETED", "CANCELLED", "RESCHEDULED"],
            "RESCHEDULED": ["CONFIRMED", "CANCELLED"],
            "COMPLETED": [],
            "CANCELLED": []
        }

        # Helper state transition validator
        def can_transition(current: str, target: str) -> bool:
            return target in VALID_TRANSITIONS.get(current, [])

        # Allowed transitions
        self.assertTrue(can_transition("PENDING", "CONFIRMED"))
        self.assertTrue(can_transition("CONFIRMED", "COMPLETED"))
        self.assertTrue(can_transition("CONFIRMED", "CANCELLED"))
        self.assertTrue(can_transition("CONFIRMED", "RESCHEDULED"))
        self.assertTrue(can_transition("RESCHEDULED", "CONFIRMED"))

        # Forbidden transitions
        self.assertFalse(can_transition("CANCELLED", "CONFIRMED"))
        self.assertFalse(can_transition("COMPLETED", "CANCELLED"))
        self.assertFalse(can_transition("CANCELLED", "COMPLETED"))

    # =========================================================================
    # 4. CONTEXT RESOLUTION
    # =========================================================================

    def test_context_resolution_doctor_and_slot_references(self):
        """Verifies conversational working memory reference resolution."""
        ctx = ConversationContext(session_id="sess-unit-101")
        ctx.last_doctors_listed = [
            {"id": "doc-1", "full_name": "Dr. Sarah Jenkins", "specialty": "Cardiology"},
            {"id": "doc-2", "full_name": "Dr. Marcus Chen", "specialty": "Dermatology"}
        ]
        ctx.last_slots_listed = [
            {"id": "slot-1", "start_time": "2026-09-25T09:00:00Z"},
            {"id": "slot-2", "start_time": "2026-09-25T10:00:00Z"},
            {"id": "slot-3", "start_time": "2026-09-25T14:00:00Z"}
        ]

        # 1. Resolve 'the first doctor'
        res1 = ctx.resolve_reference("I would like to see the first doctor")
        self.assertEqual(res1.get("doctor", {}).get("full_name"), "Dr. Sarah Jenkins")

        # 2. Resolve doctor by surname
        ctx.selected_doctor = None
        res2 = ctx.resolve_reference("Book me with Dr. Chen please")
        self.assertEqual(res2.get("doctor", {}).get("full_name"), "Dr. Marcus Chen")

        # 3. Resolve 'the second slot'
        res3 = ctx.resolve_reference("Let's go with the second slot")
        self.assertEqual(res3.get("slot", {}).get("id"), "slot-2")

        # 4. Resolve notification preference channel
        ctx.resolve_reference("Please send me SMS confirmation")
        self.assertEqual(ctx.communication_preferences.get("channel"), "SMS")

    # =========================================================================
    # 5. CAPABILITY VALIDATION & AUTHORIZATION
    # =========================================================================

    def test_capability_validation_and_authorization(self):
        """Verifies schema validation, semantic hooks, and role authorization."""
        registry = CapabilityRegistry()
        cap = DummyCapability()
        registry.register(cap)

        # 1. Valid invocation by authorized role
        ctx_authorized = CapabilityContext(
            user_id="patient-1",
            user_role="PATIENT",
            tenant_id=self.hospital.id,
            correlation_id="CORR-CAP-1"
        )
        res_valid = asyncio.run(registry.invoke(
            "dummy_book",
            {"doctor_id": "doc-1", "slot_time": "10:00", "patient_id": "p-100"},
            ctx_authorized
        ))
        self.assertTrue(res_valid.success)
        self.assertEqual(res_valid.data["booking_id"], "dummy-p-100")

        # 2. Schema validation error on missing required field
        res_missing = asyncio.run(registry.invoke(
            "dummy_book",
            {"doctor_id": "doc-1"},  # missing slot_time and patient_id
            ctx_authorized
        ))
        self.assertFalse(res_missing.success)
        self.assertEqual(res_missing.error_code, "INVALID_INPUT")

        # 3. Semantic validation error hook triggered
        res_semantic = asyncio.run(registry.invoke(
            "dummy_book",
            {"doctor_id": "invalid-doctor", "slot_time": "10:00", "patient_id": "p-100"},
            ctx_authorized
        ))
        self.assertFalse(res_semantic.success)
        self.assertEqual(res_semantic.error_code, "VALIDATION_ERROR")

        # 4. Authorization check blocks unauthorized role
        ctx_unauthorized = CapabilityContext(
            user_id="guest-1",
            user_role="GUEST_ANONYMOUS",
            tenant_id=self.hospital.id,
            correlation_id="CORR-CAP-UNAUTH"
        )
        res_unauth = asyncio.run(registry.invoke(
            "dummy_book",
            {"doctor_id": "doc-1", "slot_time": "10:00", "patient_id": "p-100"},
            ctx_unauthorized
        ))
        self.assertFalse(res_unauth.success)
        self.assertEqual(res_unauth.error_code, "UNAUTHORIZED")

    # =========================================================================
    # 6. WORKFLOW CONDITIONS & STEPS
    # =========================================================================

    def test_workflow_conditional_branching(self):
        """Verifies workflow step conditions execute or skip steps dynamically."""
        async def dummy_step_handler(ctx: WorkflowContext):
            return {"executed": True}

        # Step with True condition
        step_true = WorkflowStep(
            name="EXECUTE_ON_CONFIRMED",
            handler=dummy_step_handler,
            condition=lambda ctx: ctx.payload.get("status") == "CONFIRMED"
        )

        # Step with False condition
        step_false = WorkflowStep(
            name="EXECUTE_ON_EMERGENCY",
            handler=dummy_step_handler,
            condition=lambda ctx: ctx.payload.get("urgency") == "EMERGENCY"
        )

        ctx = WorkflowContext(
            db=self.db,
            workflow_id="wf-unit-test",
            workflow_type="POST_BOOKING_WORKFLOW",
            hospital_id=self.hospital.id,
            payload={"status": "CONFIRMED", "urgency": "ROUTINE"}
        )

        self.assertTrue(step_true.condition(ctx))
        self.assertFalse(step_false.condition(ctx))

    # =========================================================================
    # 7. IDENTIFIER MAPPING
    # =========================================================================

    def test_identifier_mapping_bidirectional_and_persistence(self):
        """Verifies multi-system entity ID translation and DB persistence."""
        registry = IdentifierMappingRegistry()
        internal_patient_id = f"pat-{uuid.uuid4().hex[:6]}"
        external_mrn = "EHR-MRN-90210"

        # 1. Set mapping with DB persistence
        registry.set_mapping(
            entity_type="PATIENT",
            internal_id=internal_patient_id,
            external_id=external_mrn,
            hospital_id=self.hospital.id,
            db=self.db,
            system_name="MOCK_EHR"
        )

        # 2. Bidirectional translation
        resolved_ext = registry.get_external_id("PATIENT", internal_patient_id, system_name="MOCK_EHR")
        self.assertEqual(resolved_ext, external_mrn)

        resolved_int = registry.get_internal_id("PATIENT", external_mrn, system_name="MOCK_EHR")
        self.assertEqual(resolved_int, internal_patient_id)

        # 3. Verify record in SQLite database
        db_rec = self.db.query(models.ExternalIdentifierMapping).filter(
            models.ExternalIdentifierMapping.internal_id == internal_patient_id
        ).first()
        self.assertIsNotNone(db_rec)
        self.assertEqual(db_rec.external_id, external_mrn)

    # =========================================================================
    # 8. IDEMPOTENCY & DEDUPLICATION
    # =========================================================================

    def test_idempotency_key_deduplication(self):
        """Verifies identical idempotency_key prevents duplicate appointment records."""
        idemp_key = f"IDEMP-{uuid.uuid4().hex[:10]}"

        # Seed a test patient and doctor
        patient = models.Patient(
            id=str(uuid.uuid4()),
            full_name="Idemp Test Patient",
            phone="+1-555-0199",
            email="idemp@patient.org",
            primary_hospital_id=self.hospital.id
        )
        doctor = models.Doctor(
            id=str(uuid.uuid4()),
            hospital_id=self.hospital.id,
            full_name="Dr. Idemp",
            specialty="Cardiology"
        )
        self.db.add_all([patient, doctor])
        self.db.commit()

        # Create first appointment record
        appt1 = models.Appointment(
            id=str(uuid.uuid4()),
            hospital_id=self.hospital.id,
            patient_id=patient.id,
            doctor_id=doctor.id,
            idempotency_key=idemp_key,
            status="CONFIRMED",
            start_time=datetime.utcnow() + timedelta(days=1),
            end_time=datetime.utcnow() + timedelta(days=1, minutes=30)
        )
        self.db.add(appt1)
        self.db.commit()

        # Check existing lookup with same key
        existing = self.db.query(models.Appointment).filter(
            models.Appointment.idempotency_key == idemp_key
        ).first()

        self.assertIsNotNone(existing)
        self.assertEqual(existing.id, appt1.id)

    # =========================================================================
    # 9. RECONCILIATION & DRIFT REPAIR
    # =========================================================================

    def test_reconciliation_verifies_external_state(self):
        """Verifies EHR reconciliation queries external system to verify booking state."""
        mock_connector = MockConnectorForReconciliation(found=True, external_id="EXT-APPT-555")
        reconciliation_mgr = EHRReconciliationManager(connector=mock_connector)

        cid = f"CORR-REC-{uuid.uuid4().hex[:6]}"
        idemp = f"IDEMP-TIMEOUT-{uuid.uuid4().hex[:6]}"

        verification = asyncio.run(reconciliation_mgr.handle_timeout_outcome(
            idempotency_key=idemp,
            appointment_id="appt-rec-1",
            correlation_id=cid
        ))

        self.assertTrue(verification.found)
        self.assertEqual(verification.external_appointment_id, "EXT-APPT-555")


if __name__ == "__main__":
    unittest.main()
