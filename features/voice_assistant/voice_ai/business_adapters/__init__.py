"""
Business Flow Adapters for Voice AI.
Connects validated intents to backend business aggregates.
"""

from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
from features.voice_assistant.voice_ai.business_adapters.health_adapter import HealthReportAdapter
from features.voice_assistant.voice_ai.business_adapters.medication_adapter import MedicationAdapter
from features.voice_assistant.voice_ai.business_adapters.media_games_adapter import MediaAndGamesAdapter

__all__ = [
    "DoctorAssistanceAdapter",
    "MedicationAdapter",
    "HealthReportAdapter",
    "MediaAndGamesAdapter",
]

