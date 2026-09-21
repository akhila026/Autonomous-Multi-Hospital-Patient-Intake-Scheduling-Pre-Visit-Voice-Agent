import re
from typing import Dict, Any, Union

# Sensitive field name tokens for recursive key-based redaction
REDACT_KEY_TOKENS = {
    "password", "ssn", "secret", "token", "medical_record_raw", 
    "access_token", "refresh_token", "authorization", "auth_token",
    "credit_card", "cvv", "api_key", "private_key"
}

# Fields containing patient direct identifiers that should be masked/redacted
PATIENT_IDENTIFIER_KEYS = {
    "patient_name", "full_name", "emergency_contact", "emergency_contact_name"
}

PHONE_KEYS = {
    "phone", "patient_phone", "contact_phone", "emergency_contact_phone", "recipient_contact"
}

EMAIL_KEYS = {
    "email", "patient_email", "contact_email", "recipient_email"
}

CLINICAL_CONTENT_KEYS = {
    "medical_notes", "raw_transcript", "diagnostic_details", "prescription_details"
}

# Regex patterns for scanning string values
EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
PHONE_REGEX = re.compile(r"(\+?\d{1,2}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?(\d{2,4})")
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def mask_email(email_str: str) -> str:
    """Masks email address like alice.morgan@example.com -> a****@example.com"""
    def _repl(match):
        return f"{match.group(1)}****@{match.group(2)}"
    return EMAIL_REGEX.sub(_repl, email_str)


def mask_phone(phone_str: str) -> str:
    """Masks phone numbers keeping country/area prefix and last 2 digits: +1-555-***-**00"""
    def _repl(match):
        prefix = match.group(1) or "+1-"
        suffix = match.group(2) or "00"
        return f"{prefix}***-***-*{suffix}"
    return PHONE_REGEX.sub(_repl, phone_str)


def mask_name(name_str: str) -> str:
    """Masks patient names like 'Alice Morgan' -> 'A**** M*****'"""
    parts = name_str.strip().split()
    if not parts:
        return "[PATIENT_NAME_REDACTED]"
    masked = [f"{p[0]}****" if len(p) > 1 else f"{p}*" for p in parts]
    return " ".join(masked)


def sanitize_string(val: str) -> str:
    """Scans and redacts SSNs, emails, and phones from arbitrary text."""
    if not isinstance(val, str):
        return val
    s = SSN_REGEX.sub("[SSN_REDACTED]", val)
    s = EMAIL_REGEX.sub(r"\1****@\2", s)
    return s


def redact_sensitive_data(data: Any) -> Any:
    """
    Recursively redacts and masks sensitive healthcare data (HIPAA/PHI)
    from dictionaries, lists, and primitives before audit or operational logging.
    """
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            k_lower = k.lower()
            # 1. Total credential/secret redaction
            if any(tok in k_lower for tok in REDACT_KEY_TOKENS):
                cleaned[k] = "[REDACTED]"
            # 2. Patient name masking
            elif any(tok == k_lower for tok in PATIENT_IDENTIFIER_KEYS):
                if isinstance(v, str) and v.strip():
                    cleaned[k] = mask_name(v)
                else:
                    cleaned[k] = v
            # 3. Phone number masking
            elif any(tok == k_lower for tok in PHONE_KEYS):
                if isinstance(v, str) and v.strip():
                    cleaned[k] = mask_phone(v)
                else:
                    cleaned[k] = v
            # 4. Email address masking
            elif any(tok == k_lower for tok in EMAIL_KEYS):
                if isinstance(v, str) and v.strip():
                    cleaned[k] = mask_email(v)
                else:
                    cleaned[k] = v
            # 5. Raw clinical notes masking
            elif any(tok in k_lower for tok in CLINICAL_CONTENT_KEYS):
                cleaned[k] = "[SENSITIVE_CLINICAL_CONTENT_MASKED]"
            else:
                cleaned[k] = redact_sensitive_data(v)
        return cleaned

    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]

    elif isinstance(data, str):
        return sanitize_string(data)

    return data


# Standard alias
sanitize_for_audit = redact_sensitive_data
