"""
Unit tests for app/agents/email_agent.py

Tests:
- process_email() with mocked OpenAI client
- _extract_json() with various response formats
- _fallback() returns correct structure
- Error handling when OpenAI call fails
"""

import json

import pytest

from app.agents.email_agent import (
    process_email, _extract_json, _fallback, _validate_and_normalise,
    VALID_CATEGORIES, VALID_PRIORITIES, VALID_INTENTS,
)


class TestExtractJson:
    """Tests for _extract_json helper."""

    def test_extract_simple_json(self):
        raw = '{"intent": "schedule", "time": "2026-06-10T09:00:00"}'
        result = _extract_json(raw)
        assert result["intent"] == "schedule"
        assert result["time"] == "2026-06-10T09:00:00"

    def test_extract_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"intent": "send_email"}\nEnd.'
        result = _extract_json(raw)
        assert result["intent"] == "send_email"

    def test_extract_json_with_markdown_code_block(self):
        raw = '```json\n{"intent": "reschedule", "old_time": "2026-06-10T09:00:00", "time": "2026-06-11T10:00:00"}\n```'
        result = _extract_json(raw)
        assert result["intent"] == "reschedule"
        assert result["old_time"] == "2026-06-10T09:00:00"

    def test_extract_json_no_json_raises(self):
        with pytest.raises(ValueError, match="Không tìm thấy JSON"):
            _extract_json("This is plain text without JSON.")

    def test_extract_json_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("{invalid json here}")


class TestValidateAndNormalise:
    """Tests for _validate_and_normalise."""

    def test_full_valid_input_passes_through(self):
        result = _validate_and_normalise({
            "intent": "schedule",
            "category": "Meeting",
            "priority": "High",
            "summary": "Alice wants to meet on Friday",
            "action_required": True,
            "important_note": "Meeting: 14:00 Friday",
            "time": "2026-06-12T14:00:00",
            "old_time": None,
            "location": "Room A",
            "attendees": ["alice@example.com"],
            "confidence": 0.95,
            "raw_time_text": "14:00 Friday",
        })
        assert result["intent"] == "schedule"
        assert result["category"] == "Meeting"
        assert result["priority"] == "High"
        assert result["summary"] == "Alice wants to meet on Friday"
        assert result["action_required"] is True
        assert result["important_note"] == "Meeting: 14:00 Friday"
        assert result["time"] == "2026-06-12T14:00:00"
        assert result["confidence"] == 0.95

    def test_invalid_intent_fallback_to_other(self):
        result = _validate_and_normalise({"intent": "dance"})
        assert result["intent"] == "other"

    def test_invalid_category_fallback_to_other(self):
        result = _validate_and_normalise({"category": "Aliens"})
        assert result["category"] == "Other"

    def test_invalid_priority_fallback_to_low(self):
        result = _validate_and_normalise({"priority": "Extreme"})
        assert result["priority"] == "Low"

    def test_defaults_filled_when_missing(self):
        result = _validate_and_normalise({})
        assert result["intent"] == "other"
        assert result["category"] == "Other"
        assert result["priority"] == "Low"
        assert result["summary"] == "Không thể tóm tắt"
        assert result["action_required"] is False
        assert result["important_note"] is None
        assert result["confidence"] == 0.5

    def test_empty_summary_gets_default(self):
        result = _validate_and_normalise({"summary": ""})
        assert result["summary"] == "Không thể tóm tắt"

    def test_whitespace_summary_gets_default(self):
        result = _validate_and_normalise({"summary": "   "})
        assert result["summary"] == "Không thể tóm tắt"

    def test_empty_important_note_becomes_none(self):
        result = _validate_and_normalise({"important_note": ""})
        assert result["important_note"] is None

    def test_whitespace_important_note_becomes_none(self):
        result = _validate_and_normalise({"important_note": "   "})
        assert result["important_note"] is None

    def test_action_required_coerces_non_bool(self):
        result = _validate_and_normalise({"action_required": 1})
        assert result["action_required"] is True
        result2 = _validate_and_normalise({"action_required": 0})
        assert result2["action_required"] is False

    def test_confidence_clamped(self):
        result = _validate_and_normalise({"confidence": 999})
        assert result["confidence"] == 1.0
        result2 = _validate_and_normalise({"confidence": -0.5})
        assert result2["confidence"] == 0.0


class TestFallback:
    """Tests for _fallback helper."""

    def test_fallback_structure(self):
        result = _fallback("API error")
        assert result["intent"] == "other"
        assert result["category"] == "Other"
        assert result["priority"] == "Low"
        assert result["summary"] == "Không thể phân tích: API error"
        assert result["action_required"] is False
        assert result["important_note"] is None
        assert result["confidence"] == 0.0
        assert result["error"] == "API error"
        assert result["time"] is None
        assert result["location"] is None
        assert result["attendees"] == []
        assert result["raw_time_text"] is None


class TestProcessEmail:
    """Tests for process_email() with mocked OpenAI."""

    def test_process_email_returns_correct_structure(self, mock_openai_client, sample_email):
        """process_email should return a dict with all expected keys including new analysis fields."""
        result = process_email(sample_email)
        assert isinstance(result, dict)
        # Legacy routing keys
        assert "intent" in result
        assert "time" in result
        assert "location" in result
        assert "attendees" in result
        assert "confidence" in result
        assert "raw_time_text" in result
        # New analysis keys
        assert "category" in result
        assert "priority" in result
        assert "summary" in result
        assert "action_required" in result
        assert "important_note" in result

    def test_process_email_schedule_intent(self, mock_openai_client, sample_email):
        """Should return schedule intent from mocked response."""
        result = process_email(sample_email)
        assert result["intent"] == "schedule"
        assert result["time"] == "2026-06-10T09:00:00"
        assert result["location"] == "Room A"
        assert result["category"] == "Meeting"
        assert result["priority"] == "High"
        assert result["action_required"] is True

    def test_process_email_calls_openai(self, mock_openai_client, sample_email):
        """Should call OpenAI's chat.completions.create."""
        process_email(sample_email)
        mock_openai_client.chat.completions.create.assert_called_once()

    def test_process_email_with_minimal_email(self, mock_openai_client):
        """Should handle emails with minimal fields."""
        from app.schemas.email import EmailSchema
        email = EmailSchema(
            sender="test@example.com",
            subject="",
            body="",
            timestamp="2026-06-06T10:00:00",
        )
        result = process_email(email)
        assert result["intent"] == "schedule"  # from mock
        assert result["category"] == "Meeting"  # from mock

    def test_process_email_openai_error_returns_fallback(self, mock_openai_client, sample_email):
        """When OpenAI call fails, should return fallback response."""
        mock_openai_client.chat.completions.create.side_effect = Exception(
            "API timeout")
        result = process_email(sample_email)
        assert result["intent"] == "other"
        assert result["category"] == "Other"
        assert result["confidence"] == 0.0
        assert "error" in result

    def test_process_email_defaults_for_missing_keys(self, mock_openai_client, sample_email):
        """Should set defaults for keys not present in OpenAI response."""
        # Override mock to return minimal JSON
        mock_openai_client.chat.completions.create.return_value = type(
            "MockResponse", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"intent": "schedule"}'
                    })()
                })()]
            }
        )()
        result = process_email(sample_email)
        assert result["intent"] == "schedule"
        assert result["category"] == "Other"
        assert result["summary"] == "Không thể tóm tắt"
        assert result["time"] is None
        assert result["confidence"] == 0.5
