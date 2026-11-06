"""
Deterministic Pre-LLM Safety Detector & Urgent Assistance Rules.
Executes in < 5ms before any external LLM call to guarantee that emergency
assistance is NEVER delayed by LLM inference, network latency, or provider timeouts.
Implements Section 4 (W4) and Section 7 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from features.voice_assistant.voice_ai.domain.contracts import RedFlagSeverity, SafetySignal

# Patterns for explicit negation of doctor/help requests and medication adherence
NEGATION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(không|đừng|chưa|chẳng)\s+(cần|phải|gọi|kêu|báo|phiền)\s+(bác\s*sĩ|bác\s*sỹ|cấp\s*cứu|người\s*nhà|ai)", re.IGNORECASE),
    re.compile(r"\bkhông\s+cần\b", re.IGNORECASE),
    re.compile(r"\bđừng\s+gọi\b", re.IGNORECASE),
    re.compile(r"\bchưa\s+cần\b", re.IGNORECASE),
    re.compile(r"\bkhông\s+có\s+gọi\b", re.IGNORECASE),
    re.compile(r"\b(không|chẳng|chưa)\s+(nói|bảo|xác\s*nhận)\b", re.IGNORECASE),
    re.compile(r"\bkhông\s+(nhớ|rõ|biết)(\s+(là|đã|có)?\s+(uống|dùng)(\s+chưa)?)?\b", re.IGNORECASE),
    re.compile(r"\bđừng\s+(đánh\s*dấu|ghi|bảo|chọn)(\s+là)?\s+(đã\s+)?(uống|xong)\b", re.IGNORECASE),
]

# Patterns for explicit doctor / emergency assistance requests
ASSISTANCE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(gọi|kêu|tìm|nhờ|báo|liên\s*hệ)\s+(bác\s*sĩ|bác\s*sỹ|y\s*tá|bác\s*si)\b", re.IGNORECASE), "gọi bác sĩ"),
    (re.compile(r"\b(tôi|mình|cụ|ông|bà)\s+(cần|muốn)\s+(gặp|bác\s*sĩ|bác\s*sỹ|cấp\s*cứu)\b", re.IGNORECASE), "cần gặp bác sĩ"),
    (re.compile(r"\b(bác\s*sĩ|bác\s*sỹ)\s*(ơi|giúp|đâu|cứu)\b", re.IGNORECASE), "kêu gọi bác sĩ"),
    (re.compile(r"\b(cứu\s*tôi|cứu\s*với|giúp\s*tôi\s*với|cần\s*cấp\s*cứu|cấp\s*cứu\s*ngay)\b", re.IGNORECASE), "cấp cứu"),
    (re.compile(r"\b(ngất|xỉu|té|ngã)\s+(rồi|xuống|đây)\b", re.IGNORECASE), "sự cố ngã/ngất"),
]

# Critical Red-Flag Clinical Symptoms (Automatic escalation to assistance path)
RED_FLAG_PATTERNS: List[Tuple[re.Pattern, str, RedFlagSeverity]] = [
    (re.compile(r"\b(đau\s*ngực\s*dữ\s*dội|đau\s*ngực\s*quá|đau\s*thắt\s*ngực|tức\s*ngực\s*dữ\s*dội|nghẹt\s*thở|khó\s*thở\s*quá)\b", re.IGNORECASE), "khó thở/tức ngực dữ dội", RedFlagSeverity.CRITICAL),
    (re.compile(r"\b(méo\s*miệng|liệt|không\s*nói\s*được|yếu\s*nửa\s*người)\b", re.IGNORECASE), "dấu hiệu đột quỵ", RedFlagSeverity.CRITICAL),
    (re.compile(r"\b(co\s*giật|bất\s*tỉnh|hôn\s*mê)\b", re.IGNORECASE), "mất tri giác", RedFlagSeverity.CRITICAL),
    (re.compile(r"\b(chóng\s*mặt\s*dữ\s*dội|quay\s*cuồng\s*không\s*đứng\s*vững)\b", re.IGNORECASE), "chóng mặt cấp tính", RedFlagSeverity.MODERATE),
]


class DeterministicSafetyDetector:
    """Fast, deterministic pre-LLM safety analyzer."""

    def __init__(self) -> None:
        self.negation_patterns = NEGATION_PATTERNS
        self.assistance_patterns = ASSISTANCE_PATTERNS
        self.red_flag_patterns = RED_FLAG_PATTERNS

    def analyze(self, transcript: str) -> SafetySignal:
        text = transcript.strip()
        if not text:
            return SafetySignal(is_urgent_assistance=False)

        # 1. Check for explicit negations of calling help
        has_negation = False
        for neg_pat in self.negation_patterns:
            if neg_pat.search(text):
                has_negation = True
                break

        # 2. Check for explicit assistance / doctor calls
        matched_assistance: List[str] = []
        if not has_negation:
            for pat, desc in self.assistance_patterns:
                if pat.search(text):
                    matched_assistance.append(desc)

        # 3. Check for clinical red flags
        matched_red_flags: List[str] = []
        highest_severity = RedFlagSeverity.NONE
        for pat, desc, severity in self.red_flag_patterns:
            if pat.search(text):
                matched_red_flags.append(desc)
                if severity == RedFlagSeverity.CRITICAL:
                    highest_severity = RedFlagSeverity.CRITICAL
                elif severity == RedFlagSeverity.MODERATE and highest_severity != RedFlagSeverity.CRITICAL:
                    highest_severity = RedFlagSeverity.MODERATE

        is_urgent = len(matched_assistance) > 0 or highest_severity == RedFlagSeverity.CRITICAL

        urgency_reason = None
        if is_urgent:
            reasons = matched_assistance + matched_red_flags
            urgency_reason = "; ".join(reasons)

        suggested_action = None
        if is_urgent:
            suggested_action = "DISPATCH_ASSISTANCE_IMMEDIATELY"
        elif highest_severity == RedFlagSeverity.MODERATE:
            suggested_action = "LOG_HEALTH_AND_ALERT_CAREGIVER"

        return SafetySignal(
            is_urgent_assistance=is_urgent,
            is_red_flag_health=len(matched_red_flags) > 0,
            red_flag_severity=highest_severity,
            urgency_reason=urgency_reason,
            matched_keywords=matched_assistance + matched_red_flags,
            has_negation=has_negation,
            suggested_action=suggested_action,
        )

    def detect(self, transcript: str) -> SafetySignal:
        """Alias for analyze() complying with SafetyDetector contract."""
        return self.analyze(transcript)
