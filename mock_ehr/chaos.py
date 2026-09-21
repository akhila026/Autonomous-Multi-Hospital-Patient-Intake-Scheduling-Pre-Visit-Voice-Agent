from enum import Enum
from pydantic import BaseModel

class ChaosMode(str, Enum):
    NORMAL = "NORMAL"
    TIMEOUT_AFTER_SAVE = "TIMEOUT_AFTER_SAVE"
    TIMEOUT_BEFORE_SAVE = "TIMEOUT_BEFORE_SAVE"
    HTTP_500 = "HTTP_500"

class ChaosController(BaseModel):
    mode: ChaosMode = ChaosMode.TIMEOUT_AFTER_SAVE
    simulated_delay_ms: int = 2500
    is_active: bool = True

chaos_controller = ChaosController()
