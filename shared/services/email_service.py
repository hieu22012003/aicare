"""Medication caregiver SMTP transport. Durable retries are owned by MedicationService."""

from __future__ import annotations

import hashlib
import os
import sys
import smtplib
import ssl
from pathlib import Path
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Optional

from shared import config
from shared.config import (
    EMAIL_ENABLED,
    EMAIL_HOST,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_USERNAME,
)
from shared.services.user_service import UserService


class DeliveryStatus:
    DETECTED = "detected"
    QUEUED = "queued"
    DRY_RUN = "DRY_RUN"
    DELIVERED_UNKNOWN = "delivered_unknown"
    SMTP_ACCEPTED = "SMTP_ACCEPTED"
    UNCONFIGURED = "UNCONFIGURED"
    INVALID_EMAIL = "INVALID_EMAIL"
    FAILED_AUTH = "FAILED_AUTH"
    FAILED_NETWORK = "FAILED_NETWORK"
    FAILED_ERROR = "FAILED_ERROR"


@dataclass
class DeliveryResult:
    """Detailed result of email alert dispatch."""
    success: bool
    status: str  # DeliveryStatus constant
    message: str
    recipient: str = ""
    event_id: str = ""
    smtp_response: str = ""
    retries: int = 0

    def __bool__(self) -> bool:
        return self.success


class EmailService:
    """Medication SMTP transport; one attempt per persisted claim."""

    def __init__(
        self,
        enabled: Optional[bool] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        mode: str = "camera",
    ) -> None:
        self.enabled = EMAIL_ENABLED if enabled is None else enabled
        self.host = (host or EMAIL_HOST).strip()
        self.port = port or EMAIL_PORT
        self.username = (username if username is not None else EMAIL_USERNAME).strip()
        self.password = (password if password is not None else EMAIL_PASSWORD).strip()
        self.max_retries = max_retries if max_retries is not None else getattr(config, "EMAIL_RETRY_MAX", 3)
        self.retry_delay = retry_delay if retry_delay is not None else getattr(config, "EMAIL_RETRY_DELAY", 3.0)
        self.mode = mode

    def is_configured(self) -> bool:
        """Check whether email alerting is enabled with non-empty credentials."""
        return bool(self.enabled and self.username and self.password and self.host and self.port)

    def network_blocked(self):
        # Torch imports unittest internally; module presence does not mean tests are running.
        entry = Path(sys.argv[0]) if sys.argv else Path('')
        test_runner = (entry.name.startswith('test_')
                       or any(part in ('unittest','pytest','py.test') for part in entry.parts))
        return (self.mode not in ('camera', 'medication') or '--test' in sys.argv
                or os.environ.get('AI_CARE_TEST_MODE') == '1'
                or 'PYTEST_CURRENT_TEST' in os.environ or test_runner)

    def send_medication_overdue(self, recipient_email, patient_name, due_time, event_id, *, voice_subject=None, voice_body=None):
        """One SMTP attempt. Retry only known pre-submission failures in the repository."""
        if self.network_blocked():
            return DeliveryResult(False, 'DRY_RUN', 'Test mode: SMTP blocked', event_id=event_id)
        if not self.is_configured():
            return DeliveryResult(False, 'UNCONFIGURED', 'Chưa cấu hình Gmail', event_id=event_id)
        if not UserService.validate_email(recipient_email):
            return DeliveryResult(False, 'INVALID_EMAIL', 'Chưa có email người chăm sóc hợp lệ', event_id=event_id)
        msg = MIMEText(f'Chưa có xác nhận cho liều dự kiến của {patient_name} lúc {due_time}.\n'
                       'Vui lòng liên hệ người dùng để kiểm tra. Thông báo này không xác định thuốc đã được uống hay chưa.\n'
                       f'Mã lần nhắc: {event_id}', 'plain', 'utf-8')
        if voice_body is not None:
            msg = MIMEText(voice_body, 'plain', 'utf-8')
        msg['Subject'] = voice_subject or '[MEDICATION_OVERDUE] Chưa có xác nhận cho liều dự kiến'
        msg['From'] = self.username
        msg['To'] = recipient_email
        msg['Message-ID'] = f'<medication-{hashlib.sha256(event_id.encode()).hexdigest()}@ai-care.local>'
        server = None
        submitted = False
        try:
            server = smtplib.SMTP(self.host, self.port, timeout=8)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(self.username, self.password)
            submitted = True
            refused = server.send_message(msg)
            if refused:
                return DeliveryResult(False, 'FAILED_RECIPIENT', 'SMTP từ chối người nhận', event_id=event_id)
            return DeliveryResult(True, 'SMTP_ACCEPTED', 'SMTP đã chấp nhận; chưa biết thư đã đến hộp thư hay được đọc', event_id=event_id)
        except smtplib.SMTPAuthenticationError:
            return DeliveryResult(False, 'FAILED_AUTH', 'Xác thực Gmail thất bại', event_id=event_id)
        except Exception:
            return DeliveryResult(False, 'DELIVERY_UNKNOWN' if submitted else 'FAILED_NETWORK',
                                  'Không rõ kết quả gửi; không tự gửi lại' if submitted else 'Không kết nối được SMTP', event_id=event_id)
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass

    def send_voice_request(self, recipient, intent, transcript, created_at, event_id, priority='normal', context_refs=None):
        """Reuse guarded SMTP transport; caller owns durable delivery claims."""
        return self.send_medication_overdue(recipient, '', '', event_id,
            voice_subject=f'[VOICE {priority}] Yêu cầu {intent}',
            voice_body=f'Nguyên văn: {transcript}\nThời điểm gốc: {created_at}\nMã yêu cầu: {event_id}\n'
                       f'Ngữ cảnh: {context_refs or {}}\n'
                       'Đây là lời yêu cầu của người dùng, chưa có xác nhận tiếp nhận.')

    def test_connection(self) -> DeliveryResult:
        """Verify SMTP connectivity and credentials without sending an email."""
        if self.network_blocked():
            return DeliveryResult(False, "DRY_RUN", "Test mode: SMTP blocked")
        if not self.enabled:
            return DeliveryResult(
                success=False,
                status="UNCONFIGURED",
                message="Dịch vụ gửi email đang tắt (EMAIL_ENABLED=false)",
            )
        if not self.username or not self.password:
            return DeliveryResult(
                success=False,
                status="UNCONFIGURED",
                message="Chưa cấu hình tài khoản gửi Gmail hệ thống (EMAIL_USERNAME / EMAIL_PASSWORD rỗng)",
            )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=8) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self.username, self.password)
            return DeliveryResult(
                success=True,
                status="SMTP_ACCEPTED",
                message="Kết nối máy chủ SMTP và xác thực thành công",
            )
        except smtplib.SMTPAuthenticationError as e:
            return DeliveryResult(
                success=False,
                status="FAILED_AUTH",
                message="Xác thực Gmail thất bại. Vui lòng kiểm tra Mật khẩu ứng dụng (App Password)",
                smtp_response=str(e),
            )
        except (smtplib.SMTPConnectError, TimeoutError, OSError) as e:
            return DeliveryResult(
                success=False,
                status="FAILED_NETWORK",
                message=f"Không thể kết nối đến máy chủ SMTP {self.host}:{self.port}",
                smtp_response=str(e),
            )
        except Exception as e:
            return DeliveryResult(
                success=False,
                status="FAILED_ERROR",
                message=f"Lỗi kiểm tra kết nối SMTP: {e}",
                smtp_response=str(e),
            )
