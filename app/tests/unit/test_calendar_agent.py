"""
Unit tests for app/agents/calendar_agent.py

Tests:
- process_schedule() — create event, conflict detection, error handling
- process_cancel() — cancel event, not found, error handling
- process_reschedule() — reschedule event, conflict, not found
- _check_conflict(), _create_event(), _find_events_by_time()
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from app.agents.calendar_agent import (
    process_schedule,
    process_cancel,
    process_reschedule,
    _check_conflict,
    _create_event,
    _find_events_by_time,
)


class TestCheckConflict:
    """Tests for _check_conflict."""

    def test_no_conflict(self, mock_calendar_service):
        """Should return empty list when no busy slots."""
        start = datetime(2026, 6, 10, 9, 0)
        end = datetime(2026, 6, 10, 10, 0)
        busy = _check_conflict(mock_calendar_service, start, end)
        assert busy == []

    def test_with_conflict(self, mock_calendar_service_with_conflict):
        """Should return busy slots when conflict exists."""
        start = datetime(2026, 6, 10, 9, 0)
        end = datetime(2026, 6, 10, 10, 0)
        busy = _check_conflict(mock_calendar_service_with_conflict, start, end)
        assert len(busy) == 1
        assert busy[0]["start"] == "2026-06-10T09:00:00Z"


class TestCreateEvent:
    """Tests for _create_event."""

    def test_creates_event_successfully(self, mock_calendar_service):
        """Should create an event and return the API response."""
        start = datetime(2026, 6, 10, 9, 0)
        end = datetime(2026, 6, 10, 10, 0)
        result = _create_event(
            mock_calendar_service,
            summary="Test Meeting",
            start_dt=start,
            end_dt=end,
            location="Room A",
            attendees=["attendee@example.com"],
            description="Test description",
        )
        assert result["id"] == "evt_new_001"
        assert result["htmlLink"] == "https://calendar.google.com/event?eid=evt_new_001"


class TestFindEventsByTime:
    """Tests for _find_events_by_time."""

    def test_finds_events(self, mock_calendar_service):
        """Should return events in the time range."""
        start = datetime(2026, 6, 10, 8, 0)
        end = datetime(2026, 6, 10, 11, 0)
        events = _find_events_by_time(mock_calendar_service, start, end)
        assert len(events) == 1
        assert events[0]["id"] == "evt_001"
        assert events[0]["summary"] == "Test Meeting"


class TestProcessSchedule:
    """Tests for process_schedule()."""

    def test_schedule_success(self, mock_calendar_service):
        """Should create event successfully when no conflict."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "summary": "Test Meeting",
                "location": "Room A",
                "attendees": ["attendee@example.com"],
                "raw_time_text": "9am Monday",
            }
            result = process_schedule(email_result)
            assert result["status"] == "created"
            assert "event_id" in result
            assert "event_link" in result

    def test_schedule_no_time(self, mock_calendar_service):
        """Should return error when no time provided."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": None,
                "summary": "Test",
                "location": None,
                "attendees": [],
            }
            result = process_schedule(email_result)
            assert result["status"] == "error"
            assert "thời gian" in result["message"]

    def test_schedule_invalid_time(self, mock_calendar_service):
        """Should return error when time format is invalid."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "not-a-valid-date",
                "summary": "Test",
                "location": None,
                "attendees": [],
            }
            result = process_schedule(email_result)
            assert result["status"] == "error"
            assert "không hợp lệ" in result["message"]

    def test_schedule_conflict(self, mock_calendar_service_with_conflict):
        """Should return conflict status when time slot is busy."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service_with_conflict):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "summary": "Test Meeting",
                "location": "Room A",
                "attendees": ["attendee@example.com"],
            }
            result = process_schedule(email_result)
            assert result["status"] == "conflict"
            assert "bận" in result["message"]

    def test_schedule_http_error(self, mock_calendar_service):
        """Should handle Google API HttpError gracefully."""
        mock_calendar_service.freebusy().query().execute.side_effect = HttpError(
            resp=MagicMock(status=403), content=b"Rate limit exceeded"
        )
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "summary": "Test",
                "location": None,
                "attendees": [],
            }
            result = process_schedule(email_result)
            assert result["status"] == "error"
            assert "Google Calendar" in result["message"]


class TestProcessCancel:
    """Tests for process_cancel()."""

    def test_cancel_success(self, mock_calendar_service):
        """Should cancel an existing event."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "summary": "Cancel meeting",
            }
            result = process_cancel(email_result)
            assert result["status"] == "cancelled"
            assert "event_id" in result

    def test_cancel_no_time(self, mock_calendar_service):
        """Should return error when no time provided."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {"time": None, "summary": ""}
            result = process_cancel(email_result)
            assert result["status"] == "error"
            assert "thời gian" in result["message"]

    def test_cancel_not_found(self, mock_calendar_service):
        """Should return not_found when no event matches."""
        # Override events list to return empty
        mock_calendar_service.events().list().execute.return_value = {
            "items": []}
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "summary": "Cancel meeting",
            }
            result = process_cancel(email_result)
            assert result["status"] == "not_found"


class TestProcessReschedule:
    """Tests for process_reschedule()."""

    def test_reschedule_success(self, mock_calendar_service):
        """Should reschedule an existing event to a new time."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-11T10:00:00",
                "old_time": "2026-06-10T09:00:00",
                "summary": "Reschedule meeting",
            }
            result = process_reschedule(email_result)
            assert result["status"] == "rescheduled"
            assert "new_start" in result
            assert result["new_start"] == "2026-06-11T10:00:00"

    def test_reschedule_no_new_time(self, mock_calendar_service):
        """Should return error when no new time provided."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": None,
                "old_time": "2026-06-10T09:00:00",
            }
            result = process_reschedule(email_result)
            assert result["status"] == "error"
            assert "giờ mới" in result["message"]

    def test_reschedule_no_old_time(self, mock_calendar_service):
        """Should return error when no old time provided."""
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-11T10:00:00",
                "old_time": None,
            }
            result = process_reschedule(email_result)
            assert result["status"] == "error"
            assert "giờ cũ" in result["message"]

    def test_reschedule_not_found(self, mock_calendar_service):
        """Should return not_found when old event doesn't exist."""
        mock_calendar_service.events().list().execute.return_value = {
            "items": []}
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service):
            email_result = {
                "time": "2026-06-11T10:00:00",
                "old_time": "2026-06-10T09:00:00",
            }
            result = process_reschedule(email_result)
            assert result["status"] == "not_found"

    def test_reschedule_conflict(self, mock_calendar_service_with_conflict):
        """Should return conflict when new time slot is busy."""
        # Need to provide an existing old event so the function can find it
        # before checking for conflict on the new time
        mock_calendar_service_with_conflict.events().list().execute.return_value = {
            "items": [{
                "id": "evt_001",
                "summary": "Old Meeting",
                "start": {"dateTime": "2026-06-09T09:00:00+07:00"},
                "end": {"dateTime": "2026-06-09T10:00:00+07:00"},
                "attendees": [],
            }]
        }
        with patch("app.agents.calendar_agent._get_service", return_value=mock_calendar_service_with_conflict):
            email_result = {
                "time": "2026-06-10T09:00:00",
                "old_time": "2026-06-09T09:00:00",
            }
            result = process_reschedule(email_result)
            assert result["status"] == "conflict"
