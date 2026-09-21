from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from backend.config import settings

# For SQLite, check_same_thread=False allows multi-threaded requests in FastAPI
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def init_db_migrations():
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE chaos_settings ADD COLUMN one_shot BOOLEAN DEFAULT 0",
        "ALTER TABLE questionnaires ADD COLUMN doctor_id VARCHAR(36)",
        "ALTER TABLE questionnaires ADD COLUMN appointment_type VARCHAR(50) DEFAULT 'ALL'",
        "ALTER TABLE questionnaires ADD COLUMN condition_category VARCHAR(100) DEFAULT 'GENERAL_INTAKE'",
        "ALTER TABLE questionnaires ADD COLUMN is_approved BOOLEAN DEFAULT 1",
        "ALTER TABLE questionnaires ADD COLUMN approved_by VARCHAR(100)",
        "ALTER TABLE questionnaires ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE questionnaire_responses ADD COLUMN is_urgent BOOLEAN DEFAULT 0",
        "ALTER TABLE questionnaire_responses ADD COLUMN urgent_reasons JSON",
        "ALTER TABLE questionnaire_responses ADD COLUMN reviewed_by VARCHAR(36)",
        "ALTER TABLE questionnaire_responses ADD COLUMN reviewed_at DATETIME",
        "ALTER TABLE questionnaire_responses ADD COLUMN doctor_notes TEXT",
        "ALTER TABLE pre_visit_questionnaires ADD COLUMN is_urgent BOOLEAN DEFAULT 0",
        "ALTER TABLE pre_visit_questionnaires ADD COLUMN urgent_reasons JSON",
        "ALTER TABLE pre_visit_questionnaires ADD COLUMN reviewed_by VARCHAR(36)",
        "ALTER TABLE pre_visit_questionnaires ADD COLUMN reviewed_at DATETIME",
        "ALTER TABLE pre_visit_questionnaires ADD COLUMN doctor_notes TEXT",
        "ALTER TABLE workflows ADD COLUMN idempotency_key VARCHAR(100)",
        "ALTER TABLE workflows ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE workflows ADD COLUMN max_retries INTEGER DEFAULT 3",
        "ALTER TABLE workflows ADD COLUMN scheduled_for DATETIME",
        "ALTER TABLE workflows ADD COLUMN error_message TEXT",
        "ALTER TABLE workflows ADD COLUMN completed_at DATETIME",
        "ALTER TABLE notifications ADD COLUMN idempotency_key VARCHAR(100)",
        "ALTER TABLE notifications ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE notifications ADD COLUMN payload JSON",
        "ALTER TABLE hospitals ADD COLUMN correction_notes TEXT",
        "ALTER TABLE hospitals ADD COLUMN services JSON",
        "ALTER TABLE hospitals ADD COLUMN operating_hours JSON",
        "ALTER TABLE hospitals ADD COLUMN supported_healthcare_systems JSON",
        "ALTER TABLE hospitals ADD COLUMN integration_config JSON",
        "ALTER TABLE hospitals ADD COLUMN communication_preferences JSON",
        "ALTER TABLE hospitals ADD COLUMN lifecycle_history JSON",
        "ALTER TABLE doctors ADD COLUMN photo_url VARCHAR(500)",
        "ALTER TABLE doctors ADD COLUMN qualifications VARCHAR(255)",
        "ALTER TABLE doctors ADD COLUMN experience_years INTEGER DEFAULT 5",
        "ALTER TABLE doctors ADD COLUMN languages VARCHAR(255) DEFAULT 'English'",
        "ALTER TABLE doctors ADD COLUMN external_provider_id VARCHAR(100)",
        "ALTER TABLE doctors ADD COLUMN supported_appointment_types VARCHAR(255)",
        "ALTER TABLE doctors ADD COLUMN status VARCHAR(50) DEFAULT 'ACTIVE'",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass

init_db_migrations()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
