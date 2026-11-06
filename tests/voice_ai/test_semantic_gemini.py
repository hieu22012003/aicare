"""
Comprehensive Unit Tests for Gemini Flash Semantic Provider and Prompt Engine.
Tests Section 3, Section 7, and Phase 3 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
import requests

from features.voice_assistant.voice_ai.domain.contracts import (
    IntentPriority,
    IntentType,
    SemanticResult,
)
from features.voice_assistant.voice_ai.semantic.factory import get_semantic_provider
from features.voice_assistant.voice_ai.semantic.gemini_provider import (
    GeminiSemanticProvider,
    mask_secret,
)
from features.voice_assistant.voice_ai.semantic.mock_provider import MockSemanticProvider
from features.voice_assistant.voice_ai.semantic.prompts import (
    PROMPT_VERSION,
    build_gemini_prompt_payload,
    sanitize_elderly_context,
    sanitize_transcript,
)


class TestPromptsAndSanitization:
    def test_prompt_version_and_content(self):
        assert PROMPT_VERSION == "v1.0"
        sys_inst, user_content = build_gemini_prompt_payload("Tôi đau ngực")
        assert "Elderly Care Voice AI" in sys_inst
        assert "KHÔNG BAO GIỜ" in sys_inst
        assert "Tôi đau ngực" in user_content

    def test_sanitize_elderly_context_pii_scrubbing(self):
        raw_ctx = {
            "elder_name": "Nguyen Van A",
            "phone": "0912345678",
            "elder_phone": "0987654321",
            "address": "123 Ba Dinh, Hanoi",
            "token": "secret_jwt_token_12345",
            "pending_medications": ["Amlodipine 5mg", "Metformin 500mg"],
            "current_screen": "MEDICATION",
            "time_of_day": "morning",
        }
        sanitized = sanitize_elderly_context(raw_ctx)

        # Forbidden PII must be scrubbed completely
        assert "phone" not in sanitized
        assert "elder_phone" not in sanitized
        assert "address" not in sanitized
        assert "token" not in sanitized
        assert "elder_name" not in sanitized

        # Allowed non-PII operational context must be preserved
        assert sanitized["pending_medications"] == ["Amlodipine 5mg", "Metformin 500mg"]
        assert sanitized["current_screen"] == "MEDICATION"
        assert sanitized["time_of_day"] == "morning"

    def test_sanitize_transcript_bounding_and_cleaning(self):
        raw = "   Tôi \x00\x08uống thuốc rồi \r\n  "
        clean = sanitize_transcript(raw, max_chars=50)
        assert clean == "Tôi uống thuốc rồi"

        long_text = "a" * 1500
        bounded = sanitize_transcript(long_text, max_chars=100)
        assert len(bounded) == 100


class TestSecretMasking:
    def test_mask_secret_with_explicit_key(self):
        key = "SyntheticSecretForRedactionOnly"
        raw_msg = f"Error connecting with key {key} to endpoint"
        masked = mask_secret(raw_msg, key)
        assert key not in masked
        assert "***REDACTED***" in masked

    def test_mask_secret_with_google_api_key_regex(self):
        key = "AI" + "zaSyA" + "1234567890123456789012345678"
        raw_msg = f"Failed with {key}"
        masked = mask_secret(raw_msg)
        assert key not in masked
        assert "***REDACTED_API_KEY***" in masked

    def test_mask_secret_with_query_params(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models?key=secret_value_123"
        masked = mask_secret(url)
        assert "secret_value_123" not in masked
        assert "key=***REDACTED***" in masked


class TestGeminiSemanticProvider:
    def test_availability_and_info(self):
        provider_no_key = GeminiSemanticProvider(api_key="")
        assert not provider_no_key.is_available()

        provider_with_key = GeminiSemanticProvider(api_key="AIzaSyDummyKeyForTestingOnly12345")
        assert provider_with_key.is_available()
        info = provider_with_key.get_provider_info()
        assert info["provider"] == "gemini"
        assert info["prompt_version"] == "v1.0"
        assert info["has_api_key"] is True
        assert "AIzaSyDummyKeyForTestingOnly12345" not in str(info)

    def test_missing_api_key_returns_graceful_fallback(self):
        provider = GeminiSemanticProvider(api_key="")
        res = provider.analyze("Gọi bác sĩ cho tôi")
        assert isinstance(res, SemanticResult)
        assert len(res.intents) == 1
        assert res.intents[0].type == IntentType.UNKNOWN
        assert res.intents[0].requires_clarification is True
        assert "chưa được cấu hình" in res.intents[0].entities.get("error_detail", "")

    def test_empty_transcript_returns_empty_fallback(self):
        provider = GeminiSemanticProvider(api_key="dummy_key")
        res = provider.analyze("   ")
        assert isinstance(res, SemanticResult)
        assert len(res.intents) == 1
        assert res.intents[0].type == IntentType.UNKNOWN
        assert res.intents[0].requires_clarification is True

    @patch("requests.post")
    def test_successful_single_intent(self, mock_post):
        mock_response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "schema_version": "1.0",
                                    "language": "vi",
                                    "intents": [
                                        {
                                            "type": "doctor_assistance",
                                            "entities": {"reason": "đau thắt ngực", "urgency": "high"},
                                            "evidence_span": "Bác sĩ ơi tôi thấy đau thắt ngực",
                                            "requires_clarification": False,
                                        }
                                    ],
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_payload
        mock_post.return_value = mock_resp

        provider = GeminiSemanticProvider(api_key="test_api_key_12345")
        result = provider.analyze("Bác sĩ ơi tôi thấy đau thắt ngực")

        assert isinstance(result, SemanticResult)
        assert len(result.intents) == 1
        intent = result.intents[0]
        assert intent.type == IntentType.DOCTOR_ASSISTANCE
        assert intent.priority == IntentPriority.P1
        assert intent.entities["reason"] == "đau thắt ngực"
        assert intent.requires_clarification is False

        # Verify header auth was used instead of query param
        mock_post.assert_called_once()
        headers = mock_post.call_args[1]["headers"]
        assert headers["x-goog-api-key"] == "test_api_key_12345"
        assert "key=" not in mock_post.call_args[0][0]

    @patch("requests.post")
    def test_successful_multi_intent(self, mock_post):
        transcript = "Uống thuốc xong rồi, tôi hơi chóng mặt, gọi bác sĩ giúp tôi"
        mock_response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "schema_version": "1.0",
                                    "language": "vi",
                                    "intents": [
                                        {
                                            "type": "medication_confirm",
                                            "entities": {"status": "taken"},
                                            "evidence_span": "Uống thuốc xong rồi",
                                            "requires_clarification": False,
                                        },
                                        {
                                            "type": "health_report",
                                            "entities": {"symptoms": "chóng mặt", "severity": "mild"},
                                            "evidence_span": "tôi hơi chóng mặt",
                                            "requires_clarification": False,
                                        },
                                        {
                                            "type": "doctor_assistance",
                                            "entities": {"reason": "chóng mặt", "urgency": "medium"},
                                            "evidence_span": "gọi bác sĩ giúp tôi",
                                            "requires_clarification": False,
                                        },
                                    ],
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_payload
        mock_post.return_value = mock_resp

        provider = GeminiSemanticProvider(api_key="test_api_key_12345")
        result = provider.analyze(transcript)

        assert len(result.intents) == 3
        types = [i.type for i in result.intents]
        assert IntentType.MEDICATION_CONFIRM in types
        assert IntentType.HEALTH_REPORT in types
        assert IntentType.DOCTOR_ASSISTANCE in types

    @patch("requests.post")
    def test_markdown_code_fences_stripped(self, mock_post):
        inner_json = json.dumps({
            "schema_version": "1.0",
            "language": "vi",
            "intents": [
                {
                    "type": "music_play",
                    "entities": {"playlist": "nhạc thiền"},
                    "evidence_span": "Bật nhạc thiền",
                    "requires_clarification": False,
                }
            ],
        })
        fenced_text = f"```json\n{inner_json}\n```"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": fenced_text}]}}]
        }
        mock_post.return_value = mock_resp

        provider = GeminiSemanticProvider(api_key="test_api_key_12345")
        result = provider.analyze("Bật nhạc thiền")

        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.MUSIC_PLAY

    @patch("time.sleep", return_value=None)
    @patch("requests.post")
    def test_transient_error_retry(self, mock_post, mock_sleep):
        # 1st call: 429 Rate Limit; 2nd call: 200 OK
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = "Rate limit exceeded"

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "schema_version": "1.0",
                                    "language": "vi",
                                    "intents": [
                                        {
                                            "type": "game_launch",
                                            "entities": {"game_type": "card_matching"},
                                            "evidence_span": "Chơi game",
                                            "requires_clarification": False,
                                        }
                                    ],
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.side_effect = [resp_429, resp_200]

        provider = GeminiSemanticProvider(api_key="test_key", max_retries=2)
        result = provider.analyze("Chơi game")

        assert mock_post.call_count == 2
        assert mock_sleep.called
        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.GAME_LAUNCH

    @patch("time.sleep", return_value=None)
    @patch("requests.post")
    def test_exhausted_retries_returns_safe_fallback(self, mock_post, mock_sleep):
        resp_503 = MagicMock()
        resp_503.status_code = 503
        resp_503.text = "Service temporarily unavailable"
        mock_post.return_value = resp_503

        provider = GeminiSemanticProvider(api_key="test_key", max_retries=1)
        result = provider.analyze("Gọi bác sĩ")

        assert mock_post.call_count == 2  # initial + 1 retry
        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.UNKNOWN
        assert result.intents[0].requires_clarification is True

    @patch("requests.post", side_effect=requests.exceptions.Timeout("Read timeout after 8s"))
    @patch("time.sleep", return_value=None)
    def test_timeout_returns_safe_fallback(self, mock_sleep, mock_post):
        provider = GeminiSemanticProvider(api_key="test_key", timeout_sec=2.0, max_retries=1)
        result = provider.analyze("Nhắc tôi uống thuốc")

        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.UNKNOWN
        assert result.intents[0].requires_clarification is True

    @patch("requests.post")
    def test_malformed_json_returns_safe_fallback(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Broken {json"}]}}]
        }
        mock_post.return_value = mock_resp

        provider = GeminiSemanticProvider(api_key="test_key")
        result = provider.analyze("Tôi muốn nghe nhạc")

        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.UNKNOWN
        assert result.intents[0].requires_clarification is True


class TestSemanticFactory:
    def test_factory_returns_mock_when_no_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        provider = get_semantic_provider(force_mock=False)
        assert isinstance(provider, MockSemanticProvider)

    def test_factory_returns_mock_when_force_mock(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test_key")
        provider = get_semantic_provider(force_mock=True)
        assert isinstance(provider, MockSemanticProvider)

    def test_factory_returns_gemini_when_key_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test_key_123")
        provider = get_semantic_provider(force_mock=False)
        assert isinstance(provider, GeminiSemanticProvider)
        assert provider.is_available() is True

