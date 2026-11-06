"""
Idempotency Manager for Voice AI Workflows.
Guarantees at-most-once execution of side-effecting operations (medication confirm, doctor calls).
Implements Section 4 and Section 7 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("voice_ai.orchestrator.idempotency")


class IdempotencyManager:
    """
    Thread-safe in-memory cache with TTL for request deduplication.
    Prevents duplicate medication confirmations and multiple doctor alerts.
    """

    def __init__(self, default_ttl_sec: int = 600) -> None:
        self.default_ttl_sec = default_ttl_sec
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def check(self, key: Optional[str]) -> Optional[Any]:
        """
        Returns cached response if key exists and has not expired; None otherwise.
        """
        if not key or not str(key).strip():
            return None

        clean_key = str(key).strip()
        now = time.time()
        with self._lock:
            entry = self._cache.get(clean_key)
            if not entry:
                return None
            expiry, cached_obj = entry
            if now > expiry:
                del self._cache[clean_key]
                return None
            logger.info(f"Idempotent hit for key: {clean_key[:16]}...")
            return cached_obj

    def store(self, key: Optional[str], response_payload: Any, ttl_sec: Optional[int] = None) -> None:
        """
        Stores response for idempotency key.
        """
        if not key or not str(key).strip():
            return

        clean_key = str(key).strip()
        ttl = ttl_sec if ttl_sec is not None else self.default_ttl_sec
        expiry = time.time() + ttl

        with self._lock:
            # Clean up old expired entries if cache grows
            if len(self._cache) > 1000:
                self._prune_expired()
            self._cache[clean_key] = (expiry, response_payload)

    def _prune_expired(self) -> None:
        now = time.time()
        expired = [k for k, (exp, _) in self._cache.items() if now > exp]
        for k in expired:
            del self._cache[k]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

