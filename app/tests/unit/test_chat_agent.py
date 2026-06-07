"""
Unit tests for app/agents/chat_agent.py

Tests:
- chat() — with action extraction, query_calendar, error handling
- _fetch_upcoming_events() — with mocked Calendar API
- _format_events() — formatting logic
"""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.chat_agent import chat, _fetch_upcoming_events, _format_events


class TestFormatEvents:
    """Tests for _format_events()."""

    def test_empty_events(self):
        result = _format_events([])
        assert result == "Không có lịch nào."

    def test_single_event(self):
        events = [{"summary": "Team standup",
                   "start": "2026-06-10T09:00:00+07:00", "location": "Room A", "link": ""}]
        result = _format_events(events)
        assert "Team standup" in result
        assert "Room A" in result

    def test_multiple_events(self):
        events = [
            {"summary": "Meeting 1", "start": "2026-06-10T09:00:00+07:00",
                "location": "", "link": ""},
            {"summary": "Meeting 2", "start": "2026-06-11T14:00:00+07:00",
                "location": "Room B", "link": ""},
        ]
        result = _format_events(events)
        assert "Meeting 1" in result
        assert "Meeting 2" in result
        assert "Room B" in result

    def test_event_without_location(self):
        events = [{"summary": "Standup",
                   "start": "2026-06-10T09:00:00+07:00", "location": "", "link": ""}]
        result = _format_events(events)
        assert "Standup" in result
        assert "@" not in result  # No location marker

    def test_invalid_date_format(self):
        events = [{"summary": "Event", "start": "invalid-date",
                   "location": "", "link": ""}]
        result = _format_events(events)
        assert "Event" in result


class TestFetchUpcomingEvents:
    """Tests for _fetch_upcoming_events()."""

    def test_returns_events(self, mock_calendar_service):
        """Should return formatted events from Calendar API."""
        with patch("app.agents.chat_agent._get_calendar_service", return_value=mock_calendar_service):
            events = _fetch_upcoming_events(range_days=7)
            assert len(events) == 1
            assert events[0]["summary"] == "Test Meeting"
            assert "start" in events[0]
            assert "link" in events[0]

    def test_empty_response(self, mock_calendar_service):
        """Should return empty list when no events."""
        mock_calendar_service.events().list().execute.return_value = {
            "items": []}
        with patch("app.agents.chat_agent._get_calendar_service", return_value=mock_calendar_service):
            events = _fetch_upcoming_events(range_days=7)
            assert events == []

    def test_api_error_returns_empty(self, mock_calendar_service):
        """Should return empty list on API error."""
        mock_calendar_service.events().list().execute.side_effect = Exception("API error")
        with patch("app.agents.chat_agent._get_calendar_service", return_value=mock_calendar_service):
            events = _fetch_upcoming_events(range_days=7)
            assert events == []


class TestChat:
    """Tests for chat()."""

    def test_chat_returns_reply_and_action(self, mock_openai_chat_client):
        """Should return dict with reply and action."""
        result = chat([{"role": "user", "content": "What's my schedule?"}])
        assert "reply" in result
        assert "action" in result

    def test_chat_extracts_action(self, mock_openai_chat_client):
        """Should extract action from <action> tags."""
        result = chat([{"role": "user", "content": "Show my calendar"}])
        # The query_calendar action is consumed (set to None) after processing
        # The reply should contain calendar information
        assert result["action"] is None
        assert len(result["reply"]) > 0

    def test_chat_handles_query_calendar(self, mock_openai_chat_client, mock_calendar_service):
        """Should fetch calendar events when action is query_calendar."""
        with patch("app.agents.chat_agent._get_calendar_service", return_value=mock_calendar_service):
            result = chat([{"role": "user", "content": "Show my calendar"}])
            # After query_calendar, action should be None (consumed)
            assert result["action"] is None
            assert len(result["reply"]) > 0

    def test_chat_no_action(self):
        """Should handle responses without action tags."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = type(
            "MockResponse", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": "Xin chào! Tôi có thể giúp gì cho bạn?"
                    })()
                })()]
            }
        )()
        with patch("app.agents.chat_agent.client", mock_client):
            result = chat([{"role": "user", "content": "Hello"}])
            assert result["action"] is None
            assert "Xin chào" in result["reply"]

    def test_chat_error_returns_fallback(self):
        """Should return fallback message on error."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(
            "OpenAI error")
        with patch("app.agents.chat_agent.client", mock_client):
            result = chat([{"role": "user", "content": "Hello"}])
            assert "sự cố" in result["reply"]
            assert result["action"] is None
