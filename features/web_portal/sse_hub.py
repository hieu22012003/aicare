"""
Server-Sent Events (SSE) Hub for Near Real-Time Web Push.
Maintains active client connections and dispatches authorized minimal event signals.
Strictly thread-safe using standard library queue.Queue.
"""

from __future__ import annotations

import json
import logging
import queue
import time
import uuid
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("SSEHub")


class SSEClient:
    def __init__(self, client_id: str, user_id: str, role: str) -> None:
        self.client_id = client_id
        self.user_id = user_id
        self.role = role
        self.queue: queue.Queue = queue.Queue(maxsize=1000)
        self.connected_at = time.time()
        self.active = True

    def send_event(self, event_data: Dict[str, Any]) -> bool:
        if not self.active:
            return False
        try:
            self.queue.put_nowait(event_data)
            return True
        except queue.Full:
            logger.warning("SSE queue full for client %s (%s)", self.client_id, self.user_id)
            return False

    def close(self) -> None:
        self.active = False
        try:
            self.queue.put_nowait({"type": "close"})
        except Exception:
            pass


class SSEHub:
    """Central registry and dispatcher for Server-Sent Events."""

    _instance: Optional[SSEHub] = None

    def __init__(self) -> None:
        self._clients: Dict[str, SSEClient] = {}
        self._user_clients: Dict[str, Set[str]] = {}

    @classmethod
    def get_instance(cls) -> SSEHub:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_client(self, user_id: str, role: str) -> SSEClient:
        client_id = f"sse_{uuid.uuid4().hex[:12]}"
        client = SSEClient(client_id, user_id, role)
        self._clients[client_id] = client
        if user_id not in self._user_clients:
            self._user_clients[user_id] = set()
        self._user_clients[user_id].add(client_id)
        logger.info("SSE client registered: %s (user: %s, role: %s). Total clients: %d", client_id, user_id, role, len(self._clients))
        return client

    def unregister_client(self, client_id: str) -> None:
        client = self._clients.pop(client_id, None)
        if client:
            client.active = False
            user_set = self._user_clients.get(client.user_id)
            if user_set:
                user_set.discard(client_id)
                if not user_set:
                    self._user_clients.pop(client.user_id, None)
            logger.info("SSE client unregistered: %s. Remaining clients: %d", client_id, len(self._clients))

    def dispatch_outbox_event(self, event_dict: Dict[str, Any], auth_service: Any) -> int:
        """
        Dispatches minimal outbox event to all connected clients that currently
        have active authorized access to event_dict['patient_id'].
        Returns number of clients reached.
        """
        patient_id = event_dict.get("patient_id") or ""

        # Minimal sanitized payload for broadcast (NO sensitive patient record or photos)
        signal = {
            "event_id": event_dict.get("event_id"),
            "type": event_dict.get("event_type") or event_dict.get("type"),
            "patient_id": patient_id,
            "aggregate_id": event_dict.get("aggregate_id"),
            "version": event_dict.get("aggregate_version", 1),
            "occurred_at": event_dict.get("occurred_at"),
        }
        raw_payload = event_dict.get("payload_minimal")
        if isinstance(raw_payload, str):
            try:
                signal["payload"] = json.loads(raw_payload)
            except Exception:
                signal["payload"] = {}
        elif isinstance(raw_payload, dict):
            signal["payload"] = raw_payload

        delivered_count = 0
        clients_snapshot = list(self._clients.values())

        for client in clients_snapshot:
            if not client.active:
                continue

            # Check if this user currently has access to patient_id, or is admin
            if client.role == "admin":
                has_access = True
            elif patient_id:
                has_access, _ = auth_service.check_patient_access(client.user_id, patient_id)
            else:
                has_access = False

            if has_access:
                if client.send_event(signal):
                    delivered_count += 1

        logger.debug("Dispatched event %s to %d clients", signal.get("type"), delivered_count)
        return delivered_count

    def revoke_access(self, user_id: str, patient_id: str) -> None:
        """Immediately notifies all sessions of user_id that access to patient_id was revoked."""
        client_ids = list(self._user_clients.get(user_id, set()))
        for cid in client_ids:
            client = self._clients.get(cid)
            if client and client.active:
                client.send_event({
                    "event_id": f"rev_{uuid.uuid4().hex[:12]}",
                    "type": "access.revoked",
                    "patient_id": patient_id,
                    "message": "Quyền truy cập hồ sơ bệnh nhân đã bị thu hồi",
                })


# Global singleton
sse_hub = SSEHub.get_instance()

