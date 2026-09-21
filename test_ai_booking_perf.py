import asyncio
import time
from backend.database import SessionLocal
from backend.ai.agent import AIPatientAccessAgent
from backend.ehr.connector import MockEHRConnector
from backend import models

import uuid

async def test_ai_multi_booking():
    db = SessionLocal()
    connector = MockEHRConnector()
    connector.set_chaos_config(mode="NORMAL", is_active=False)

    session_id = f"test-perf-{uuid.uuid4().hex[:6]}"
    patient = db.query(models.Patient).first()
    print(f"Patient: {patient.full_name} ({patient.id})")

    # Turn 1: Search doctors / request appointment with Dr. Elena Rostova
    t0 = time.time()
    res1 = await AIPatientAccessAgent.process_turn(
        db=db,
        session_id=session_id,
        message="I want to book an appointment with Dr. Elena Rostova",
        patient_id=patient.id
    )
    print(f"Turn 1 took {time.time() - t0:.2f}s, reply:\n{res1.reply.encode('ascii', 'ignore').decode()}\ncapabilities: {res1.capabilities_executed}")

    # Turn 2: Select slot
    t1 = time.time()
    res2 = await AIPatientAccessAgent.process_turn(
        db=db,
        session_id=session_id,
        message="Option 1",
        patient_id=patient.id
    )
    print(f"\nTurn 2 took {time.time() - t1:.2f}s, reply:\n{res2.reply.encode('ascii', 'ignore').decode()}\ncapabilities: {res2.capabilities_executed}")

    # Turn 3: Second appointment booking attempt with Dr. Elena Rostova!
    t2 = time.time()
    res3 = await AIPatientAccessAgent.process_turn(
        db=db,
        session_id=session_id,
        message="Now I want to book another appointment for tomorrow with Dr. Elena Rostova",
        patient_id=patient.id
    )
    print(f"\nTurn 3 took {time.time() - t2:.2f}s, reply:\n{res3.reply.encode('ascii', 'ignore').decode()}\ncapabilities: {res3.capabilities_executed}")

    # Turn 4: Select slot for second doctor
    t3 = time.time()
    res4 = await AIPatientAccessAgent.process_turn(
        db=db,
        session_id=session_id,
        message="Option 1",
        patient_id=patient.id
    )
    print(f"Turn 4 took {time.time() - t3:.2f}s, reply:\n{res4.reply.encode('ascii', 'ignore').decode()}\ncapabilities: {res4.capabilities_executed}")

    db.close()

if __name__ == "__main__":
    asyncio.run(test_ai_multi_booking())
