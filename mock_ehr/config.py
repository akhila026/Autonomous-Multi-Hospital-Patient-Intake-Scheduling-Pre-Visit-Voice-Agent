import os

class MockEhrConfig:
    SERVICE_NAME: str = "Mock EHR External Service"
    HOST: str = os.getenv("MOCK_EHR_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("MOCK_EHR_PORT", "8001"))
    DEFAULT_CHAOS_MODE: str = os.getenv("DEFAULT_CHAOS_MODE", "TIMEOUT_AFTER_SAVE")
    DEFAULT_DELAY_MS: int = int(os.getenv("MOCK_EHR_DELAY_MS", "2500"))

config = MockEhrConfig()
