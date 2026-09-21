from datetime import datetime, timedelta
from mock_ehr.database import EhrSessionLocal, EhrBase, ehr_engine
from mock_ehr import models

def seed_mock_ehr():
    """Seeds standalone Mock EHR database with demo external providers, patients, and chaos config."""
    EhrBase.metadata.create_all(bind=ehr_engine)
    db = EhrSessionLocal()
    try:
        # 1. Chaos config
        chaos = db.query(models.EhrChaosConfig).first()
        if not chaos:
            chaos = models.EhrChaosConfig(
                id=1,
                mode="TIMEOUT_AFTER_SAVE",
                simulated_delay_ms=2500,
                failure_active=True
            )
            db.add(chaos)

        # 2. Providers
        if not db.query(models.EhrProvider).first():
            p1 = models.EhrProvider(
                id="DOC-501",
                name="Dr. Sarah Jenkins",
                specialty="Orthopedics",
                department="Orthopedic Surgery",
                hospital_name="St. Jude Memorial Hospital",
                is_active=True
            )
            p2 = models.EhrProvider(
                id="DOC-502",
                name="Dr. Marcus Vance",
                specialty="Cardiology",
                department="Cardiology Department",
                hospital_name="Metro Health General",
                is_active=True
            )
            db.add_all([p1, p2])

        # 3. Patients
        if not db.query(models.EhrPatient).first():
            pat1 = models.EhrPatient(
                id="P-101",
                first_name="Alice",
                last_name="Morgan",
                dob="1992-04-15",
                contact="+1-555-832-1920",
                gender="Female"
            )
            pat2 = models.EhrPatient(
                id="P-102",
                first_name="Bob",
                last_name="Jenkins",
                dob="1985-11-20",
                contact="+1-555-444-5555",
                gender="Male"
            )
            db.add_all([pat1, pat2])

        # 4. Availabilities
        if not db.query(models.EhrAvailability).first():
            now = datetime.utcnow()
            for offset in range(1, 8):
                start = now + timedelta(days=offset, hours=2)
                db.add(models.EhrAvailability(
                    provider_id="DOC-501",
                    start_time=start,
                    end_time=start + timedelta(minutes=30),
                    status="AVAILABLE"
                ))

        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed_mock_ehr()
    print("Mock EHR database initialized and seeded successfully.")
