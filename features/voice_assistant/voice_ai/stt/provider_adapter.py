"""
STT Provider Adapters.
Encapsulates external vendor API calls, formats, models, and error mapping.
Secret keys are strictly accessed on the backend.
Implements Section 4 & 5 of KE_HOACH_TICH_HOP_API_STT.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import io
import json
import logging
import os
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request
import uuid

from features.voice_assistant.voice_ai.stt.contracts import SttErrorCode, SttResponse

logger = logging.getLogger("SttProviderAdapter")


class BaseSTTProviderAdapter(ABC):
    """Abstract interface for STT Provider Adapters."""

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "vi",
        request_id: Optional[str] = None,
        voice_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SttResponse:
        """
        Transcribes audio bytes to standardized SttResponse.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks if adapter is configured and ready."""
        pass


class MockSTTProviderAdapter(BaseSTTProviderAdapter):
    """
    Deterministic mock provider adapter for offline tests and headless verification.
    Supports injecting specific responses, errors, or latencies.
    """

    def __init__(self, default_text: str = "Tôi đã uống thuốc rồi") -> None:
        self.default_text = default_text
        self._preset_transcript: Optional[str] = None
        self._simulated_error: Optional[str] = None
        self._simulated_latency_sec: float = 0.0

    def set_preset_transcript(self, text: Optional[str]) -> None:
        self._preset_transcript = text
        self._simulated_error = None

    def simulate_error(self, error_code: str) -> None:
        self._simulated_error = error_code

    def reset(self) -> None:
        self._preset_transcript = None
        self._simulated_error = None
        self._simulated_latency_sec = 0.0

    def is_available(self) -> bool:
        return True

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "vi",
        request_id: Optional[str] = None,
        voice_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SttResponse:
        rid = request_id or str(uuid.uuid4())
        sid = voice_session_id or str(uuid.uuid4())
        start_t = time.time()

        if self._simulated_latency_sec > 0:
            time.sleep(self._simulated_latency_sec)

        latency_ms = int((time.time() - start_t) * 1000)

        if self._simulated_error:
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=self._simulated_error,
                language=language,
                latency_ms=latency_ms,
            )

        if not audio_bytes or len(audio_bytes) < 44:
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.NO_AUDIO.value,
                language=language,
                latency_ms=latency_ms,
            )

        text = self._preset_transcript if self._preset_transcript is not None else self.default_text
        return SttResponse(
            request_id=rid,
            voice_session_id=sid,
            status="completed",
            transcript=text,
            language=language,
            latency_ms=latency_ms,
            confidence=0.95,
        )


class GenericHttpSTTProviderAdapter(BaseSTTProviderAdapter):
    """
    Standard HTTP Provider Adapter for cloud STT APIs.
    Supports OpenAI-compatible /v1/audio/transcriptions and REST JSON endpoints.
    Secret API key is read from backend environment.
    Applies timeout (default 20s), single retry on 5xx, and error normalization.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_endpoint: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key or os.getenv("STT_API_KEY", "")
        self.api_endpoint = api_endpoint or os.getenv(
            "STT_API_ENDPOINT", "https://api.openai.com/v1/audio/transcriptions"
        )
        self.model_name = model_name or os.getenv("STT_API_MODEL", "whisper-1")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_endpoint)

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "vi",
        request_id: Optional[str] = None,
        voice_session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SttResponse:
        rid = request_id or str(uuid.uuid4())
        sid = voice_session_id or str(uuid.uuid4())
        start_t = time.time()

        if not self.is_available():
            logger.error("STT Provider API Key or Endpoint is not configured.")
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.PROVIDER_UNAVAILABLE.value,
                latency_ms=0,
            )

        if not audio_bytes or len(audio_bytes) < 44:
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.NO_AUDIO.value,
                latency_ms=0,
            )

        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        body_io = io.BytesIO()

        # Add model field
        body_io.write(f"--{boundary}\r\n".encode("utf-8"))
        body_io.write(b'Content-Disposition: form-data; name="model"\r\n\r\n')
        body_io.write(f"{self.model_name}\r\n".encode("utf-8"))

        # Add language field
        body_io.write(f"--{boundary}\r\n".encode("utf-8"))
        body_io.write(b'Content-Disposition: form-data; name="language"\r\n\r\n')
        body_io.write(f"{language}\r\n".encode("utf-8"))

        # Add file field
        body_io.write(f"--{boundary}\r\n".encode("utf-8"))
        body_io.write(b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n')
        body_io.write(b"Content-Type: audio/wav\r\n\r\n")
        body_io.write(audio_bytes)
        body_io.write(b"\r\n")

        # Close multipart
        body_io.write(f"--{boundary}--\r\n".encode("utf-8"))
        payload_bytes = body_io.getvalue()

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {self.api_key}",
        }

        attempts = 0
        last_error_code = SttErrorCode.PROVIDER_UNAVAILABLE.value

        while attempts <= self.max_retries:
            attempts += 1
            try:
                req = urllib.request.Request(
                    self.api_endpoint,
                    data=payload_bytes,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    resp_bytes = resp.read()
                    data = json.loads(resp_bytes.decode("utf-8"))
                    transcript = data.get("text", "").strip()
                    latency_ms = int((time.time() - start_t) * 1000)

                    return SttResponse(
                        request_id=rid,
                        voice_session_id=sid,
                        status="completed",
                        transcript=transcript,
                        language=language,
                        latency_ms=latency_ms,
                    )

            except urllib.error.HTTPError as he:
                status_code = he.code
                logger.warning("STT Provider HTTP Error %d on attempt %d", status_code, attempts)
                if status_code in (401, 403):
                    last_error_code = SttErrorCode.FORBIDDEN.value
                    break  # Do not retry auth errors
                elif status_code == 429:
                    last_error_code = SttErrorCode.RATE_LIMITED.value
                    time.sleep(0.5)
                elif 500 <= status_code < 600:
                    last_error_code = SttErrorCode.PROVIDER_UNAVAILABLE.value
                    time.sleep(0.5)
                else:
                    last_error_code = SttErrorCode.INVALID_REQUEST.value
                    break

            except (urllib.error.URLError, TimeoutError, OSError) as ue:
                logger.warning("STT Provider Network Error: %s on attempt %d", ue, attempts)
                if "timed out" in str(ue).lower():
                    last_error_code = SttErrorCode.TIMEOUT.value
                else:
                    last_error_code = SttErrorCode.PROVIDER_UNAVAILABLE.value
                time.sleep(0.5)

        latency_ms = int((time.time() - start_t) * 1000)
        return SttResponse(
            request_id=rid,
            voice_session_id=sid,
            status="failed",
            error_code=last_error_code,
            language=language,
            latency_ms=latency_ms,
        )


_active_adapter: Optional[BaseSTTProviderAdapter] = None


def get_stt_provider_adapter(mode: Optional[str] = None) -> BaseSTTProviderAdapter:
    """
    Returns active STT Provider Adapter.
    - 'mock': deterministic mock adapter
    - 'http' / 'real': generic HTTP adapter with secret backend credentials
    - default: reads STT_PROVIDER or falls back to mock if no credentials configured.
    """
    global _active_adapter
    selected_mode = (mode or os.getenv("STT_PROVIDER", "auto")).lower()

    if selected_mode == "mock":
        return MockSTTProviderAdapter()

    if selected_mode in ("http", "real"):
        return GenericHttpSTTProviderAdapter()

    # auto mode: if API key is provisioned, use HTTP adapter, else mock
    if os.getenv("STT_API_KEY"):
        return GenericHttpSTTProviderAdapter()

    if _active_adapter is None:
        _active_adapter = MockSTTProviderAdapter()
    return _active_adapter


def set_stt_provider_adapter(adapter: BaseSTTProviderAdapter) -> None:
    """Inject adapter for testing."""
    global _active_adapter
    _active_adapter = adapter

