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

from app.agents.email_agent import process_email, _extract_json, _fallback


class TestExtractJson:
    """Tests for _extract_json helper."""

    def test_extract_simple_json(self):
        raw = '{"intent": "schedule", "time": "2026-06-10T09:00:00"}'
        result = _extract_json(raw)
        assert result["intent"] == "schedule"
        assert result["time"] == "2026-06-10T09:00:00"

    def test_extract_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"intent": "cancel"}\nEnd.'
        result = _extract_json(raw)
        assert result["intent"] == "cancel"

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


class TestFallback:
    """Tests for _fallback helper."""

    def test_fallback_structure(self):
        result = _fallback("API error")
        assert result["intent"] == "other"
        assert result["confidence"] == 0.0
        assert result["error"] == "API error"
        assert result["summary"] == "Không thể phân tích: API error"
        assert result["time"] is None
        assert result["location"] is None
        assert result["attendees"] == []
        assert result["raw_time_text"] is None


class TestProcessEmail:
    """Tests for process_email() with mocked OpenAI."""

    def test_process_email_returns_correct_structure(self, mock_openai_client, sample_email):
        """process_email should return a dict with all expected keys."""
        result = process_email(sample_email)
        assert isinstance(result, dict)
        assert "intent" in result
        assert "summary" in result
        assert "time" in result
        assert "location" in result
        assert "attendees" in result
        assert "confidence" in result
        assert "raw_time_text" in result

    def test_process_email_schedule_intent(self, mock_openai_client, sample_email):
        """Should return schedule intent from mocked response."""
        result = process_email(sample_email)
        assert result["intent"] == "schedule"
        assert result["time"] == "2026-06-10T09:00:00"
        assert result["location"] == "Room A"

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

    def test_process_email_openai_error_returns_fallback(self, mock_openai_client, sample_email):
        """When OpenAI call fails, should return fallback response."""
        mock_openai_client.chat.completions.create.side_effect = Exception(
            "API timeout")
        result = process_email(sample_email)
        assert result["intent"] == "other"
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
        assert result["summary"] == ""
        assert result["time"] is None
        assert result["confidence"] == 0.5
