import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

MOCK_EHR_DB_PATH = os.getenv("MOCK_EHR_DATABASE_URL", "sqlite:///./mock_ehr.db")

connect_args = {"check_same_thread": False} if MOCK_EHR_DB_PATH.startswith("sqlite") else {}

ehr_engine = create_engine(
    MOCK_EHR_DB_PATH,
    connect_args=connect_args
)

EhrSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ehr_engine)

EhrBase = declarative_base()

def get_ehr_db():
    db = EhrSessionLocal()
    try:
        yield db
    finally:
        db.close()
