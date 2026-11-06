"""
Client for the Backend STT Gateway.
Handles HTTP transmission outside the UI thread, auth token attachment,
and clean client-side error normalization.
Implements Section 4 & 5 of KE_HOACH_TICH_HOP_API_STT.md.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from features.voice_assistant.voice_ai.stt.contracts import SttErrorCode, SttRequest, SttResponse
from features.voice_assistant.voice_ai.stt.gateway import BackendSttGateway

logger = logging.getLogger("SttClient")


class SttClient:
    """
    Client used by UI Controller to transcribe audio via Backend STT Gateway.
    Can operate via HTTP REST call or in-process direct Gateway dispatch.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout_seconds: float = 20.0,
        direct_gateway: Optional[BackendSttGateway] = None,
    ) -> None:
        self.gateway_url = gateway_url or "http://127.0.0.1:8080/api/v1/voice/stt"
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self.direct_gateway = direct_gateway

    def transcribe_audio(
        self,
        request: SttRequest,
        audio_bytes: bytes,
    ) -> SttResponse:
        """
        Transcribes audio by calling either in-process gateway or HTTP endpoint.
        """
        start_t = time.time()

        # 1. Direct in-process gateway shortcut (fastest for desktop app running embedded backend)
        if self.direct_gateway is not None:
            return self.direct_gateway.process_stt_request(
                request=request,
                raw_audio_bytes=audio_bytes,
                auth_context={"user_id": "desktop_app", "role": "patient", "patient_id": request.patient_id},
            )

        # 2. HTTP POST transmission to backend gateway
        if not audio_bytes or len(audio_bytes) < 44:
            return SttResponse(
                request_id=request.request_id,
                voice_session_id=request.voice_session_id,
                status="failed",
                error_code=SttErrorCode.NO_AUDIO.value,
                latency_ms=0,
            )

        b64_audio = base64.b64encode(audio_bytes).decode("ascii")
        payload = {
            "request_id": request.request_id,
            "voice_session_id": request.voice_session_id,
            "patient_id": request.patient_id,
            "language": request.language,
            "context_type": request.context_type,
            "context_id": request.context_id,
            "context_version": request.context_version,
            "duration_ms": request.duration_ms,
            "audio_base64": b64_audio,
        }

        body_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AI-Rehab-Elder-Desktop/1.0",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            req = urllib.request.Request(self.gateway_url, data=body_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                latency_ms = int((time.time() - start_t) * 1000)

                return SttResponse(
                    request_id=resp_data.get("request_id", request.request_id),
                    voice_session_id=resp_data.get("voice_session_id", request.voice_session_id),
                    status=resp_data.get("status", "completed"),
                    transcript=resp_data.get("transcript", ""),
                    language=resp_data.get("language", request.language),
                    latency_ms=resp_data.get("latency_ms", latency_ms),
                    error_code=resp_data.get("error_code"),
                    confidence=resp_data.get("confidence"),
                )

        except urllib.error.HTTPError as he:
            latency_ms = int((time.time() - start_t) * 1000)
            logger.warning("[SttClient] HTTP %d from gateway", he.code)
            error_code = SttErrorCode.PROVIDER_UNAVAILABLE.value
            if he.code in (401, 403):
                error_code = SttErrorCode.FORBIDDEN.value
            elif he.code == 429:
                error_code = SttErrorCode.RATE_LIMITED.value
            elif he.code == 400:
                error_code = SttErrorCode.INVALID_REQUEST.value

            return SttResponse(
                request_id=request.request_id,
                voice_session_id=request.voice_session_id,
                status="failed",
                error_code=error_code,
                latency_ms=latency_ms,
            )

        except (urllib.error.URLError, TimeoutError, OSError) as ue:
            latency_ms = int((time.time() - start_t) * 1000)
            logger.warning("[SttClient] Network error connecting to gateway: %s", ue)
            err_code = SttErrorCode.TIMEOUT.value if "timed out" in str(ue).lower() else SttErrorCode.PROVIDER_UNAVAILABLE.value
            return SttResponse(
                request_id=request.request_id,
                voice_session_id=request.voice_session_id,
                status="failed",
                error_code=err_code,
                latency_ms=latency_ms,
            )

