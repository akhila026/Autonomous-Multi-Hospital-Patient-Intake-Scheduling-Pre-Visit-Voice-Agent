from typing import Dict, Any, Optional
from backend.agent.context import ConversationContext
from backend.agent.prompts import HEALTHCARE_SYSTEM_PROMPT, EMERGENCY_SIGNS
from backend.capabilities.registry import capability_registry
from backend.capabilities.base import CapabilityContext

class AgentRunner:
    """
    Coordinates conversational turns with the patient:
    - Analyzes natural language input
    - Validates safety boundaries & emergency red-flags
    - Maintains conversational state
    - Triggers controlled capabilities
    """
    def __init__(self, context: ConversationContext):
        self.context = context

    async def process_turn(self, user_utterance: str) -> Dict[str, Any]:
        """
        Executes a single conversational turn with safety checks and capability invocation.
        """
        lower = user_utterance.lower()

        # 1. Emergency safety check
        if any(sign in lower for sign in EMERGENCY_SIGNS):
            self.context.urgency_level = "EMERGENCY"
            return {
                "reply": "⚠️ Critical Medical Alert: Based on your symptoms, please dial 911 or proceed to the nearest Emergency Room immediately.",
                "is_emergency": True,
                "urgency_level": "EMERGENCY",
                "action_taken": "emergency_escalation"
            }

        # 2. Administrative intent detection & capability execution
        return {
            "reply": "How can I help you find care or manage your appointments today?",
            "is_emergency": False,
            "urgency_level": self.context.urgency_level,
            "action_taken": "clarification"
        }
