import uuid
from datetime import datetime, timedelta, time
from backend.database import SessionLocal, engine, Base
from backend import models
from backend.services.slot_service import SlotService

def seed_database():
    """
    Seeds database with realistic healthcare demo data matching PRD specifications:
    - 2 Hospitals (St. Jude Memorial & Metro Health General)
    - Departments for each hospital
    - Specialties: Orthopedics, Cardiology, General Medicine, Dermatology
    - Hospital Administrators
    - Doctors distributed across both hospitals with profiles and fees
    - Doctor Calendars
    - Realistic working hours & recurring availabilities
    - Future available discrete time slots
    - Several blocked periods (surgery blocks, department rounds)
    - Configured pre-visit questionnaires
    - Demo Patient
    - Chaos settings for EHR simulation
    """
    db = SessionLocal()

    try:
        # Check if already seeded with demo hospital
        existing_hosp = db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-88910").first()
        if existing_hosp:
            _ensure_seed_users(db)
            _ensure_seed_questionnaires(db)
            print("Database already seeded with core healthcare demo data, questionnaires, and users verified.")
            return

        print("Seeding database with complete multi-tenant healthcare data...")

        # =====================================================================
        # 1. SPECIALTIES
        # =====================================================================
        spec_ortho = models.Specialty(name="Orthopedics", code="ORTHO", description="Musculoskeletal system, joints, and sports injuries.")
        spec_cardio = models.Specialty(name="Cardiology", code="CARDIO", description="Heart, cardiovascular system, and hypertension.")
        spec_genmed = models.Specialty(name="General Medicine", code="GENMED", description="Primary care, internal medicine, and acute evaluations.")
        spec_derma = models.Specialty(name="Dermatology", code="DERMA", description="Dermatologic disorders, skin lesions, and rash management.")
        
        db.add_all([spec_ortho, spec_cardio, spec_genmed, spec_derma])
        db.commit()

        # =====================================================================
        # 2. HOSPITALS (2 Hospitals)
        # =====================================================================
        h1 = models.Hospital(
            name="St. Jude Memorial Hospital",
            address="450 Healthcare Blvd, Metro City, NY 10001",
            license_number="HOSP-NY-88910",
            contact_email="admin@stjudehealth.org",
            phone="+1-555-019-2834",
            status="APPROVED"
        )
        h2 = models.Hospital(
            name="Metro Health General",
            address="120 University Ave, Metro City, NY 10005",
            license_number="HOSP-NY-99201",
            contact_email="onboarding@metrogeneral.org",
            phone="+1-555-442-8812",
            status="APPROVED"
        )
        db.add_all([h1, h2])
        db.commit()
        db.refresh(h1)
        db.refresh(h2)

        # =====================================================================
        # 3. HOSPITAL ADMINISTRATORS
        # =====================================================================
        admin1 = models.HospitalAdmin(
            hospital_id=h1.id,
            full_name="David Miller",
            email="david.miller@stjudehealth.org",
            phone="+1-555-019-2800",
            role_title="Chief Medical Operations Director"
        )
        admin2 = models.HospitalAdmin(
            hospital_id=h2.id,
            full_name="Claire Vance",
            email="claire.vance@metrogeneral.org",
            phone="+1-555-442-8801",
            role_title="Clinical Operations Administrator"
        )
        db.add_all([admin1, admin2])
        db.commit()

        # =====================================================================
        # 4. DEPARTMENTS
        # =====================================================================
        # Departments for St. Jude
        dept_ortho = models.Department(hospital_id=h1.id, name="Orthopedic & Joint Surgery", code="DEP-ORTHO-01", description="Joint reconstruction, trauma, and sports medicine.")
        dept_cardio = models.Department(hospital_id=h1.id, name="Cardiovascular Institute", code="DEP-CARD-01", description="Heart failure, non-invasive imaging, and preventive cardiology.")
        dept_genmed = models.Department(hospital_id=h1.id, name="General Internal Medicine", code="DEP-IM-01", description="Comprehensive adult outpatient primary care.")

        # Departments for Metro Health
        dept_derma = models.Department(hospital_id=h2.id, name="Dermatology & Skin Center", code="DEP-DERM-02", description="Clinical dermatology and laser therapies.")
        dept_metro_primary = models.Department(hospital_id=h2.id, name="Family Medicine & Urgent Care", code="DEP-FAM-02", description="Walk-in and scheduled primary care.")

        db.add_all([dept_ortho, dept_cardio, dept_genmed, dept_derma, dept_metro_primary])
        db.commit()

        # =====================================================================
        # 5. DOCTORS (Distributed across hospitals)
        # =====================================================================
        # St. Jude Doctors
        doc_sarah = models.Doctor(
            hospital_id=h1.id,
            department_id=dept_ortho.id,
            specialty_id=spec_ortho.id,
            full_name="Dr. Sarah Jenkins, MD",
            specialty="Orthopedics",
            bio="Board-certified orthopedic surgeon specializing in knee arthroscopy, sports trauma, and joint preservation.",
            consultation_fee=180.0,
            slot_duration_min=30,
            external_provider_id="EPIC-PROV-101",
            is_active=True
        )
        doc_elena = models.Doctor(
            hospital_id=h1.id,
            department_id=dept_cardio.id,
            specialty_id=spec_cardio.id,
            full_name="Dr. Elena Rostova, MD",
            specialty="Cardiology",
            bio="Non-invasive cardiologist with 14 years experience in cardiovascular health, arrhythmia, and hypertension.",
            consultation_fee=220.0,
            slot_duration_min=30,
            external_provider_id="EPIC-PROV-102",
            is_active=True
        )
        doc_arthur = models.Doctor(
            hospital_id=h1.id,
            department_id=dept_genmed.id,
            specialty_id=spec_genmed.id,
            full_name="Dr. Arthur Pendelton, MD",
            specialty="General Medicine",
            bio="Primary care specialist focused on evidence-based chronic disease management and diagnostic evaluations.",
            consultation_fee=140.0,
            slot_duration_min=30,
            external_provider_id="EPIC-PROV-103",
            is_active=True
        )

        # Metro Health Doctors
        doc_marcus = models.Doctor(
            hospital_id=h2.id,
            department_id=dept_derma.id,
            specialty_id=spec_derma.id,
            full_name="Dr. Marcus Vance, MD",
            specialty="Dermatology",
            bio="Clinical dermatologist specializing in inflammatory skin diseases, acute rashes, eczema, and psoriasis.",
            consultation_fee=165.0,
            slot_duration_min=30,
            external_provider_id="CERNER-PROV-201",
            is_active=True
        )
        doc_maya = models.Doctor(
            hospital_id=h2.id,
            department_id=dept_metro_primary.id,
            specialty_id=spec_genmed.id,
            full_name="Dr. Maya Lin, MD",
            specialty="General Medicine",
            bio="Family physician providing comprehensive care for acute respiratory symptoms and preventative health.",
            consultation_fee=150.0,
            slot_duration_min=30,
            external_provider_id="CERNER-PROV-202",
            is_active=True
        )

        all_doctors = [doc_sarah, doc_elena, doc_arthur, doc_marcus, doc_maya]
        db.add_all(all_doctors)
        db.commit()

        # =====================================================================
        # 6. CALENDARS & WORKING HOURS (AVAILABILITY)
        # =====================================================================
        now = datetime.utcnow()

        for doc in all_doctors:
            cal = models.Calendar(
                hospital_id=doc.hospital_id,
                doctor_id=doc.id,
                name=f"Clinical Schedule - {doc.full_name}",
                timezone="America/New_York"
            )
            db.add(cal)
            db.commit()
            db.refresh(cal)

            # Define working hours Mon-Fri (09:00 - 17:00)
            for day_idx in range(0, 5):
                avail = models.Availability(
                    hospital_id=doc.hospital_id,
                    doctor_id=doc.id,
                    calendar_id=cal.id,
                    day_of_week=day_idx,
                    start_time="09:00",
                    end_time="17:00",
                    slot_duration_minutes=doc.slot_duration_min
                )
                db.add(avail)

                # Compatibility schedule table
                compat_sched = models.AvailabilitySchedule(
                    doctor_id=doc.id,
                    day_of_week=day_idx,
                    start_time="09:00",
                    end_time="17:00",
                    is_active=True
                )
                db.add(compat_sched)

            db.commit()

            # Generate 7 days of available time slots
            SlotService.generate_slots_for_doctor(db, doc.id, days_ahead=7)

        db.commit()

        # =====================================================================
        # 7. BLOCKED PERIODS (BLOCKED SLOTS)
        # =====================================================================
        # Block 1: Dr. Sarah Jenkins - Surgery block on Day +2 afternoon
        block_date_1 = (now + timedelta(days=2)).replace(hour=13, minute=0, second=0, microsecond=0)
        b1 = models.BlockedSlot(
            hospital_id=h1.id,
            doctor_id=doc_sarah.id,
            start_time=block_date_1,
            end_time=block_date_1 + timedelta(hours=3),
            reason="Operating Room - Knee Arthroscopy Block"
        )

        # Block 2: Dr. Elena Rostova - Cardiology rounds on Day +3 morning
        block_date_2 = (now + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
        b2 = models.BlockedSlot(
            hospital_id=h1.id,
            doctor_id=doc_elena.id,
            start_time=block_date_2,
            end_time=block_date_2 + timedelta(hours=2),
            reason="Inpatient Cardiovascular Rounds"
        )

        # Block 3: Dr. Marcus Vance - Academic conference on Day +4
        block_date_3 = (now + timedelta(days=4)).replace(hour=14, minute=0, second=0, microsecond=0)
        b3 = models.BlockedSlot(
            hospital_id=h2.id,
            doctor_id=doc_marcus.id,
            start_time=block_date_3,
            end_time=block_date_3 + timedelta(hours=2),
            reason="Grand Rounds & Clinical Presentation"
        )

        db.add_all([b1, b2, b3])
        db.commit()

        # =====================================================================
        # 8. PRE-VISIT QUESTIONNAIRES (Approved hospital configurations)
        # =====================================================================
        q_ortho = models.Questionnaire(
            hospital_id=h1.id,
            specialty_id=spec_ortho.id,
            title="Orthopedic Intake & Mobility Questionnaire",
            description="Pre-consultation assessment of joint pain, swelling, and physical mobility.",
            questions=[
                {"id": "q1", "question": "Where is your pain primarily localized (e.g. knee, shoulder, hip)?", "type": "text", "required": True},
                {"id": "q2", "question": "Rate your current discomfort on a scale of 1 to 10:", "type": "scale", "min": 1, "max": 10, "required": True},
                {"id": "q3", "question": "How long have you experienced these symptoms?", "type": "choice", "options": ["Under 24 hours", "2-4 days", "1-2 weeks", "Over 1 month"], "required": True},
                {"id": "q4", "question": "Are you able to bear weight on the affected joint?", "type": "choice", "options": ["Yes, fully", "Yes, with difficulty", "No, cannot walk"], "required": True}
            ]
        )

        q_cardio = models.Questionnaire(
            hospital_id=h1.id,
            specialty_id=spec_cardio.id,
            title="Cardiovascular Pre-Visit Health Assessment",
            description="Routine pre-assessment for palpitations, blood pressure, and cardiac history.",
            questions=[
                {"id": "q1", "question": "Are you experiencing shortness of breath with mild exertion?", "type": "choice", "options": ["Yes", "No", "Only during vigorous exercise"], "required": True},
                {"id": "q2", "question": "Do you have a personal or family history of hypertension or heart disease?", "type": "text", "required": False},
                {"id": "q3", "question": "List any daily cardiovascular medications you currently take:", "type": "text", "required": False}
            ]
        )

        q_derma = models.Questionnaire(
            hospital_id=h2.id,
            specialty_id=spec_derma.id,
            title="Dermatology Skin Lesion & Rash Questionnaire",
            description="Pre-visit assessment for rashes, itching, and dermatological conditions.",
            questions=[
                {"id": "q1", "question": "What area of the skin is currently affected?", "type": "text", "required": True},
                {"id": "q2", "question": "Is the rash accompanied by itching, pain, or fever?", "type": "choice", "options": ["Severe itching", "Mild itching", "Painful to touch", "No discomfort"], "required": True},
                {"id": "q3", "question": "Have you applied any topical steroid or over-the-counter creams?", "type": "text", "required": False}
            ]
        )

        db.add_all([q_ortho, q_cardio, q_derma])
        db.commit()

        # =====================================================================
        # 9. DEMO PATIENT
        # =====================================================================
        patient = models.Patient(
            primary_hospital_id=h1.id,
            patient_mrn="MRN-009182",
            full_name="Alice Morgan",
            phone="+1-555-832-1920",
            email="alice.morgan@example.com",
            date_of_birth="1992-04-15",
            gender="Female",
            emergency_contact="Bob Morgan (+1-555-832-1921)",
            communication_preference="SMS",
            preferences={"preferred_times": "morning", "language": "en"}
        )
        db.add(patient)

        # =====================================================================
        # 10. CHAOS SETTING (For Mock EHR Timeout Recovery Demonstrations)
        # =====================================================================
        if not db.query(models.ChaosSetting).first():
            chaos = models.ChaosSetting(
                id=1,
                mode="TIMEOUT_AFTER_SAVE",
                simulated_delay_ms=2500,
                failure_active=True
            )
            db.add(chaos)

        db.commit()

        # Ensure demo users and approved questionnaires are provisioned
        _ensure_seed_users(db)
        _ensure_seed_questionnaires(db)

        print("Successfully seeded all hospitals, departments, doctors, calendars, slots, blocked periods, questionnaires, and users!")

    finally:
        db.close()

def _ensure_seed_users(db):
    """Provisions authenticable demo users with secure bcrypt hashes for all 4 PRD roles."""
    from backend.auth.security import hash_password
    from backend.auth.roles import UserRole

    # 1. Platform Admin
    p_admin = db.query(models.User).filter(models.User.email == "platform.admin@aegiscare.io").first()
    if not p_admin:
        p_admin = models.User(
            email="platform.admin@aegiscare.io",
            full_name="Platform Super Administrator",
            role=UserRole.PLATFORM_ADMIN.value,
            password_hash=hash_password("PlatformAdmin123!"),
            is_active=True
        )
        db.add(p_admin)
        db.commit()

    # 2. Hospital Admins
    admin1_rec = db.query(models.HospitalAdmin).filter(models.HospitalAdmin.email == "david.miller@stjudehealth.org").first()
    h1_user = db.query(models.User).filter(models.User.email == "david.miller@stjudehealth.org").first()
    if not h1_user:
        h1_user = models.User(
            email="david.miller@stjudehealth.org",
            full_name="David Miller",
            role=UserRole.HOSPITAL_ADMIN.value,
            hospital_id=admin1_rec.hospital_id if admin1_rec else None,
            password_hash=hash_password("AdminPass123!"),
            is_active=True
        )
        db.add(h1_user)
        db.commit()
    if admin1_rec and not admin1_rec.user_id:
        admin1_rec.user_id = h1_user.id
        db.commit()

    admin2_rec = db.query(models.HospitalAdmin).filter(models.HospitalAdmin.email == "claire.vance@metrogeneral.org").first()
    h2_user = db.query(models.User).filter(models.User.email == "claire.vance@metrogeneral.org").first()
    if not h2_user:
        h2_user = models.User(
            email="claire.vance@metrogeneral.org",
            full_name="Claire Vance",
            role=UserRole.HOSPITAL_ADMIN.value,
            hospital_id=admin2_rec.hospital_id if admin2_rec else None,
            password_hash=hash_password("AdminPass123!"),
            is_active=True
        )
        db.add(h2_user)
        db.commit()
    if admin2_rec and not admin2_rec.user_id:
        admin2_rec.user_id = h2_user.id
        db.commit()

    # 3. Doctors
    doc_sarah = db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Sarah Jenkins%")).first()
    sarah_user = db.query(models.User).filter(models.User.email == "sarah.jenkins@stjudehealth.org").first()
    if not sarah_user:
        sarah_user = models.User(
            email="sarah.jenkins@stjudehealth.org",
            full_name="Dr. Sarah Jenkins, MD",
            role=UserRole.DOCTOR.value,
            hospital_id=doc_sarah.hospital_id if doc_sarah else None,
            password_hash=hash_password("DoctorPass123!"),
            is_active=True
        )
        db.add(sarah_user)
        db.commit()
    if doc_sarah and not doc_sarah.user_id:
        doc_sarah.user_id = sarah_user.id
        db.commit()

    doc_marcus = db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Marcus Vance%")).first()
    marcus_user = db.query(models.User).filter(models.User.email == "marcus.vance@metrogeneral.org").first()
    if not marcus_user:
        marcus_user = models.User(
            email="marcus.vance@metrogeneral.org",
            full_name="Dr. Marcus Vance, MD",
            role=UserRole.DOCTOR.value,
            hospital_id=doc_marcus.hospital_id if doc_marcus else None,
            password_hash=hash_password("DoctorPass123!"),
            is_active=True
        )
        db.add(marcus_user)
        db.commit()
    if doc_marcus and not doc_marcus.user_id:
        doc_marcus.user_id = marcus_user.id
        db.commit()

    # 4. Patient Alice
    pat_alice = db.query(models.Patient).filter(models.Patient.email == "alice.morgan@example.com").first()
    alice_user = db.query(models.User).filter(models.User.email == "alice.morgan@example.com").first()
    if not alice_user:
        alice_user = models.User(
            email="alice.morgan@example.com",
            full_name="Alice Morgan",
            role=UserRole.PATIENT.value,
            hospital_id=pat_alice.primary_hospital_id if pat_alice else None,
            password_hash=hash_password("PatientPass123!"),
            is_active=True
        )
        db.add(alice_user)
        db.commit()
    if pat_alice and not pat_alice.user_id:
        pat_alice.user_id = alice_user.id
        db.commit()

def _ensure_seed_questionnaires(db):
    """Provisions approved multi-type questionnaires for hospital, specialty, and doctor tiers."""
    h1 = db.query(models.Hospital).filter(models.Hospital.license_number == "HOSP-NY-88910").first()
    if not h1:
        return

    # Check if templates already exist
    existing = db.query(models.Questionnaire).filter(models.Questionnaire.hospital_id == h1.id).first()
    if existing:
        return

    spec_ortho = db.query(models.Specialty).filter(models.Specialty.code == "ORTHO").first()
    spec_cardio = db.query(models.Specialty).filter(models.Specialty.code == "CARDIO").first()
    doc_sarah = db.query(models.Doctor).filter(models.Doctor.full_name.ilike("%Sarah Jenkins%")).first()

    # Template 1: Comprehensive Adult Intake (Supports all 8 question types)
    q_general = models.Questionnaire(
        hospital_id=h1.id,
        title="Comprehensive Pre-Visit Intake Questionnaire",
        description="Approved standard adult outpatient intake covering all standard health factors.",
        appointment_type="ALL",
        condition_category="GENERAL_INTAKE",
        questions=[
            {
                "id": "known_allergies",
                "question": "Do you have any known medication or severe environmental allergies?",
                "type": "yes_no",
                "required": True
            },
            {
                "id": "symptom_duration",
                "question": "How long have you been experiencing the primary concern for this visit?",
                "type": "choice",
                "options": ["Less than 24 hours", "2-3 days", "1-2 weeks", "More than 1 month"],
                "required": True
            },
            {
                "id": "associated_symptoms",
                "question": "Which of the following associated symptoms have you noticed?",
                "type": "multiple_choice",
                "options": ["Mild fever", "Fatigue", "Headache", "Nausea", "None of these"],
                "required": False
            },
            {
                "id": "pain_scale",
                "question": "On a scale of 1 to 10, how severe is your current pain or discomfort?",
                "type": "numeric",
                "min": 1,
                "max": 10,
                "required": True,
                "urgency_rules": {"min_threshold": 8, "reason": "Severe pain level (8+ out of 10) reported by patient"}
            },
            {
                "id": "onset_date",
                "question": "Approximate date when you first noticed this symptom or issue?",
                "type": "date",
                "required": False
            },
            {
                "id": "current_meds",
                "question": "What medications or supplements are you currently taking?",
                "type": "short_text",
                "placeholder": "e.g., Lisinopril 10mg, Metformin 500mg, Multivitamin",
                "required": False
            },
            {
                "id": "patient_narrative",
                "question": "In your own words, please describe how this issue impacts your daily activities.",
                "type": "long_text",
                "placeholder": "e.g., Difficulty walking down stairs, trouble sleeping...",
                "required": False
            },
            {
                "id": "emergency_contact_info",
                "question": "Please provide your primary emergency contact details.",
                "type": "structured_fields",
                "fields": [
                    {"id": "contact_name", "label": "Full Name", "type": "short_text"},
                    {"id": "relationship", "label": "Relationship", "type": "choice", "options": ["Spouse", "Parent", "Sibling", "Friend", "Other"]},
                    {"id": "phone_number", "label": "Phone Number", "type": "short_text"}
                ],
                "required": False
            }
        ],
        is_active=True,
        is_approved=True,
        approved_by="David Miller (Chief Medical Operations Director)",
        version=1
    )

    # Template 2: Orthopedic Pre-Procedure Evaluation (Associated with Dr. Sarah Jenkins)
    q_ortho = models.Questionnaire(
        hospital_id=h1.id,
        doctor_id=doc_sarah.id if doc_sarah else None,
        specialty_id=spec_ortho.id if spec_ortho else None,
        title="Dr. Sarah Jenkins - Knee & Joint Pre-Consultation",
        description="Specialized musculoskeletal questionnaire for joint discomfort and mobility.",
        appointment_type="IN_PERSON",
        condition_category="ORTHOPEDICS",
        questions=[
            {
                "id": "prior_injury",
                "question": "Have you had a previous injury or surgery to this joint?",
                "type": "yes_no",
                "required": True
            },
            {
                "id": "weight_bearing",
                "question": "Are you currently able to bear weight on the affected limb?",
                "type": "choice",
                "options": ["Full weight without pain", "Partial weight with limp", "Unable to bear weight"],
                "required": True
            },
            {
                "id": "pain_scale",
                "question": "On a scale of 1 to 10, what is your current joint discomfort level?",
                "type": "numeric",
                "min": 1,
                "max": 10,
                "required": True,
                "urgency_rules": {"min_threshold": 8, "reason": "Severe joint pain (8+ out of 10)"}
            },
            {
                "id": "injury_date",
                "question": "Approximate date the discomfort or injury began?",
                "type": "date",
                "required": False
            },
            {
                "id": "symptoms_description",
                "question": "Please describe any mechanical symptoms (e.g., clicking, catching, giving way).",
                "type": "short_text",
                "placeholder": "e.g., Knee buckles when descending stairs",
                "required": False
            }
        ],
        is_active=True,
        is_approved=True,
        approved_by="Dr. Sarah Jenkins, MD",
        version=1
    )

    # Template 3: Cardiology Pre-Visit Screener (Associated with St. Jude Cardiology)
    q_cardio = models.Questionnaire(
        hospital_id=h1.id,
        specialty_id=spec_cardio.id if spec_cardio else None,
        title="Cardiovascular Institute - Pre-Visit Health Screener",
        description="Cardiology screening protocol for outpatient cardiology consultations.",
        appointment_type="ALL",
        condition_category="CARDIOLOGY",
        questions=[
            {
                "id": "chest_discomfort",
                "question": "Are you currently experiencing any active chest tightness, pressure, or squeezing?",
                "type": "yes_no",
                "required": True,
                "urgency_rules": {"trigger_values": ["yes", "Yes"], "reason": "Active chest discomfort reported in cardiology screener"}
            },
            {
                "id": "shortness_of_breath",
                "question": "Do you experience sudden shortness of breath while resting or lying flat?",
                "type": "yes_no",
                "required": True,
                "urgency_rules": {"trigger_values": ["yes", "Yes"], "reason": "Resting dyspnea / orthopnea reported"}
            },
            {
                "id": "frequency",
                "question": "How often do these cardiovascular episodes or sensations occur?",
                "type": "choice",
                "options": ["Constant", "Multiple times a day", "Occasionally with exertion", "Rarely"],
                "required": True
            },
            {
                "id": "current_cardiac_meds",
                "question": "Please list any blood pressure, cholesterol, or heart medications you take.",
                "type": "short_text",
                "placeholder": "e.g., Atorvastatin 20mg, Metoprolol 50mg",
                "required": False
            }
        ],
        is_active=True,
        is_approved=True,
        approved_by="David Miller (Chief Medical Operations Director)",
        version=1
    )

    db.add_all([q_general, q_ortho, q_cardio])
    db.commit()

if __name__ == "__main__":
    seed_database()
