"""
Unit tests for app/agents/email_intelligence_agent.py

Tests:
- _extract_json() with various response formats
- _fallback() returns correct structure
- _clean_extracted_data() normalises correctly
- process_email() with mocked OpenAI client
- Error handling when OpenAI call fails
- Category validation
- Importance score clamping
"""

import json

import pytest

from app.agents.email_intelligence_agent import (
    process_email,
    _extract_json,
    _fallback,
    _clean_extracted_data,
)


class TestExtractJson:
    """Tests for _extract_json helper."""

    def test_extract_simple_json(self):
        raw = '{"category": "report", "importance_score": 75}'
        result = _extract_json(raw)
        assert result["category"] == "report"
        assert result["importance_score"] == 75

    def test_extract_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"category": "partnership"}\nEnd.'
        result = _extract_json(raw)
        assert result["category"] == "partnership"

    def test_extract_json_with_markdown_code_block(self):
        raw = (
            '```json\n'
            '{"category": "report", "importance_score": 80, "summary": "- Doanh thu tang\\n- Chi phi giam"}\n'
            '```'
        )
        result = _extract_json(raw)
        assert result["category"] == "report"
        assert result["importance_score"] == 80
        assert "Doanh thu tang" in result["summary"]

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
        assert result["category"] == "other"
        assert result["importance_score"] == 30
        assert result["error"] == "API error"
        assert result["summary"] == "- Không thể phân tích: API error"
        assert result["extracted_data"]["deadline"] is None
        assert result["extracted_data"]["owner"] is None
        assert result["extracted_data"]["project"] is None
        assert result["extracted_data"]["meeting_date"] is None
        assert result["extracted_data"]["meeting_location"] is None
        assert result["extracted_data"]["meeting_attendees"] == []
        assert result["extracted_data"]["key_points"] == []
        assert result["extracted_data"]["action_items"] == []

    def test_fallback_has_all_keys(self):
        expected_keys = {"category", "importance_score",
                         "summary", "extracted_data", "error"}
        result = _fallback("test")
        assert set(result.keys()) == expected_keys


class TestCleanExtractedData:
    """Tests for _clean_extracted_data helper."""

    def test_fills_missing_fields(self):
        minimal = {"project": "AI Platform"}
        cleaned = _clean_extracted_data(minimal)
        assert cleaned["project"] == "AI Platform"
        assert cleaned["deadline"] is None
        assert cleaned["owner"] is None
        assert cleaned["meeting_date"] is None
        assert cleaned["meeting_location"] is None
        assert cleaned["meeting_attendees"] == []
        assert cleaned["key_points"] == []
        assert cleaned["action_items"] == []

    def test_filters_unknown_keys(self):
        extra = {"project": "X", "extra_field": "should be removed"}
        cleaned = _clean_extracted_data(extra)
        assert "extra_field" not in cleaned
        assert cleaned["project"] == "X"

    def test_converts_non_list_fields_to_list(self):
        bad_types = {
            "meeting_attendees": "not a list",
            "key_points": None,
            "action_items": 123,
        }
        cleaned = _clean_extracted_data(bad_types)
        assert cleaned["meeting_attendees"] == []
        assert cleaned["key_points"] == []
        assert cleaned["action_items"] == []

    def test_full_data_passes_through(self):
        full = {
            "deadline": "2026-06-15",
            "owner": "Nguyen Van A",
            "project": "Data Science Platform",
            "meeting_date": "2026-07-01T09:00:00",
            "meeting_location": "Room B",
            "meeting_attendees": ["A", "B"],
            "key_points": ["Revenue up 15%"],
            "action_items": ["Send report by Friday"],
        }
        cleaned = _clean_extracted_data(full)
        assert cleaned == full


