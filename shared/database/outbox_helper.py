"""
Helper utilities for Outbox Pattern.
Ensures every business write is paired with an outbox event in the SAME transaction.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("OutboxHelper")


def insert_outbox_event(
    conn: Any,
    patient_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int = 1,
    occurred_at: Optional[str] = None,
    actor_id: Optional[str] = None,
    source: str = "elder_app",
    payload_minimal: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> str:
    """
    Inserts a minimal outbox event into outbox_events table within the caller's transaction.
    Returns the event_id.
    """
    if event_id is None:
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc).isoformat()
    if payload_minimal is None:
        payload_minimal = {}

    # Ensure minimal payload includes mandatory fields
    payload = {
        "event_id": event_id,
        "type": event_type,
        "patient_id": patient_id,
        "aggregate_id": aggregate_id,
        "version": aggregate_version,
        "occurred_at": occurred_at,
    }
    payload.update(payload_minimal)

    conn.execute(
        """
        INSERT INTO outbox_events (
            event_id, patient_id, event_type, aggregate_type, aggregate_id,
            aggregate_version, occurred_at, actor_id, source, payload_minimal,
            processed_at, attempts, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL)
        """,
        (
            event_id,
            patient_id,
            event_type,
            aggregate_type,
            aggregate_id,
            aggregate_version,
            occurred_at,
            actor_id,
            source,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    return event_id

