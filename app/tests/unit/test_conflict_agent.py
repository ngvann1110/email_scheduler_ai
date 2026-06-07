"""
Unit tests for app/agents/conflict_agent.py

Tests:
- find_alternatives() — found alternatives, not found, error
- _candidate_slots() — generates correct time slots
- _is_slot_free() — free/busy detection
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.agents.conflict_agent import (
    find_alternatives,
    _candidate_slots,
    _is_slot_free,
    WORKING_HOURS_START,
    WORKING_HOURS_END,
    MAX_SUGGESTIONS,
)


class TestIsSlotFree:
    """Tests for _is_slot_free."""

    def test_slot_is_free(self, mock_calendar_service):
        """Should return True when no busy slots."""
        start = datetime(2026, 6, 10, 9, 0)
        end = datetime(2026, 6, 10, 10, 0)
        assert _is_slot_free(mock_calendar_service, start, end) is True

    def test_slot_is_busy(self, mock_calendar_service_with_conflict):
        """Should return False when there are busy slots."""
        start = datetime(2026, 6, 10, 9, 0)
        end = datetime(2026, 6, 10, 10, 0)
        assert _is_slot_free(
            mock_calendar_service_with_conflict, start, end) is False


class TestCandidateSlots:
    """Tests for _candidate_slots generator."""

    def test_generates_slots_within_working_hours(self):
        """Generated slots should be within working hours."""
        from_dt = datetime(2026, 6, 10, 9, 0)
        slots = list(_candidate_slots(from_dt, 60))
        for start, end in slots:
            assert WORKING_HOURS_START <= start.hour < WORKING_HOURS_END
            assert end.hour <= WORKING_HOURS_END

    def test_slots_have_correct_duration(self):
        """Each slot should have the requested duration."""
        from_dt = datetime(2026, 6, 10, 9, 0)
        slots = list(_candidate_slots(from_dt, 60))
        for start, end in slots:
            assert (end - start) == timedelta(minutes=60)

    def test_slots_are_30_minutes_apart(self):
        """Slots should be generated at 30-minute intervals."""
        from_dt = datetime(2026, 6, 10, 9, 0)
        slots = list(_candidate_slots(from_dt, 30))
        if len(slots) >= 2:
            gap = slots[1][0] - slots[0][0]
            assert gap == timedelta(minutes=30)

    def test_skips_outside_working_hours(self):
        """Should skip slots outside working hours."""
        from_dt = datetime(2026, 6, 10, 17, 30)
        slots = list(_candidate_slots(from_dt, 60))
        for start, end in slots:
            assert start.hour < WORKING_HOURS_END

    def test_rounds_up_to_next_30_min(self):
        """Should round up to the nearest 30-minute mark."""
        from_dt = datetime(2026, 6, 10, 9, 17)
        slots = list(_candidate_slots(from_dt, 30))
        if slots:
            assert slots[0][0].minute in (30, 0)


class TestFindAlternatives:
    """Tests for find_alternatives()."""

    def test_finds_alternatives(self, mock_calendar_service):
        """Should find alternative time slots."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives(
                "2026-06-10T09:00:00", duration_minutes=60)
            assert result["status"] == "found"
            assert len(result["suggestions"]) > 0
            assert "start" in result["suggestions"][0]
            assert "end" in result["suggestions"][0]
            assert "label" in result["suggestions"][0]

    def test_limited_to_max_suggestions(self, mock_calendar_service):
        """Should not return more than MAX_SUGGESTIONS alternatives."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives(
                "2026-06-10T09:00:00", duration_minutes=30)
            assert len(result["suggestions"]) <= MAX_SUGGESTIONS

    def test_invalid_time_format(self, mock_calendar_service):
        """Should return error for invalid time format."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives("not-a-valid-date")
            assert result["status"] == "error"

    def test_not_found_when_all_busy(self, mock_calendar_service_with_conflict):
        """Should return not_found when all slots are busy."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service_with_conflict):
            result = find_alternatives(
                "2026-06-10T09:00:00", duration_minutes=60)
            assert result["status"] == "not_found"
            assert result["suggestions"] == []

    def test_error_handling(self, mock_calendar_service):
        """Should handle exceptions gracefully."""
        mock_calendar_service.freebusy().query().execute.side_effect = Exception("API error")
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives("2026-06-10T09:00:00")
            assert result["status"] == "error"
            assert "message" in result
