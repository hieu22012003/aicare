"""
Abstract Base Class for Semantic Interpretation Providers.
Defines unified interface for Gemini Flash API, local SLMs, and test mocks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from features.voice_assistant.voice_ai.domain.contracts import SemanticResult


class SemanticProvider(ABC):
    """Abstract interface for natural language semantic interpretation."""

    @abstractmethod
    def analyze(
        self,
        transcript: str,
        sanitized_context: Optional[Dict[str, Any]] = None,
    ) -> SemanticResult:
        """
        Analyzes transcript and returns a validated, schema-compliant SemanticResult.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if the semantic provider is configured and available."""
        pass

    @abstractmethod
    def get_provider_info(self) -> Dict[str, Any]:
        """Returns provider metadata (model_id, prompt_version, provider_name)."""
        pass

