"""
Semantic Provider Factory.
Instantiates either GeminiSemanticProvider or MockSemanticProvider based on environment.
Enforces non-negotiable rule: In production or when real mode is requested,
automatic fallback to MockSemanticProvider is strictly prohibited.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from features.voice_assistant.voice_ai.semantic.gemini_provider import GeminiSemanticProvider
from features.voice_assistant.voice_ai.semantic.mock_provider import MockSemanticProvider
from features.voice_assistant.voice_ai.semantic.provider import SemanticProvider

logger = logging.getLogger("voice_ai.semantic.factory")


def get_semantic_provider(
    force_mock: bool = False,
    mode: Optional[str] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    **kwargs: Any,
) -> SemanticProvider:
    """
    Factory resolving the appropriate SemanticProvider instance.
    - If force_mock or VOICE_AI_FORCE_MOCK_SEMANTIC is set, returns MockSemanticProvider.
    - If running in production (VOICE_AI_ENV=production) or mode='real'/'gemini':
      Requires GEMINI_API_KEY; raises RuntimeError if missing (never silently falls back).
    - In offline development / test mode without key, defaults to MockSemanticProvider.
    """
    env_force = os.environ.get("VOICE_AI_FORCE_MOCK_SEMANTIC", "").lower() in ("true", "1", "yes")
    if force_mock or env_force:
        logger.info("Using MockSemanticProvider (explicitly requested or forced via env).")
        return MockSemanticProvider()

    env_mode = os.environ.get("VOICE_AI_SEMANTIC_MODE", "").strip().lower()
    is_production = os.environ.get("VOICE_AI_ENV", "").strip().lower() in ("prod", "production")
    effective_mode = (mode or env_mode).lower()

    key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()

    # Rule: Never silently fallback to Mock in production or when explicitly configured for real Gemini
    if is_production or effective_mode in ("real", "gemini"):
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured in production/real mode. "
                "Automatic fallback to MockSemanticProvider is strictly prohibited."
            )
        logger.info("Using GeminiSemanticProvider in production/real mode.")
        return GeminiSemanticProvider(
            api_key=key,
            model_name=model_name,
            **kwargs,
        )

    if key:
        logger.info("Using GeminiSemanticProvider with configured GEMINI_API_KEY.")
        return GeminiSemanticProvider(
            api_key=key,
            model_name=model_name,
            **kwargs,
        )

    logger.info(
        "GEMINI_API_KEY is not set. Defaulting to MockSemanticProvider for offline/local execution."
    )
    return MockSemanticProvider()
