import os

class Settings:
    PROJECT_NAME: str = "Healthcare AI Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./healthcare.db")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
    MOCK_EHR_DEFAULT_TIMEOUT_SEC: float = float(os.getenv("MOCK_EHR_TIMEOUT_SEC", "3.0"))
    SLOT_HOLD_DURATION_MINUTES: int = int(os.getenv("SLOT_HOLD_DURATION_MINUTES", "5"))
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "aegiscare-jwt-secret-key-production-strength-2026-v1")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")
    MOCK_EHR_BASE_URL: str = os.getenv("MOCK_EHR_BASE_URL", "")
    TIMEZONE: str = os.getenv("APPLICATION_TIMEZONE", os.getenv("TIMEZONE", "UTC"))

settings = Settings()

def get_app_now(tz: str = None):
    """
    Returns the current application datetime in the configured application timezone.
    If tz is UTC or unspecified, returns datetime.utcnow() matching database UTC representation.
    """
    from datetime import datetime
    tz_name = tz or settings.TIMEZONE
    if not tz_name or tz_name.upper() == "UTC":
        return datetime.utcnow()
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
