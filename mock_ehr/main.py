from fastapi import FastAPI
from mock_ehr.config import config
from mock_ehr.database import EhrBase, ehr_engine
from mock_ehr.seed import seed_mock_ehr
from mock_ehr.routes import router as ehr_router

from sqlalchemy import text

# Initialize Mock EHR database & seed data
EhrBase.metadata.create_all(bind=ehr_engine)
try:
    with ehr_engine.connect() as conn:
        conn.execute(text("ALTER TABLE ehr_chaos_config ADD COLUMN one_shot BOOLEAN DEFAULT 0"))
        conn.commit()
except Exception:
    pass
seed_mock_ehr()

app = FastAPI(
    title=config.SERVICE_NAME,
    description="Standalone Mock EHR / Healthcare System Simulator with Isolated Database & Idempotency Verification"
)

# Mount routes at root as specified in the PRD and requirements
app.include_router(ehr_router)

# Also mount with prefix /ehr for backwards compatibility
app.include_router(ehr_router, prefix="/ehr")

@app.get("/health")
def health():
    return {"status": "healthy", "service": config.SERVICE_NAME}
