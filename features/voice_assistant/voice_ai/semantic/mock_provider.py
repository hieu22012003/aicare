"""
Mock Semantic Provider for Contract Testing and Offline Verification.
Produces deterministic, schema-compliant JSON outputs for the test fixture suite.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from features.voice_assistant.voice_ai.domain.contracts import SemanticResult
from features.voice_assistant.voice_ai.semantic.provider import SemanticProvider
from features.voice_assistant.voice_ai.semantic.schemas import validate_semantic_payload


class MockSemanticProvider(SemanticProvider):
    """Deterministic mock semantic provider based on curated fixtures."""

    def __init__(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "provider": "mock",
            "model": "mock-deterministic-v1",
            "prompt_version": "v1.0",
        }

    def analyze(self, transcript: str, sanitized_context: Optional[Dict[str, Any]] = None) -> SemanticResult:
        text = transcript.strip().lower()

        # 1. Empty or gibberish
        if not text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "UNKNOWN",
                        "entities": {},
                        "evidence_span": "",
                        "requires_clarification": True,
                        "clarification_prompt": "Dạ con chưa nghe rõ, cụ nói lại giúp con nhé.",
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 2. Multi-intent: Medication + Health + Doctor
        if "uống thuốc" in text and "chóng mặt" in text and ("gọi bác sĩ" in text or "bác sĩ" in text):
            med_evidence = "uống thuốc xong rồi" if "uống thuốc xong rồi" in text else "uống thuốc rồi" if "uống thuốc rồi" in text else "uống thuốc"
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "DOCTOR_ASSISTANCE",
                        "entities": {"reason": "chóng mặt"},
                        "evidence_span": "gọi bác sĩ",
                        "requires_clarification": False,
                    },
                    {
                        "type": "HEALTH_REPORT",
                        "entities": {"symptoms": ["chóng mặt"], "onset": "vừa xong"},
                        "evidence_span": "chóng mặt",
                        "requires_clarification": False,
                    },
                    {
                        "type": "MEDICATION_CONFIRM",
                        "entities": {"status": "taken"},
                        "evidence_span": med_evidence,
                        "requires_clarification": False,
                    },
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 3. Explicit negation of doctor call
        if "không cần gọi bác sĩ" in text or "đừng gọi bác sĩ" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "UNKNOWN",
                        "entities": {},
                        "evidence_span": "không cần gọi bác sĩ",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 3.5. Multi-intent: Doctor assistance + Health report (e.g. "Gọi bác sĩ giúp tôi, tôi thấy khó thở quá")
        if ("gọi bác sĩ" in text or "bác sĩ" in text) and "khó thở" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "DOCTOR_ASSISTANCE",
                        "entities": {"reason": "khó thở quá", "urgency": "high", "symptoms": "khó thở"},
                        "evidence_span": "Gọi bác sĩ giúp tôi",
                        "requires_clarification": False,
                    },
                    {
                        "type": "HEALTH_REPORT",
                        "entities": {"symptoms": ["khó thở quá"], "severity": "severe"},
                        "evidence_span": "tôi thấy khó thở quá",
                        "requires_clarification": False,
                    },
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 4. Explicit doctor assistance
        if "gọi bác sĩ" in text or "kêu bác sĩ" in text or "cần bác sĩ" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "DOCTOR_ASSISTANCE",
                        "entities": {"urgency": "high"},
                        "evidence_span": "gọi bác sĩ ngay" if "ngay" in text else "gọi bác sĩ",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 5. Complex negation: "Tôi không nói là tôi đã uống thuốc"
        if "không nói" in text or "không có nói" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "CLARIFY",
                        "entities": {},
                        "evidence_span": text,
                        "requires_clarification": True,
                        "clarification_prompt": "Dạ vậy cụ đã uống thuốc hay chưa ạ?",
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 6. Medication not taken (including multi-intent with health symptoms like nausea)
        if "chưa uống thuốc" in text or "không uống" in text:
            if "buồn nôn" in text:
                payload = {
                    "schema_version": "1.0",
                    "language": "vi",
                    "intents": [
                        {
                            "type": "MEDICATION_NOT_TAKEN",
                            "entities": {"reason": "buồn nôn"},
                            "evidence_span": "Tôi chưa uống thuốc",
                            "requires_clarification": False,
                        },
                        {
                            "type": "HEALTH_REPORT",
                            "entities": {"symptoms": ["buồn nôn"], "severity": "moderate"},
                            "evidence_span": "thấy buồn nôn",
                            "requires_clarification": False,
                        },
                    ],
                }
                _, _, res = validate_semantic_payload(payload, transcript)
                return res

            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MEDICATION_NOT_TAKEN",
                        "entities": {},
                        "evidence_span": "chưa uống thuốc",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 7. Medication confirm
        if "uống thuốc rồi" in text or "đã uống" in text or "vừa uống" in text:
            entities = {"status": "taken"}
            if "huyết áp" in text:
                entities["medication_name"] = "thuốc huyết áp"
                if "8 giờ" in text or "8h" in text:
                    entities["time"] = "8:00"
            elif "amlodipin" in text:
                entities["medication_ref"] = "Amlodipine"
            elif "metformin" in text:
                entities["medication_ref"] = "Metformin"

            ev_span = text if len(text) <= 40 else ("tôi đã uống thuốc rồi" if "tôi đã uống thuốc rồi" in text else "uống thuốc rồi")
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MEDICATION_CONFIRM",
                        "entities": entities,
                        "evidence_span": ev_span,
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 8. Medication guidance
        if "uống thuốc nào" in text or "hướng dẫn thuốc" in text or "uống trước hay sau ăn" in text or "màu vàng" in text:
            entities = {}
            if "màu vàng" in text:
                entities["medication_color"] = "vàng"
                entities["question_type"] = "timing"
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MEDICATION_GUIDANCE",
                        "entities": entities,
                        "evidence_span": text if len(text) <= 50 else "uống thuốc nào",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 9. Medication remind later
        if "nhắc lại" in text or "nhắc tôi" in text:
            delay = 30 if "30 phút" in text else 10
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MEDICATION_REMIND_LATER",
                        "entities": {"delay_minutes": delay},
                        "evidence_span": f"{delay} phút nữa" if f"{delay} phút" in text else "nhắc lại",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 9.5. Critical red flag symptom: Escalated to DOCTOR_ASSISTANCE
        if "đau thắt ngực" in text or "khó thở" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "DOCTOR_ASSISTANCE",
                        "entities": {"reason": "đau thắt ngực / khó thở cấp tính"},
                        "evidence_span": "đau thắt ngực",
                        "requires_clarification": False,
                    },
                    {
                        "type": "HEALTH_REPORT",
                        "entities": {"symptoms": ["đau thắt ngực", "khó thở"], "onset": "cấp tính"},
                        "evidence_span": "khó thở",
                        "requires_clarification": False,
                    },
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 10. Health report
        if "chóng mặt" in text or "đau đầu" in text:
            symptoms = []
            if "chóng mặt" in text:
                symptoms.append("chóng mặt")
            if "đau đầu" in text:
                symptoms.append("đau đầu")
            entities = {"symptoms": symptoms, "onset": "từ sáng" if "từ sáng" in text else ("tối qua" if "tối qua" in text else "hiện tại")}
            if "150/90" in text:
                entities["blood_pressure"] = "150/90"
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "HEALTH_REPORT",
                        "entities": entities,
                        "evidence_span": transcript[:100],
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 10.5. Rehabilitation start
        if "phục hồi chức năng" in text or "tập bài" in text or "tập phục hồi" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "REHABILITATION_START",
                        "entities": {"exercise_type": "sit_to_stand"},
                        "evidence_span": transcript[:60],
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 11. Games
        if "lật thẻ" in text or "lật hình" in text or "trò chơi" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "GAME_LAUNCH",
                        "entities": {"game_type": "card_matching"},
                        "evidence_span": "Tôi muốn chơi trò lật hình" if "lật hình" in text else "lật thẻ",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 12. Music
        if "tắt nhạc" in text or "dừng nhạc" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MUSIC_STOP",
                        "entities": {},
                        "evidence_span": "tắt nhạc đi" if "tắt nhạc đi" in text else "tắt nhạc",
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        if "bật nhạc" in text or "phát nhạc" in text or "nhạc thư giãn" in text or "nghe nhạc" in text or "nhạc nhẹ" in text or "bolero" in text:
            music_evidence = "bật nhạc" if "bật nhạc" in text else ("bolero" if "bolero" in text else "nhạc")
            entities = {}
            if "bolero" in text:
                entities = {"genre": "bolero xưa", "playlist": "bolero xưa"}
            else:
                entities = {"playlist": "nhạc nhẹ" if "nhạc nhẹ" in text else "nhạc thư giãn"}
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "intents": [
                    {
                        "type": "MUSIC_PLAY",
                        "entities": entities,
                        "evidence_span": music_evidence,
                        "requires_clarification": False,
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # 13. Explicit unsupported external requests (YouTube, movie, web search)
        if "youtube" in text or "phim" in text or "phim hài" in text:
            payload = {
                "schema_version": "1.0",
                "language": "vi",
                "requires_clarification": False,
                "clarification_question": None,
                "reason": "Yêu cầu ngoài phạm vi hỗ trợ của hệ thống y tế/chăm sóc (YouTube/phim hài)",
                "risk_flags": [],
                "intents": [
                    {
                        "type": "UNKNOWN",
                        "entities": {"reason": "Ngoài phạm vi ứng dụng"},
                        "evidence_span": transcript[:60],
                        "requires_clarification": False,
                        "clarification_prompt": None,
                        "reason": "Ngoài phạm vi ứng dụng",
                        "risk_flags": [],
                    }
                ],
            }
            _, _, res = validate_semantic_payload(payload, transcript)
            return res

        # Fallback unknown
        payload = {
            "schema_version": "1.0",
            "language": "vi",
            "intents": [
                {
                    "type": "UNKNOWN",
                    "entities": {},
                    "evidence_span": text[:50],
                    "requires_clarification": True,
                    "clarification_prompt": "Dạ con chưa hiểu rõ yêu cầu của cụ, cụ nói lại nhé.",
                }
            ],
        }
        _, _, res = validate_semantic_payload(payload, transcript)
        return res
