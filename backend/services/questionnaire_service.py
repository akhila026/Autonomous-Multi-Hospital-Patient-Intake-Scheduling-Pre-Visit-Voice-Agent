import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from backend import models, schemas
from backend.auth.roles import UserRole

class QuestionnaireService:
    """
    Comprehensive Pre-Visit Questionnaire Service (PRD Sections 3, 10, 15, 20, 22, 28).
    Manages approved questionnaire templates, appointment auto-assignment,
    conversational response collection into structured data, urgency escalation,
    and doctor review workflows.
    """

    # Forbidden terms that indicate unauthorized clinical assessment/diagnosis/prescribing
    FORBIDDEN_CLINICAL_TERMS = [
        "prescribe", "prescription", "rx",
        "diagnose", "diagnosis",
        "start taking", "stop taking", "adjust dosage", "modify dose",
        "recommended treatment", "medical assessment"
    ]

    # Red-flag terms for emergency escalation policy
    ACUTE_URGENT_KEYWORDS = [
        "chest pain", "shortness of breath", "difficulty breathing",
        "loss of consciousness", "passed out", "fainting",
        "severe bleeding", "uncontrolled bleeding", "stroke",
        "sudden paralysis", "facial drooping", "slurred speech",
        "anaphylaxis", "severe allergic reaction", "throat swelling"
    ]

    # =========================================================================
    # 1. TEMPLATE MANAGEMENT (Hospital & Doctor Administrators)
    # =========================================================================

    @staticmethod
    def validate_clinical_boundaries(questions: List[Dict[str, Any]]) -> None:
        """
        Validates that configured questionnaire templates strictly adhere to clinical boundaries:
        MUST NOT contain questions formulating diagnoses, recommending treatments,
        prescribing medications, or executing independent clinical assessment.
        """
        for q in questions:
            q_text = (q.get("question") or "").lower()
            q_type = (q.get("type") or "").lower()

            for term in QuestionnaireService.FORBIDDEN_CLINICAL_TERMS:
                if term in q_text:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Clinical Safety Boundary Violation: Questionnaire question '{q.get('question')}' "
                            f"contains unauthorized clinical term '{term}'. Questionnaires must strictly gather "
                            f"patient-reported intake facts and cannot formulate diagnoses or suggest treatments."
                        )
                    )

            # Validate question types
            valid_types = set(schemas.SUPPORTED_QUESTION_TYPES) | {"text", "scale"}
            if q_type not in valid_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported question type '{q_type}'. Supported types are: {', '.join(schemas.SUPPORTED_QUESTION_TYPES)}"
                )

    @staticmethod
    def create_template(
        db: Session,
        req: schemas.QuestionnaireTemplateCreate,
        current_user: Optional[models.User] = None
    ) -> models.Questionnaire:
        """
        Creates an approved questionnaire template associated with hospital, specialty,
        doctor, appointment type, or condition category.
        """
        # Tenant & Role Authorization
        if current_user:
            if current_user.role not in [UserRole.HOSPITAL_ADMIN.value, UserRole.DOCTOR.value, UserRole.SYSTEM_ADMIN.value]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Unauthorized: Only hospital or doctor administrators can configure questionnaires."
                )
            if current_user.role == UserRole.HOSPITAL_ADMIN.value and current_user.hospital_id != req.hospital_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant Isolation Violation: Cannot create questionnaire for a different hospital tenant."
                )

        # Enforce clinical boundary guardrails
        QuestionnaireService.validate_clinical_boundaries(req.questions)

        # Ensure doctor belongs to hospital if specified
        if req.doctor_id:
            doctor = db.query(models.Doctor).filter(models.Doctor.id == req.doctor_id).first()
            if not doctor:
                raise HTTPException(status_code=404, detail="Doctor not found")
            if doctor.hospital_id != req.hospital_id:
                raise HTTPException(status_code=400, detail="Doctor does not belong to specified hospital tenant")

        template = models.Questionnaire(
            hospital_id=req.hospital_id,
            specialty_id=req.specialty_id,
            doctor_id=req.doctor_id,
            title=req.title,
            description=req.description,
            appointment_type=req.appointment_type or "ALL",
            condition_category=req.condition_category or "GENERAL_INTAKE",
            questions=req.questions,
            is_active=True,
            is_approved=req.is_approved if req.is_approved is not None else True,
            approved_by=req.approved_by or (current_user.full_name if current_user else "Admin"),
            version=1
        )
        db.add(template)
        db.commit()
        db.refresh(template)

        # Audit Event
        db.add(models.AuditEvent(
            hospital_id=template.hospital_id,
            actor_id=current_user.id if current_user else "SYSTEM",
            actor_role=current_user.role if current_user else "ADMIN",
            action="CREATE_QUESTIONNAIRE_TEMPLATE",
            resource_type="QUESTIONNAIRE_TEMPLATE",
            resource_id=template.id,
            status="SUCCESS",
            details={"title": template.title, "questions_count": len(template.questions)}
        ))
        db.commit()

        return template

    @staticmethod
    def get_templates(
        db: Session,
        hospital_id: Optional[str] = None,
        specialty_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        appointment_type: Optional[str] = None
    ) -> List[models.Questionnaire]:
        query = db.query(models.Questionnaire).filter(models.Questionnaire.is_active == True)
        if hospital_id:
            query = query.filter(models.Questionnaire.hospital_id == hospital_id)
        if specialty_id:
            query = query.filter(models.Questionnaire.specialty_id == specialty_id)
        if doctor_id:
            query = query.filter(models.Questionnaire.doctor_id == doctor_id)
        if appointment_type:
            query = query.filter(
                (models.Questionnaire.appointment_type == appointment_type) |
                (models.Questionnaire.appointment_type == "ALL")
            )
        return query.order_by(models.Questionnaire.created_at.desc()).all()

    @staticmethod
    def get_template_by_id(db: Session, template_id: str) -> models.Questionnaire:
        template = db.query(models.Questionnaire).filter(models.Questionnaire.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Questionnaire template not found")
        return template

    @staticmethod
    def update_template(
        db: Session,
        template_id: str,
        req: schemas.QuestionnaireTemplateUpdate,
        current_user: Optional[models.User] = None
    ) -> models.Questionnaire:
        template = QuestionnaireService.get_template_by_id(db, template_id)

        if current_user:
            if current_user.role == UserRole.HOSPITAL_ADMIN.value and current_user.hospital_id != template.hospital_id:
                raise HTTPException(status_code=403, detail="Tenant Isolation: Cannot modify questionnaire of another hospital.")

        if req.questions is not None:
            QuestionnaireService.validate_clinical_boundaries(req.questions)
            template.questions = req.questions
            template.version += 1

        if req.title is not None:
            template.title = req.title
        if req.description is not None:
            template.description = req.description
        if req.appointment_type is not None:
            template.appointment_type = req.appointment_type
        if req.condition_category is not None:
            template.condition_category = req.condition_category
        if req.is_active is not None:
            template.is_active = req.is_active
        if req.is_approved is not None:
            template.is_approved = req.is_approved
        if req.approved_by is not None:
            template.approved_by = req.approved_by

        template.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def delete_template(db: Session, template_id: str, current_user: Optional[models.User] = None) -> bool:
        template = QuestionnaireService.get_template_by_id(db, template_id)
        if current_user and current_user.role == UserRole.HOSPITAL_ADMIN.value and current_user.hospital_id != template.hospital_id:
            raise HTTPException(status_code=403, detail="Tenant Isolation: Cannot delete questionnaire of another hospital.")
        template.is_active = False
        db.commit()
        return True

    # =========================================================================
    # 2. AUTO-ASSIGNMENT WORKFLOW
    # =========================================================================

    @staticmethod
    def assign_questionnaire_to_appointment(
        db: Session,
        appointment: models.Appointment
    ) -> models.QuestionnaireResponse:
        """
        Assigns the best matching approved questionnaire template to a booked appointment.
        Priority:
        1. Doctor specific
        2. Specialty specific
        3. Appointment Type specific
        4. Hospital default
        5. General Clinical Intake Default
        """
        doctor = appointment.doctor
        hospital_id = appointment.hospital_id

        # 1. Check Doctor specific
        template = None
        if doctor:
            template = db.query(models.Questionnaire).filter(
                models.Questionnaire.hospital_id == hospital_id,
                models.Questionnaire.doctor_id == doctor.id,
                models.Questionnaire.is_active == True,
                models.Questionnaire.is_approved == True
            ).first()

        # 2. Check Specialty specific
        if not template and doctor and doctor.specialty:
            template = db.query(models.Questionnaire).filter(
                models.Questionnaire.hospital_id == hospital_id,
                models.Questionnaire.condition_category.ilike(f"%{doctor.specialty}%"),
                models.Questionnaire.is_active == True,
                models.Questionnaire.is_approved == True
            ).first()

        # 3. Check Appointment Type specific
        if not template:
            appt_type = getattr(appointment, "appointment_type", "IN_PERSON") or "IN_PERSON"
            template = db.query(models.Questionnaire).filter(
                models.Questionnaire.hospital_id == hospital_id,
                models.Questionnaire.appointment_type == appt_type,
                models.Questionnaire.is_active == True,
                models.Questionnaire.is_approved == True
            ).first()

        # 4. Check Hospital default
        if not template:
            template = db.query(models.Questionnaire).filter(
                models.Questionnaire.hospital_id == hospital_id,
                models.Questionnaire.is_active == True,
                models.Questionnaire.is_approved == True
            ).first()

        # 5. Default approved question items if no custom template found
        if template:
            questions = template.questions
            template_id = template.id
        else:
            template_id = None
            specialty_name = doctor.specialty if doctor else "General Practice"
            complaint = appointment.chief_complaint or "General consultation"
            questions = [
                {
                    "id": "symptom_duration",
                    "question": f"How long have you experienced symptoms related to '{complaint}'?",
                    "type": "choice",
                    "options": ["Less than 24 hours", "2-3 days", "1-2 weeks", "More than a month"],
                    "required": True
                },
                {
                    "id": "pain_scale",
                    "question": "On a scale of 1 to 10, how severe is your discomfort right now?",
                    "type": "numeric",
                    "min": 1,
                    "max": 10,
                    "required": True,
                    "urgency_rules": {"min_threshold": 8, "reason": "Severe acute discomfort level reported (8+ out of 10)"}
                },
                {
                    "id": "current_medications",
                    "question": "Are you currently taking any prescribed or over-the-counter medications?",
                    "type": "short_text",
                    "placeholder": "e.g. Lisinopril 10mg daily, none",
                    "required": False
                },
                {
                    "id": "allergies",
                    "question": f"Do you have any known allergies or relevant medical conditions for {specialty_name}?",
                    "type": "short_text",
                    "placeholder": "e.g. Penicillin, latex, none",
                    "required": False
                },
                {
                    "id": "emergency_symptoms",
                    "question": "Are you currently experiencing any chest pain, severe shortness of breath, or dizziness?",
                    "type": "yes_no",
                    "required": True,
                    "urgency_rules": {"trigger_values": ["yes", "Yes"], "reason": "Potential acute emergency symptom indicated (chest pain / shortness of breath / dizziness)"}
                }
            ]

        # Create or update QuestionnaireResponse
        resp = db.query(models.QuestionnaireResponse).filter(
            models.QuestionnaireResponse.appointment_id == appointment.id
        ).first()

        if not resp:
            resp = models.QuestionnaireResponse(
                questionnaire_id=template_id,
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                hospital_id=appointment.hospital_id,
                answers={},
                status="ASSIGNED",
                is_urgent=False
            )
            db.add(resp)
        else:
            resp.questionnaire_id = template_id

        # Also maintain PreVisitQuestionnaire for backward compatibility
        pvq = db.query(models.PreVisitQuestionnaire).filter(
            models.PreVisitQuestionnaire.appointment_id == appointment.id
        ).first()

        if not pvq:
            pvq = models.PreVisitQuestionnaire(
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                questions=questions,
                status="ASSIGNED",
                is_urgent=False
            )
            db.add(pvq)
        else:
            pvq.questions = questions

        db.commit()
        db.refresh(resp)

        # Audit Event
        db.add(models.AuditEvent(
            hospital_id=appointment.hospital_id,
            actor_id="SYSTEM",
            actor_role="SYSTEM",
            action="QUESTIONNAIRE_ASSIGNED",
            resource_type="QUESTIONNAIRE_ASSIGNMENT",
            resource_id=resp.id,
            status="SUCCESS",
            details={
                "appointment_id": appointment.id,
                "template_id": template_id,
                "questions_count": len(questions)
            }
        ))
        db.commit()

        return resp

    # =========================================================================
    # 3. CONVERSATIONAL EXTRACTION & RESPONSE COLLECTION
    # =========================================================================

    @staticmethod
    def extract_answers_conversationally(
        questions: List[Dict[str, Any]],
        existing_answers: Dict[str, Any],
        utterance: str
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[str]]:
        """
        Extracts structured questionnaire values from patient natural language utterances.
        Maps patient expressions to the exact predefined questions without inventing new questions.
        Returns: (newly_extracted_answers, next_unanswered_question, urgent_reasons)
        """
        extracted = {}
        urgent_reasons = []
        lower_utterance = utterance.lower().strip()

        # Check global acute emergency red flags in patient statement
        for kw in QuestionnaireService.ACUTE_URGENT_KEYWORDS:
            if kw in lower_utterance:
                urgent_reasons.append(f"Patient reported acute symptom: '{kw}'")

        # Find unanswered questions in priority order
        unanswered = [q for q in questions if q.get("id") not in existing_answers]

        for q in questions:
            qid = q.get("id")
            if qid in existing_answers:
                continue

            q_type = q.get("type", "short_text").lower()
            val = None

            if q_type == "yes_no":
                # Check for affirmative/negative patterns
                if re.search(r"\b(yes|yeah|yep|yup|affirmative|i do|i have|true)\b", lower_utterance):
                    val = "Yes"
                elif re.search(r"\b(no|nope|nah|negative|i do not|i don't|i haven't|false)\b", lower_utterance):
                    val = "No"

            elif q_type in ["numeric", "scale"]:
                # Extract number, prioritizing scale context (e.g. 4 out of 10, 8/10) or avoiding duration units
                scale_match = re.search(r"\b([0-9]|10)\s*(?:/|out of)\s*10\b", lower_utterance)
                if scale_match:
                    val = int(scale_match.group(1))
                else:
                    context_match = re.search(r"(?:discomfort|pain|level|scale|rate|severity|is|around|about)\s+(?:is\s+)?([0-9]|10)\b", lower_utterance)
                    if context_match:
                        val = int(context_match.group(1))
                    else:
                        for m in re.finditer(r"\b([0-9]|10)\b(?!\s*(?:day|days|hour|hours|week|weeks|month|months|year|years))", lower_utterance):
                            val = int(m.group(1))
                            break

            elif q_type == "date":
                # Date extraction (e.g. "2026-09-18", "yesterday", "last week")
                date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b", lower_utterance)
                if date_match:
                    val = date_match.group(1)
                elif "yesterday" in lower_utterance:
                    val = "Yesterday"
                elif "today" in lower_utterance:
                    val = "Today"
                elif "week" in lower_utterance:
                    val = "1 week ago"

            elif q_type in ["choice", "multiple_choice"]:
                options = q.get("options", [])
                for opt in options:
                    if opt.lower() in lower_utterance:
                        val = opt
                        break
                # If duration choices
                if not val and "duration" in qid:
                    if any(t in lower_utterance for t in ["day", "days", "3 days", "yesterday"]):
                        val = "2-3 days"
                    elif any(t in lower_utterance for t in ["week", "weeks"]):
                        val = "1-2 weeks"
                    elif any(t in lower_utterance for t in ["month", "months"]):
                        val = "More than a month"
                    elif any(t in lower_utterance for t in ["hour", "hours", "today"]):
                        val = "Less than 24 hours"

            elif q_type in ["short_text", "long_text", "text"]:
                # If conversational utterance is providing context for this question
                if qid in ["symptoms", "chief_complaint", "current_medications", "allergies", "description"]:
                    val = utterance

            elif q_type == "structured_fields":
                # Extract any structured key-values
                subfields = q.get("fields", [])
                struct_res = {}
                for sf in subfields:
                    sf_id = sf.get("id")
                    if sf_id and sf_id in lower_utterance:
                        struct_res[sf_id] = "Reported"
                if struct_res:
                    val = struct_res

            # Fallback for the currently active prompt if utterance contains relevant text
            if val is None and unanswered and unanswered[0].get("id") == qid:
                if q_type in ["short_text", "long_text", "text"]:
                    val = utterance
                elif q_type in ["choice", "multiple_choice"] and len(lower_utterance.split()) <= 6:
                    val = utterance

            if val is not None:
                extracted[qid] = val
                # Check question specific urgency rules
                urgency_rules = q.get("urgency_rules")
                if urgency_rules:
                    trigger_vals = urgency_rules.get("trigger_values", [])
                    if any(str(tv).lower() == str(val).lower() for tv in trigger_vals):
                        urgent_reasons.append(urgency_rules.get("reason", f"Urgent response triggered for {q.get('question')}"))

                    min_thresh = urgency_rules.get("min_threshold")
                    if min_thresh is not None and isinstance(val, (int, float)) and val >= min_thresh:
                        urgent_reasons.append(urgency_rules.get("reason", f"Discomfort score {val} meets urgent threshold >= {min_thresh}"))

        # Determine next unanswered question
        merged = {**existing_answers, **extracted}
        remaining = [q for q in questions if q.get("id") not in merged]
        next_q = remaining[0] if remaining else None

        return extracted, next_q, urgent_reasons

    @staticmethod
    def process_conversational_turn(
        db: Session,
        appointment_id: str,
        patient_utterance: str
    ) -> schemas.QuestionnaireConversationalTurnResponse:
        """
        Executes a single conversational collection turn.
        Extracts structured responses, checks urgency, updates state, and generates next prompt.
        """
        quest = QuestionnaireService.get_by_appointment(db, appointment_id)
        questions = quest.questions or []
        existing_answers = quest.answers or {}

        extracted, next_q, turn_urgent_reasons = QuestionnaireService.extract_answers_conversationally(
            questions=questions,
            existing_answers=existing_answers,
            utterance=patient_utterance
        )

        all_answers = {**existing_answers, **extracted}
        quest.answers = all_answers

        # Urgency check
        is_urgent, full_urgent_reasons = QuestionnaireService.check_urgency(questions, all_answers)
        if turn_urgent_reasons:
            for r in turn_urgent_reasons:
                if r not in full_urgent_reasons:
                    full_urgent_reasons.append(r)
            is_urgent = True

        if is_urgent:
            quest.is_urgent = True
            quest.urgent_reasons = full_urgent_reasons
            quest.status = "URGENT_ESCALATED"
            # Emit Urgent Audit Event
            db.add(models.AuditEvent(
                hospital_id=quest.appointment.hospital_id if quest.appointment else None,
                actor_id=quest.patient_id,
                actor_role="PATIENT",
                action="QUESTIONNAIRE_URGENT_ESCALATION",
                resource_type="QUESTIONNAIRE_RESPONSE",
                resource_id=quest.id,
                status="URGENT_ALERT",
                details={"urgent_reasons": full_urgent_reasons, "answers": all_answers}
            ))

        missing = [q for q in questions if q.get("id") not in all_answers and q.get("required", True)]
        is_complete = (len(missing) == 0)

        if is_complete:
            quest.status = "URGENT_ESCALATED" if is_urgent else "SUBMITTED"
            quest.submitted_at = datetime.utcnow()
            QuestionnaireService._compile_intake_summary(quest, quest.appointment)
            prompt_msg = (
                "Thank you for submitting your pre-visit intake information. "
                "Your responses have been recorded and sent to your doctor for review before your appointment."
            )
            if is_urgent:
                prompt_msg = (
                    "⚠️ IMPORTANT MEDICAL NOTICE: Based on the symptoms you reported ("
                    + ", ".join(full_urgent_reasons)
                    + "), this has been flagged for urgent doctor escalation. If you are experiencing "
                    "severe discomfort, chest pain, or difficulty breathing, please seek immediate emergency care or call 911."
                )
        else:
            if quest.status not in ["URGENT_ESCALATED", "SUBMITTED"]:
                quest.status = "IN_PROGRESS"
            if next_q:
                options_hint = f" (Options: {', '.join(next_q.get('options', []))})" if next_q.get("options") else ""
                next_prompt = f"{next_q.get('question')}{options_hint}"
            else:
                next_prompt = "Please provide any additional relevant details for your doctor."

            if is_urgent:
                prompt_msg = (
                    "⚠️ IMPORTANT MEDICAL NOTICE: Based on the symptoms you reported ("
                    + ", ".join(full_urgent_reasons)
                    + "), this has been flagged for urgent doctor escalation. If you are experiencing "
                    "severe discomfort, chest pain, or difficulty breathing, please seek immediate emergency care or call 911.\n\n"
                    + next_prompt
                )
            else:
                prompt_msg = next_prompt

        # Keep QuestionnaireResponse in sync
        qr = db.query(models.QuestionnaireResponse).filter(
            models.QuestionnaireResponse.appointment_id == appointment_id
        ).first()
        if qr:
            qr.answers = all_answers
            qr.status = quest.status
            qr.is_urgent = quest.is_urgent
            qr.urgent_reasons = quest.urgent_reasons
            qr.patient_intake_summary = quest.patient_intake_summary
            if is_complete:
                qr.submitted_at = quest.submitted_at

        db.commit()
        db.refresh(quest)

        return schemas.QuestionnaireConversationalTurnResponse(
            appointment_id=appointment_id,
            extracted_answers=extracted,
            all_answers=all_answers,
            missing_questions=missing,
            next_question=next_q,
            ai_prompt_message=prompt_msg,
            is_complete=is_complete,
            is_urgent=is_urgent,
            urgent_reasons=full_urgent_reasons
        )

    # =========================================================================
    # 4. SUBMISSION & URGENCY ESCALATION POLICY
    # =========================================================================

    @staticmethod
    def check_urgency(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Applies clinical escalation policy to evaluate potentially urgent information.
        """
        reasons = []
        is_urgent = False

        for q in questions:
            qid = q.get("id")
            val = answers.get(qid)
            if val is None:
                continue

            # Check question urgency rules
            urgency_rules = q.get("urgency_rules")
            if urgency_rules:
                trigger_vals = urgency_rules.get("trigger_values", [])
                if any(str(tv).lower() == str(val).lower() for tv in trigger_vals):
                    is_urgent = True
                    reasons.append(urgency_rules.get("reason", f"Urgent answer '{val}' for '{q.get('question')}'"))

                min_thresh = urgency_rules.get("min_threshold")
                if min_thresh is not None:
                    try:
                        num_val = float(val) if not isinstance(val, (int, float)) else val
                        if num_val >= min_thresh:
                            is_urgent = True
                            reasons.append(urgency_rules.get("reason", f"Discomfort level {num_val} exceeds urgent threshold ({min_thresh})"))
                    except (ValueError, TypeError):
                        pass

            # Global red flags check on string values
            val_str = str(val).lower()
            for kw in QuestionnaireService.ACUTE_URGENT_KEYWORDS:
                if kw in val_str:
                    is_urgent = True
                    reasons.append(f"Reported critical clinical indicator: '{kw}'")

            # Check pain score in general numeric questions
            if "pain" in qid.lower() or "severity" in qid.lower():
                try:
                    num_val = int(re.search(r"\d+", str(val)).group()) if re.search(r"\d+", str(val)) else None
                    if num_val and num_val >= 8:
                        is_urgent = True
                        reasons.append(f"Severe pain scale reported ({num_val}/10)")
                except Exception:
                    pass

        return is_urgent, list(set(reasons))

    @staticmethod
    def _compile_intake_summary(quest: Any, appt: Any) -> str:
        """
        Compiles an objective, structured patient-reported intake summary.
        STRICT COMPLIANCE:
        - Must NOT include diagnostic impressions or speculation.
        - Must NOT include treatment proposals or medication prescriptions.
        - Clearly formats patient-reported facts for doctor review.
        """
        patient_name = appt.patient.full_name if (appt and appt.patient) else "Patient"
        gender = appt.patient.gender if (appt and appt.patient and appt.patient.gender) else "Unspecified"
        dob = appt.patient.date_of_birth if (appt and appt.patient and appt.patient.date_of_birth) else "N/A"
        complaint = appt.chief_complaint if appt else "General consultation"
        urgency = appt.urgency_level if appt else "ROUTINE"

        patient_facts = [f"• {k}: {v}" for k, v in (quest.answers or {}).items()]
        formatted_facts = (
            "\n".join(patient_facts)
            if patient_facts
            else "• No additional responses provided."
        )

        urgency_alert = ""
        if getattr(quest, "is_urgent", False):
            reasons = getattr(quest, "urgent_reasons", []) or []
            urgency_alert = (
                f"\n⚠️ CLINICAL ESCALATION ALERT: Potentially urgent symptoms flagged by intake rules:\n"
                + "\n".join([f"  - {r}" for r in reasons])
                + "\n"
            )

        summary = (
            f"**Patient-Reported Intake Summary**\n"
            f"• Patient: {patient_name} ({gender}, DOB: {dob})\n"
            f"• Reported Concern: {complaint}\n"
            f"• Reported Urgency: {urgency}\n"
            f"{urgency_alert}"
            f"• Patient-Reported Responses:\n{formatted_facts}"
        )

        quest.patient_intake_summary = summary
        return summary

    @staticmethod
    def submit_answers(
        db: Session,
        appointment_id: str,
        req: schemas.QuestionnaireSubmitRequest
    ) -> models.PreVisitQuestionnaire:
        """
        Submits structured responses, evaluates urgency escalation, and compiles intake summary.
        Maintains 100% backward compatibility with PreVisitQuestionnaire and QuestionnaireResponse.
        """
        quest = QuestionnaireService.get_by_appointment(db, appointment_id)
        appt = quest.appointment
        questions = quest.questions or []

        quest.answers = req.answers
        quest.submitted_at = datetime.utcnow()

        is_urgent, urgent_reasons = QuestionnaireService.check_urgency(questions, req.answers)
        quest.is_urgent = is_urgent
        quest.urgent_reasons = urgent_reasons
        quest.status = "URGENT_ESCALATED" if is_urgent else "SUBMITTED"

        # Compile factual, patient-reported intake summary without clinical impressions or diagnoses (PRD Section 3, 15, 20)
        QuestionnaireService._compile_intake_summary(quest, appt)

        # Sync QuestionnaireResponse
        qr = db.query(models.QuestionnaireResponse).filter(
            models.QuestionnaireResponse.appointment_id == appointment_id
        ).first()
        if qr:
            qr.answers = req.answers
            qr.status = quest.status
            qr.is_urgent = is_urgent
            qr.urgent_reasons = urgent_reasons
            qr.patient_intake_summary = quest.patient_intake_summary
            qr.submitted_at = quest.submitted_at

        # Audit Event
        action_name = "QUESTIONNAIRE_URGENT_ESCALATION" if is_urgent else "SUBMIT_QUESTIONNAIRE"
        db.add(models.AuditEvent(
            hospital_id=appt.hospital_id if appt else None,
            actor_id=quest.patient_id,
            actor_role="PATIENT",
            action=action_name,
            resource_type="QUESTIONNAIRE_SUBMISSION",
            resource_id=quest.id,
            status="URGENT_ALERT" if is_urgent else "SUCCESS",
            details={
                "appointment_id": appointment_id,
                "is_urgent": is_urgent,
                "urgent_reasons": urgent_reasons,
                "answers_count": len(req.answers)
            }
        ))

        db.commit()
        db.refresh(quest)

        # Dispatch QUESTIONNAIRE_COMPLETED (and HUMAN_ESCALATION if urgent)
        try:
            from backend.workflows.events import event_dispatcher, DomainEvent, EventType
            import asyncio
            ev_completed = DomainEvent(
                event_type=EventType.QUESTIONNAIRE_COMPLETED,
                entity_id=quest.id,
                tenant_id=appt.hospital_id if appt else None,
                payload={
                    "appointment_id": appointment_id,
                    "patient_id": quest.patient_id,
                    "is_urgent": is_urgent,
                    "urgent_reasons": urgent_reasons
                }
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(event_dispatcher.dispatch(ev_completed))
                if is_urgent:
                    ev_urgent = DomainEvent(
                        event_type=EventType.HUMAN_ESCALATION,
                        entity_id=quest.id,
                        tenant_id=appt.hospital_id if appt else None,
                        payload={
                            "reason": f"Urgent questionnaire intake: {', '.join(urgent_reasons)}",
                            "appointment_id": appointment_id,
                            "patient_id": quest.patient_id
                        }
                    )
                    loop.create_task(event_dispatcher.dispatch(ev_urgent))
            except RuntimeError:
                asyncio.run(event_dispatcher.dispatch(ev_completed))
        except Exception as ev_err:
            logger.warning(f"Failed to dispatch questionnaire domain events: {ev_err}")

        return quest

    # =========================================================================
    # 5. DOCTOR REVIEW & CLINICAL QUEUE
    # =========================================================================

    @staticmethod
    def get_by_appointment(db: Session, appointment_id: str) -> models.PreVisitQuestionnaire:
        """
        Retrieves questionnaire for appointment. Guarantees finding or creating the record.
        """
        quest = db.query(models.PreVisitQuestionnaire).filter(
            models.PreVisitQuestionnaire.appointment_id == appointment_id
        ).first()

        if not quest:
            # Check QuestionnaireResponse and convert or create
            qr = db.query(models.QuestionnaireResponse).filter(
                models.QuestionnaireResponse.appointment_id == appointment_id
            ).first()
            if qr:
                appt = qr.appointment
                questions = qr.questionnaire.questions if qr.questionnaire else []
                quest = models.PreVisitQuestionnaire(
                    appointment_id=appointment_id,
                    patient_id=qr.patient_id,
                    questions=questions,
                    answers=qr.answers,
                    patient_intake_summary=qr.patient_intake_summary,
                    status=qr.status,
                    is_urgent=qr.is_urgent,
                    urgent_reasons=qr.urgent_reasons,
                    reviewed_by=qr.reviewed_by,
                    reviewed_at=qr.reviewed_at,
                    doctor_notes=qr.doctor_notes,
                    submitted_at=qr.submitted_at
                )
                db.add(quest)
                db.commit()
                db.refresh(quest)
            else:
                raise HTTPException(status_code=404, detail="Questionnaire not found for this appointment")
        return quest

    @staticmethod
    def get_pending_reviews_for_doctor(
        db: Session,
        doctor_id: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieves submitted questionnaires requiring doctor review for a specific doctor's queue.
        """
        # Join appointments with questionnaires
        records = db.query(models.PreVisitQuestionnaire).join(
            models.Appointment, models.PreVisitQuestionnaire.appointment_id == models.Appointment.id
        ).filter(
            models.Appointment.doctor_id == doctor_id,
            models.PreVisitQuestionnaire.status.in_(["SUBMITTED", "URGENT_ESCALATED", "REVIEWED"])
        ).order_by(
            models.PreVisitQuestionnaire.is_urgent.desc(),
            models.PreVisitQuestionnaire.submitted_at.desc()
        ).all()

        results = []
        for r in records:
            appt = r.appointment
            results.append({
                "id": r.id,
                "appointment_id": r.appointment_id,
                "patient_id": r.patient_id,
                "patient_name": appt.patient.full_name if (appt and appt.patient) else "Unknown",
                "chief_complaint": appt.chief_complaint if appt else None,
                "slot_time": appt.slot.start_time.isoformat() if (appt and appt.slot and appt.slot.start_time) else None,
                "questions": r.questions or [],
                "answers": r.answers or {},
                "patient_intake_summary": r.patient_intake_summary,
                "status": r.status,
                "is_urgent": bool(r.is_urgent),
                "urgent_reasons": r.urgent_reasons or [],
                "reviewed_by": r.reviewed_by,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "doctor_notes": r.doctor_notes,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None
            })
        return results

    @staticmethod
    def submit_doctor_review(
        db: Session,
        appointment_id: str,
        doctor_id: str,
        doctor_notes: Optional[str] = None
    ) -> models.PreVisitQuestionnaire:
        """
        Records the doctor's review of the pre-visit intake summary.
        """
        quest = QuestionnaireService.get_by_appointment(db, appointment_id)
        appt = quest.appointment

        if appt and appt.doctor_id != doctor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doctor Review Authorization Violation: You can only review questionnaires for your own appointments."
            )

        quest.reviewed_by = doctor_id
        quest.reviewed_at = datetime.utcnow()
        quest.doctor_notes = doctor_notes
        quest.status = "REVIEWED"

        # Sync QuestionnaireResponse
        qr = db.query(models.QuestionnaireResponse).filter(
            models.QuestionnaireResponse.appointment_id == appointment_id
        ).first()
        if qr:
            qr.reviewed_by = doctor_id
            qr.reviewed_at = quest.reviewed_at
            qr.doctor_notes = doctor_notes
            qr.status = "REVIEWED"

        # Audit Event
        db.add(models.AuditEvent(
            hospital_id=appt.hospital_id if appt else None,
            actor_id=doctor_id,
            actor_role="DOCTOR",
            action="QUESTIONNAIRE_REVIEWED",
            resource_type="QUESTIONNAIRE_REVIEW",
            resource_id=quest.id,
            status="SUCCESS",
            details={
                "appointment_id": appointment_id,
                "doctor_notes_present": bool(doctor_notes)
            }
        ))

        db.commit()
        db.refresh(quest)
        return quest
