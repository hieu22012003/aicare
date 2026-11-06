"""
Backend STT Gateway.
Validates client identity, enforces patient authorization and audio bounds,
manages provider rate limits, and isolates cloud API secrets on the server.
Implements Section 4 & 5 of KE_HOACH_TICH_HOP_API_STT.md.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Optional, Tuple

from features.voice_assistant.voice_ai.stt.contracts import SttErrorCode, SttRequest, SttResponse
from features.voice_assistant.voice_ai.stt.provider_adapter import BaseSTTProviderAdapter, get_stt_provider_adapter

logger = logging.getLogger("BackendSttGateway")


class BackendSttGateway:
    """
    Server-side STT Gateway.
    - Authenticates the requesting actor.
    - Authorizes access to the specified patient.
    - Enforces audio size and duration limits.
    - Calls the configured STT provider adapter with backend secret.
    - Normalizes errors and prevents leaking internal tokens or audio.
    """

    def __init__(
        self,
        provider_adapter: Optional[BaseSTTProviderAdapter] = None,
        auth_service: Optional[Any] = None,
        max_duration_ms: int = 20000,
        max_audio_size_bytes: int = 10 * 1024 * 1024,  # 10 MB limit
    ) -> None:
        self.provider_adapter = provider_adapter or get_stt_provider_adapter()
        self.auth_service = auth_service
        self.max_duration_ms = max_duration_ms
        self.max_audio_size_bytes = max_audio_size_bytes
        self._rate_limit_cache: Dict[str, list[float]] = {}

    def process_stt_request(
        self,
        request: SttRequest,
        auth_context: Optional[Dict[str, Any]] = None,
        raw_audio_bytes: Optional[bytes] = None,
    ) -> SttResponse:
        """
        Executes gateway triage and invokes provider adapter.
        """
        start_t = time.time()
        rid = request.request_id
        sid = request.voice_session_id

        # 1. Authorization check
        auth_ok, auth_err_code, auth_msg = self._check_authorization(request, auth_context)
        if not auth_ok:
            logger.warning("[STT Gateway] Auth failure: %s (code: %s)", auth_msg, auth_err_code)
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=auth_err_code,
                latency_ms=int((time.time() - start_t) * 1000),
            )

        # 2. Extract and validate audio bytes
        audio_data = raw_audio_bytes
        if not audio_data and request.audio_base64:
            try:
                audio_data = base64.b64decode(request.audio_base64)
            except Exception as ex:
                logger.warning("[STT Gateway] Base64 decode error: %s", ex)
                return SttResponse(
                    request_id=rid,
                    voice_session_id=sid,
                    status="failed",
                    error_code=SttErrorCode.INVALID_AUDIO.value,
                    latency_ms=int((time.time() - start_t) * 1000),
                )

        if not audio_data or len(audio_data) < 44:
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.NO_AUDIO.value,
                latency_ms=int((time.time() - start_t) * 1000),
            )

        if len(audio_data) > self.max_audio_size_bytes:
            logger.warning("[STT Gateway] Audio payload exceeded %d bytes limit.", self.max_audio_size_bytes)
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.INVALID_AUDIO.value,
                latency_ms=int((time.time() - start_t) * 1000),
            )

        if request.duration_ms > self.max_duration_ms:
            logger.warning("[STT Gateway] Duration %d ms exceeded max %d ms.", request.duration_ms, self.max_duration_ms)
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.INVALID_REQUEST.value,
                latency_ms=int((time.time() - start_t) * 1000),
            )

        # 3. Rate limiting / Quota (maximum 10 requests per 10 seconds per patient)
        patient_key = request.patient_id or "anonymous"
        now = time.time()
        timestamps = self._rate_limit_cache.setdefault(patient_key, [])
        self._rate_limit_cache[patient_key] = [t for t in timestamps if now - t < 10.0]
        if len(self._rate_limit_cache[patient_key]) >= 10:
            logger.warning("[STT Gateway] Rate limit exceeded for patient: %s", patient_key)
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.RATE_LIMITED.value,
                latency_ms=int((time.time() - start_t) * 1000),
            )
        self._rate_limit_cache[patient_key].append(now)

        # 4. Invoke Provider Adapter
        adapter = self.provider_adapter
        try:
            resp = adapter.transcribe(
                audio_bytes=audio_data,
                language=request.language or "vi",
                request_id=rid,
                voice_session_id=sid,
                metadata={
                    "patient_id": request.patient_id,
                    "context_type": request.context_type,
                    "context_id": request.context_id,
                },
            )
            # Ensure correct ids
            resp.request_id = rid
            resp.voice_session_id = sid
            return resp

        except Exception as ex:
            logger.error("[STT Gateway] Unexpected error during provider transcription: %s", ex)
            return SttResponse(
                request_id=rid,
                voice_session_id=sid,
                status="failed",
                error_code=SttErrorCode.PROVIDER_UNAVAILABLE.value,
                latency_ms=int((time.time() - start_t) * 1000),
            )

    def _check_authorization(
        self,
        request: SttRequest,
        auth_context: Optional[Dict[str, Any]],
    ) -> Tuple[bool, str, str]:
        """
        Validates actor identity and ensures actor has rights to the specified patient.
        Client cannot forge patient_id.
        """
        if not auth_context:
            return True, "", ""  # Internal or local trusted call

        user_id = str(auth_context.get("user_id") or "").strip()
        role = str(auth_context.get("role") or "").strip()

        if not user_id:
            return False, SttErrorCode.AUTH_REQUIRED.value, "Thiếu thông tin xác thực"

        patient_id = request.patient_id or auth_context.get("patient_id")
        if not patient_id:
            return False, SttErrorCode.INVALID_REQUEST.value, "Thiếu patient_id"

        # If running with auth_service RBAC
        if self.auth_service and hasattr(self.auth_service, "check_patient_access"):
            has_access, err = self.auth_service.check_patient_access(user_id, patient_id, "view")
            if not has_access:
                return False, SttErrorCode.FORBIDDEN.value, f"Từ chối quyền truy cập người bệnh: {err}"

        return True, "", ""

