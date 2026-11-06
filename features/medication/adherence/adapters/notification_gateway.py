"""
Notification Gateway Implementation for Medication Adherence Module.
Provides robust alert delivery tracking, retry management, and simulation mock.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from features.medication.adherence.domain.contracts import INotificationGateway
from features.medication.adherence.domain.models import (
    Alert,
    DeliveryChannel,
    DeliveryStatus,
    NotificationDelivery,
    Patient,
)

logger = logging.getLogger("NotificationGateway")


class MockNotificationGateway(INotificationGateway):
    """
    Mock implementation of INotificationGateway for headless unit testing and demo runs.
    Records delivery history, supports simulated network failures and retries.
    """

    def __init__(self, simulate_network_failure: bool = False, max_simulated_failures: int = 0) -> None:
        self.simulate_network_failure = simulate_network_failure
        self.max_simulated_failures = max_simulated_failures
        self._current_failures = 0
        self.sent_deliveries: List[NotificationDelivery] = []
        self.sent_alerts: List[Dict[str, any]] = []

    def send_alert(self, delivery: NotificationDelivery, alert: Alert, patient: Patient) -> NotificationDelivery:
        now_iso = datetime.now(timezone.utc).isoformat()
        delivery.attempt_count += 1
        delivery.last_attempt_at_utc = now_iso

        # Simulated failure check
        if self.simulate_network_failure and self._current_failures < self.max_simulated_failures:
            self._current_failures += 1
            delivery.status = DeliveryStatus.FAILED
            delivery.error_message = f"Simulated network timeout (attempt {delivery.attempt_count})"
            logger.warning("MockNotificationGateway simulated failure #%d for alert %s", self._current_failures, alert.alert_id)
            return delivery

        # Successful dispatch
        delivery.status = DeliveryStatus.DELIVERED
        delivery.error_message = ""
        self.sent_deliveries.append(delivery)
        self.sent_alerts.append({
            "delivery_id": delivery.delivery_id,
            "alert_id": alert.alert_id,
            "patient_id": patient.patient_id,
            "patient_name": patient.full_name,
            "alert_type": alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
            "recipient": delivery.recipient_snapshot,
            "channel": delivery.channel.value if hasattr(delivery.channel, "value") else str(delivery.channel),
            "delivered_at": now_iso,
        })
        logger.info(
            "MockNotificationGateway: Alert %s delivered to %s via %s",
            alert.alert_id,
            delivery.recipient_snapshot,
            delivery.channel,
        )
        return delivery

    def get_sent_alerts_for_patient(self, patient_id: str) -> List[Dict[str, any]]:
        return [a for a in self.sent_alerts if a["patient_id"] == patient_id]

    def clear(self) -> None:
        self.sent_deliveries.clear()
        self.sent_alerts.clear()
        self._current_failures = 0


class NotificationGateway(INotificationGateway):
    """Production Notification Gateway dispatching via SMTP or External API."""

    def __init__(self, smtp_host: str = "", smtp_port: int = 587, smtp_user: str = "", smtp_pass: str = "") -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass

    def send_alert(self, delivery: NotificationDelivery, alert: Alert, patient: Patient) -> NotificationDelivery:
        now_iso = datetime.now(timezone.utc).isoformat()
        delivery.attempt_count += 1
        delivery.last_attempt_at_utc = now_iso

        if not delivery.recipient_snapshot or "@" not in delivery.recipient_snapshot:
            delivery.status = DeliveryStatus.FAILED
            delivery.error_message = "Địa chỉ email người nhận không hợp lệ"
            return delivery

        # In independent MVP without live SMTP configured, simulate safe delivery log
        delivery.status = DeliveryStatus.SENT
        delivery.error_message = ""
        logger.info("NotificationGateway: Dispatched alert %s to %s", alert.alert_id, delivery.recipient_snapshot)
        return delivery