class TestProcessEmail:
    """Tests for process_email() with mocked OpenAI."""

    @pytest.fixture
    def mock_client(self):
        """Patch the Email Intelligence Agent's OpenAI client."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion

        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": 78,
                    "summary": "- Doanh thu Q2 tăng 12%\n- Chi phí marketing giảm 5%\n- Mục tiêu Q3: mở rộng 2 thị trường",
                    "extracted_data": {
                        "deadline": "2026-07-15",
                        "owner": "Nguyen Van A",
                        "project": "Market Expansion",
                        "key_points": ["Doanh thu tăng", "Cần mở rộng thị trường"],
                        "action_items": ["Chuẩn bị báo cáo Q2"],
                    },
                })
            )
            yield mc

    def test_process_email_returns_correct_structure(self, mock_client, sample_email):
        """process_email should return a dict with all expected keys."""
        result = process_email(sample_email)
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "category", "importance_score", "summary", "extracted_data",
        }
        assert "deadline" in result["extracted_data"]
        assert "action_items" in result["extracted_data"]

    def test_process_email_classifies_report(self, mock_client, sample_email):
        """Should classify a report email correctly."""
        result = process_email(sample_email)
        assert result["category"] == "report"
        assert result["importance_score"] == 78
        assert "Doanh thu" in result["summary"]
        assert result["extracted_data"]["deadline"] == "2026-07-15"
        assert result["extracted_data"]["owner"] == "Nguyen Van A"

    def test_process_email_calls_openai(self, mock_client, sample_email):
        """Should call OpenAI's chat.completions.create."""
        process_email(sample_email)
        mock_client.chat.completions.create.assert_called_once()

    def test_process_email_with_minimal_email(self, mock_client):
        """Should handle emails with minimal fields."""
        from app.schemas.email import EmailSchema
        email = EmailSchema(
            sender="test@example.com",
            subject="",
            body="",
            timestamp="2026-06-06T10:00:00",
        )
        result = process_email(email)
        assert result["category"] == "report"  # from mock

    def test_process_email_openai_error_returns_fallback(self, mock_client, sample_email):
        """When OpenAI call fails, should return fallback response."""
        mock_client.chat.completions.create.side_effect = Exception(
            "API timeout")
        result = process_email(sample_email)
        assert result["category"] == "other"
        assert result["importance_score"] == 30
        assert "error" in result
        assert "API timeout" in result["summary"]

    def test_process_email_invalid_category_falls_back_to_other(self, sample_email):
        """When LLM returns unknown category, should use 'other'."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "spam",  # invalid category
                    "importance_score": 50,
                    "summary": "- Something",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["category"] == "other"
            # importance_score still passed through
            assert result["importance_score"] == 50

    def test_process_email_score_clamped_to_range(self, sample_email):
        """Importance score should be clamped to 0-100."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion

        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": 150,  # out of range
                    "summary": "- Test",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["importance_score"] == 100

        with patch("app.agents.email_intelligence_agent.client") as mc2:
            mc2.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": -5,  # out of range
                    "summary": "- Test",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["importance_score"] == 0

    def test_process_email_defaults_for_missing_keys(self, sample_email):
        """Should set defaults for keys not present in OpenAI response."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "partnership",
                })
            )
            result = process_email(sample_email)
            assert result["category"] == "partnership"
            assert result["importance_score"] == 50  # default
            assert result["summary"] == "- Không có tóm tắt"
            assert result["extracted_data"]["deadline"] is None

    def test_process_email_null_category_defaults_to_other(self, sample_email):
        """When category is null/None, should default to other."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": None,
                    "importance_score": 40,
                    "summary": "- Test",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["category"] == "other"

    def test_process_email_partnership_category(self, sample_email):
        """Should accept all valid categories."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        valid_categories = [
            "meeting", "report", "partnership", "support", "announcement", "other",
        ]
        for cat in valid_categories:
            with patch("app.agents.email_intelligence_agent.client") as mc:
                mc.chat.completions.create.return_value = MockChatCompletion(
                    json.dumps({
                        "category": cat,
                        "importance_score": 50,
                        "summary": "- Test",
                        "extracted_data": {},
                    })
                )
                result = process_email(sample_email)
                assert result["category"] == cat

    def test_process_email_importance_score_string_converted(self, sample_email):
        """String importance_score should be converted to int."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": "65",
                    "summary": "- Test",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["importance_score"] == 65
            assert isinstance(result["importance_score"], int)

    def test_process_email_non_numeric_score_defaults_to_50(self, sample_email):
        """Non-numeric importance_score should default to 50."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": "high",
                    "summary": "- Test",
                    "extracted_data": {},
                })
            )
            result = process_email(sample_email)
            assert result["importance_score"] == 50

    def test_process_email_extracted_data_with_deadline(self, sample_email):
        """extracted_data.deadline should be preserved."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "report",
                    "importance_score": 60,
                    "summary": "- Test",
                    "extracted_data": {
                        "deadline": "2026-12-31",
                        "owner": "Tran Thi B",
                        "project": "Project X",
                    },
                })
            )
            result = process_email(sample_email)
            assert result["extracted_data"]["deadline"] == "2026-12-31"
            assert result["extracted_data"]["owner"] == "Tran Thi B"
            assert result["extracted_data"]["project"] == "Project X"

    def test_process_email_extracted_data_with_meeting_info(self, sample_email):
        """Meeting category should include meeting_date, location, attendees."""
        from unittest.mock import patch
        from app.tests.conftest import MockChatCompletion
        with patch("app.agents.email_intelligence_agent.client") as mc:
            mc.chat.completions.create.return_value = MockChatCompletion(
                json.dumps({
                    "category": "meeting",
                    "importance_score": 70,
                    "summary": "- Hop ve chien luoc Q3\n- Co 5 phong ban tham gia",
                    "extracted_data": {
                        "meeting_date": "2026-08-15T14:00:00",
                        "meeting_location": "Phong hop A",
                        "meeting_attendees": ["Giam doc", "Truong phong KD"],
                        "key_points": ["Chien luoc Q3"],
                        "action_items": ["Chuan bi tai lieu"],
                    },
                })
            )
            result = process_email(sample_email)
            assert result["extracted_data"]["meeting_date"] == "2026-08-15T14:00:00"
            assert result["extracted_data"]["meeting_location"] == "Phong hop A"
            assert result["extracted_data"]["meeting_attendees"] == [
                "Giam doc", "Truong phong KD"]
