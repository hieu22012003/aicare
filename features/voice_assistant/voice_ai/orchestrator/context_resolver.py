"""
Operational Context Resolver for Elderly Voice AI Sessions.
Implements Section 3, Section 4, and Phase 4 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("voice_ai.orchestrator.context")


class ContextResolver:
    """
    Resolves minimal, non-PII operational context for an authenticated elder session.
    Protects user privacy by providing only necessary workflow metadata.
    """

    def __init__(self, db_manager: Optional[Any] = None) -> None:
        self.db_manager = db_manager

    def resolve_context(
        self,
        patient_id: str,
        current_screen: Optional[str] = None,
        extra_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Resolves operational context:
        - time_of_day: morning / afternoon / evening / night
        - current_screen: DASHBOARD / MEDICATION / MUSIC / GAMES
        - pending_medications: list of medication names due around the current time
        """
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 11:
            time_of_day = "morning"
        elif 11 <= hour < 14:
            time_of_day = "noon"
        elif 14 <= hour < 18:
            time_of_day = "afternoon"
        elif 18 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        screen = (current_screen or "DASHBOARD").strip().upper()

        pending_meds: List[str] = []
        if extra_state and "pending_medications" in extra_state:
            pending_meds = [str(m).strip()[:80] for m in extra_state["pending_medications"]][:5]
        elif self.db_manager:
            try:
                # Query scheduled medications due today for this patient if db available
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT medication_name FROM medication_schedules 
                        WHERE patient_id = ? AND is_active = 1
                        LIMIT 5
                        """,
                        (patient_id,),
                    )
                    rows = cursor.fetchall()
                    for r in rows:
                        pending_meds.append(r[0] if isinstance(r, (list, tuple)) else r["medication_name"])
            except Exception as ex:
                logger.debug(f"Could not query medication schedules from DB: {ex}")

        return {
            "time_of_day": time_of_day,
            "current_screen": screen,
            "pending_medications": pending_meds,
        }

