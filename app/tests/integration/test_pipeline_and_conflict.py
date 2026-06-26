"""
Combined integration tests for:
  - app/agents/conflict_agent.py  (unit-style pure-logic tests, mocked-service tests,
                                   and full Google API integration tests)
  - app/orchestrator/orchestrator.py  (LangGraph pipeline, all externals mocked)

Fixtures mock_calendar_service and mock_calendar_service_with_conflict are
provided by app/tests/conftest.py and auto-discovered by pytest.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.agents.conflict_agent import (
    ICT,
    MAX_SUGGESTIONS,
    WORKING_HOURS_END,
    WORKING_HOURS_START,
    _candidate_slots,
    _is_slot_free,
    detect_conflict,
    find_alternatives,
)


# ══════════════════════════════════════════════════════════════════════════════
# Part 1 — conflict_agent unit tests (fixture-based, from app/tests/unit)
# ══════════════════════════════════════════════════════════════════════════════


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

    def test_converts_ict_to_utc_in_api_call(self):
        """
        _is_slot_free converts ICT-aware datetimes to UTC before querying the API.

        14:00 ICT (UTC+7) = 07:00 UTC  |  15:00 ICT = 08:00 UTC.
        _is_slot_free now expects timezone-aware datetimes; naive input is no
        longer supported at this layer (ICT attachment is the caller's responsibility).
        """
        service = MagicMock()
        service.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }

        start_ict = datetime(2026, 4, 28, 14, 0, 0, tzinfo=ICT)
        end_ict   = datetime(2026, 4, 28, 15, 0, 0, tzinfo=ICT)

        _is_slot_free(service, start_ict, end_ict)

        body = service.freebusy.return_value.query.call_args.kwargs["body"]

        assert body["timeMin"] == "2026-04-28T07:00:00Z", (
            f"Expected 07:00Z (14:00 ICT → UTC) but got {body['timeMin']}."
        )
        assert body["timeMax"] == "2026-04-28T08:00:00Z", (
            f"Expected 08:00Z but got {body['timeMax']}."
        )


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
        """Should find alternative weekday slots when the requested slot is on a weekend."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives(
                "2026-06-27T09:00:00", duration_minutes=60)  # Saturday
            assert result["status"] == "found"
            assert result.get("conflict_reason") == "weekend"
            assert len(result["suggestions"]) > 0
            assert "start" in result["suggestions"][0]
            assert "end" in result["suggestions"][0]
            assert "label" in result["suggestions"][0]

    def test_limited_to_max_suggestions(self, mock_calendar_service):
        """Should not return more than MAX_SUGGESTIONS alternatives."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives(
                "2026-06-27T09:00:00", duration_minutes=30)  # Saturday
            assert len(result["suggestions"]) <= MAX_SUGGESTIONS

    def test_invalid_time_format(self, mock_calendar_service):
        """Should return error for invalid time format."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives("not-a-valid-date")
            assert result["status"] == "error"

    def test_not_found_when_all_busy(self, mock_calendar_service_with_conflict):
        """Should return not_found when the requested slot is busy and all alternatives are busy."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service_with_conflict):
            result = find_alternatives(
                "2026-06-10T09:00:00", duration_minutes=60)
            assert result["status"] == "not_found"
            assert result.get("conflict_reason") == "slot_is_busy"
            assert result["suggestions"] == []

    def test_no_conflict_when_slot_is_free(self, mock_calendar_service):
        """Should return no_conflict when the requested slot is actually free."""
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives(
                "2026-06-10T09:00:00", duration_minutes=60)
            assert result["status"] == "no_conflict"
            assert result.get("conflict_reason") == "slot_is_free"
            assert result["suggestions"] == []

    def test_error_handling(self, mock_calendar_service):
        """Should handle exceptions gracefully and include conflict_reason='error'."""
        mock_calendar_service.freebusy().query().execute.side_effect = Exception("API error")
        with patch("app.agents.conflict_agent._get_service", return_value=mock_calendar_service):
            result = find_alternatives("2026-06-10T09:00:00")
            assert result["status"] == "error"
            assert result.get("conflict_reason") == "error"
            assert "message" in result


# ══════════════════════════════════════════════════════════════════════════════
# Part 2 — conflict_agent pure-logic tests (standalone, from tests/unit)
# ══════════════════════════════════════════════════════════════════════════════

# ── helpers ──────────────────────────────────────────────────────────────────

def _all_slots(from_dt: datetime, duration: int = 60):
    return list(_candidate_slots(from_dt, duration))


def _make_find_alternatives_result(requested: str, free: bool = True):
    """Helper: run find_alternatives with fully mocked calendar."""
    with patch("app.agents.conflict_agent._get_service") as mock_svc, \
         patch("app.agents.conflict_agent._is_slot_free", return_value=free):
        mock_svc.return_value = MagicMock()
        return find_alternatives(requested, duration_minutes=60)


# ── Bug 1: weekend filtering ──────────────────────────────────────────────────

def test_no_weekend_slots_starting_saturday():
    """Starting on Saturday: every yielded slot must be Mon–Fri."""
    # 2026-06-27 is a Saturday
    saturday = datetime(2026, 6, 27, 8, 0, tzinfo=ICT)
    slots = _all_slots(saturday)
    assert slots, "Expected slots in the 7-day window"
    for start, _ in slots:
        assert start.weekday() < 5, (
            f"Weekend slot yielded: {start.strftime('%A %Y-%m-%d %H:%M')}"
        )


def test_no_weekend_slots_starting_sunday():
    """Starting on Sunday: every yielded slot must be Mon–Fri."""
    # 2026-06-28 is a Sunday
    sunday = datetime(2026, 6, 28, 10, 0, tzinfo=ICT)
    slots = _all_slots(sunday)
    assert slots
    for start, _ in slots:
        assert start.weekday() < 5, (
            f"Weekend slot yielded: {start.strftime('%A %Y-%m-%d %H:%M')}"
        )


def test_no_weekend_slots_spanning_weekend():
    """Starting Friday afternoon: slots must skip Sat/Sun and resume Monday."""
    # 2026-06-26 is a Friday
    friday_pm = datetime(2026, 6, 26, 16, 0, tzinfo=ICT)
    slots = _all_slots(friday_pm, duration=60)
    for start, _ in slots:
        assert start.weekday() < 5, (
            f"Weekend slot yielded: {start.strftime('%A %Y-%m-%d %H:%M')}"
        )


# ── Bug 1 + slot-end guard ────────────────────────────────────────────────────

def test_slot_end_does_not_exceed_working_hours():
    """No yielded slot should have slot_end.hour > WORKING_HOURS_END."""
    monday = datetime(2026, 6, 29, 8, 0, tzinfo=ICT)
    slots = _all_slots(monday, duration=180)  # 3-hour meetings amplify overruns
    assert slots
    for start, end in slots:
        assert end.hour <= WORKING_HOURS_END, (
            f"Slot ends past working hours: {start}–{end}"
        )


# ── detect_conflict ───────────────────────────────────────────────────────────

def test_detect_conflict_weekend():
    """Saturday → is_conflict True, reason 'weekend' (no API call)."""
    result = detect_conflict("2026-06-27T09:00:00")  # Saturday
    assert result == {"is_conflict": True, "reason": "weekend"}


def test_detect_conflict_sunday():
    result = detect_conflict("2026-06-28T09:00:00")  # Sunday
    assert result == {"is_conflict": True, "reason": "weekend"}


def test_detect_conflict_before_working_hours():
    """Hour before WORKING_HOURS_START → outside_working_hours."""
    result = detect_conflict("2026-06-29T07:30:00")  # Monday 07:30
    assert result == {"is_conflict": True, "reason": "outside_working_hours"}


def test_detect_conflict_after_working_hours():
    """Hour at or after WORKING_HOURS_END → outside_working_hours."""
    result = detect_conflict("2026-06-29T18:00:00")  # Monday 18:00
    assert result == {"is_conflict": True, "reason": "outside_working_hours"}


def test_detect_conflict_slot_is_free():
    """Free slot on a weekday in working hours → is_conflict False."""
    with patch("app.agents.conflict_agent._get_service") as mock_svc, \
         patch("app.agents.conflict_agent._is_slot_free", return_value=True):
        mock_svc.return_value = MagicMock()
        result = detect_conflict("2026-06-29T09:00:00")  # Monday 09:00
    assert result == {"is_conflict": False, "reason": "slot_is_free"}


def test_detect_conflict_slot_is_busy():
    """Busy slot on a weekday in working hours → is_conflict True, slot_is_busy."""
    with patch("app.agents.conflict_agent._get_service") as mock_svc, \
         patch("app.agents.conflict_agent._is_slot_free", return_value=False):
        mock_svc.return_value = MagicMock()
        result = detect_conflict("2026-06-29T09:00:00")
    assert result == {"is_conflict": True, "reason": "slot_is_busy"}


def test_detect_conflict_naive_string_attaches_ict():
    """Naive ISO string must be handled without ValueError; ICT is assumed."""
    with patch("app.agents.conflict_agent._get_service") as mock_svc, \
         patch("app.agents.conflict_agent._is_slot_free", return_value=True):
        mock_svc.return_value = MagicMock()
        result = detect_conflict("2026-06-29T09:00:00")  # naive, weekday in hours
    assert "is_conflict" in result
    assert "reason" in result


# ── Bug 2: naive ISO string ───────────────────────────────────────────────────

def test_naive_iso_string_does_not_raise():
    """find_alternatives must not raise for a naive ISO string; new schema always has conflict_reason."""
    with patch("app.agents.conflict_agent._get_service") as mock_svc, \
         patch("app.agents.conflict_agent._is_slot_free", return_value=True):
        mock_svc.return_value = MagicMock()
        result = find_alternatives("2026-06-30T10:00:00", duration_minutes=60)

    assert result.get("status") != "error", (
        f"Unexpected error for naive ISO string: {result.get('message')}"
    )
    assert "conflict_reason" in result, "New schema must always include conflict_reason"


def test_naive_iso_string_produces_aware_slots():
    """_candidate_slots must yield aware datetimes when from_dt is ICT-aware."""
    aware_start = datetime(2026, 6, 30, 9, 0, tzinfo=ICT)
    for start, end in _candidate_slots(aware_start, 60):
        assert start.tzinfo is not None, "start slot is naive"
        assert end.tzinfo is not None, "end slot is naive"


# ── Bug 3: after-hours guard ──────────────────────────────────────────────────

def test_after_hours_starts_next_morning():
    """Input at 19:00 ICT (past WORKING_HOURS_END): first slot must be next day at WORKING_HOURS_START."""
    # 2026-06-29 is a Monday
    monday_evening = datetime(2026, 6, 29, 19, 0, tzinfo=ICT)
    slots = _all_slots(monday_evening, duration=60)
    assert slots
    first_start, _ = slots[0]
    expected_date = (monday_evening + timedelta(days=1)).date()
    assert first_start.date() == expected_date, (
        f"Expected first slot on {expected_date}, got {first_start.date()}"
    )
    assert first_start.hour == WORKING_HOURS_START
    assert first_start.minute == 0


def test_exactly_at_working_hours_end_advances_to_next_day():
    """Input at exactly WORKING_HOURS_END (18:00): first slot is next morning."""
    monday_18 = datetime(2026, 6, 29, 18, 0, tzinfo=ICT)
    slots = _all_slots(monday_18, duration=60)
    assert slots
    first_start, _ = slots[0]
    assert first_start.date() > monday_18.date(), (
        "First slot should be on a later day when starting at WORKING_HOURS_END"
    )
    assert first_start.hour == WORKING_HOURS_START


# ── Suggestion format ─────────────────────────────────────────────────────────

def test_suggestion_start_end_include_utc_offset():
    """start and end must carry the explicit +07:00 UTC offset."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    for s in result["suggestions"]:
        assert s["start"].endswith("+07:00"), (
            f"start missing +07:00 offset: {s['start']}"
        )
        assert s["end"].endswith("+07:00"), (
            f"end missing +07:00 offset: {s['end']}"
        )


def test_suggestion_label_format():
    """Label must match '{weekday}, dd/mm/yyyy lúc HH:MM – HH:MM (ICT)'."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    pattern = re.compile(
        r"^(Thứ Hai|Thứ Ba|Thứ Tư|Thứ Năm|Thứ Sáu|Thứ Bảy|Chủ Nhật), "
        r"\d{2}/\d{2}/\d{4} lúc \d{2}:\d{2} – \d{2}:\d{2} \(ICT\)$"
    )
    for s in result["suggestions"]:
        assert pattern.match(s["label"]), (
            f"Label does not match expected format: {s['label']!r}"
        )


def test_suggestion_label_no_old_typo():
    """Label must use 'lúc' (with accent), not the old 'luc' typo."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    for s in result["suggestions"]:
        assert " lúc " in s["label"], f"'lúc' not found in label: {s['label']!r}"
        assert " luc " not in s["label"], f"Old typo 'luc' found in label: {s['label']!r}"


def test_suggestion_has_proximity_hours():
    """Each suggestion must have a proximity_hours float rounded to 1 decimal."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    for s in result["suggestions"]:
        assert "proximity_hours" in s, "proximity_hours key missing"
        ph = s["proximity_hours"]
        assert isinstance(ph, float), f"proximity_hours must be float, got {type(ph)}"
        assert ph >= 0, "proximity_hours must be non-negative"
        assert round(ph, 1) == ph, "proximity_hours must be rounded to 1 decimal place"


def test_suggestions_sorted_by_proximity_hours():
    """Suggestions must be sorted by proximity_hours ascending."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    hours = [s["proximity_hours"] for s in result["suggestions"]]
    assert hours == sorted(hours), (
        f"Suggestions not sorted by proximity_hours: {hours}"
    )


def test_suggestions_capped_at_max_suggestions():
    """No more than MAX_SUGGESTIONS suggestions should be returned."""
    result = _make_find_alternatives_result("2026-06-27T09:00:00", free=True)
    assert result["status"] == "found"
    assert len(result["suggestions"]) <= MAX_SUGGESTIONS


# ══════════════════════════════════════════════════════════════════════════════
# Part 3 — conflict_agent Google API integration tests (from tests/integration)
# ══════════════════════════════════════════════════════════════════════════════

# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def calendar_service():
    """
    Yields a MagicMock Google Calendar service with googleapiclient.discovery.build
    patched so all Calendar API traffic is intercepted.

    Configure freebusy responses on the returned object:
        service.freebusy.return_value.query.return_value.execute.return_value = {...}
        service.freebusy.return_value.query.return_value.execute.side_effect = [...]
    """
    mock_creds = MagicMock()
    mock_creds.valid = True

    with (
        patch("app.agents.conflict_agent.os.path.exists", return_value=True),
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ),
        patch("app.agents.conflict_agent.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        yield mock_service


# ── Freebusy response helpers ─────────────────────────────────────────────────

def _free_response():
    """Calendar reports no busy events."""
    return {"calendars": {"primary": {"busy": []}}}


def _busy_response():
    """Calendar reports a busy event overlapping the queried window."""
    return {"calendars": {"primary": {"busy": [
        {"start": "2026-06-29T02:00:00Z", "end": "2026-06-29T02:30:00Z"}
    ]}}}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_free_slot_returns_no_conflict(calendar_service):
    """
    When the freebusy API reports no busy events for the requested slot,
    find_alternatives must return status='no_conflict' immediately and return
    an empty suggestions list.
    """
    calendar_service.freebusy.return_value.query.return_value.execute.return_value = (
        _free_response()
    )

    result = find_alternatives("2026-06-29T09:00:00", duration_minutes=60)

    assert result["status"] == "no_conflict"
    assert result["conflict_reason"] == "slot_is_free"
    assert result["suggestions"] == []


def test_busy_slot_finds_three_sorted_alternatives(calendar_service):
    """
    When the requested slot is busy and enough free slots follow, find_alternatives
    must return status='found' with exactly MAX_SUGGESTIONS suggestions.

    Call sequence for "2026-06-29T09:00:00" (Monday):
      call #1  detect_conflict checks 09:00–10:00 → busy
      (09:00 candidate skipped: start_dt == requested_dt, no API call)
      call #2  09:30–10:30 → free → suggestion 1
      call #3  10:00–11:00 → free → suggestion 2
      call #4  10:30–11:30 → free → suggestion 3  → break
    """
    calendar_service.freebusy.return_value.query.return_value.execute.side_effect = [
        _busy_response(),   # call 1 — conflict check
        _free_response(),   # call 2 — alternative 1
        _free_response(),   # call 3 — alternative 2
        _free_response(),   # call 4 — alternative 3
    ]

    result = find_alternatives("2026-06-29T09:00:00", duration_minutes=60)

    assert result["status"] == "found"
    assert result["conflict_reason"] == "slot_is_busy"
    assert len(result["suggestions"]) == MAX_SUGGESTIONS

    for s in result["suggestions"]:
        assert s["start"].endswith("+07:00"), (
            f"start missing +07:00 offset: {s['start']!r}"
        )
        assert s["end"].endswith("+07:00"), (
            f"end missing +07:00 offset: {s['end']!r}"
        )
        assert isinstance(s["proximity_hours"], float)
        assert s["proximity_hours"] >= 0.0

    hours = [s["proximity_hours"] for s in result["suggestions"]]
    assert hours == sorted(hours), f"Suggestions not sorted: {hours}"


def test_busy_slot_no_free_alternatives(calendar_service):
    """
    When every freebusy query returns busy (the entire search window is occupied),
    find_alternatives must return status='not_found' with an empty suggestions list.
    """
    calendar_service.freebusy.return_value.query.return_value.execute.return_value = (
        _busy_response()
    )

    result = find_alternatives("2026-06-29T09:00:00", duration_minutes=60)

    assert result["status"] == "not_found"
    assert result["conflict_reason"] == "slot_is_busy"
    assert result["suggestions"] == []


def test_saturday_input_weekend_reason_weekday_suggestions(calendar_service):
    """
    Requesting a Saturday slot:
    - detect_conflict must return 'weekend' without any freebusy API call.
    - Alternatives must land on Mon–Fri only.
    - 2026-06-27 is a Saturday.
    """
    calendar_service.freebusy.return_value.query.return_value.execute.return_value = (
        _free_response()
    )

    result = find_alternatives("2026-06-27T09:00:00", duration_minutes=60)

    assert result["status"] == "found"
    assert result["conflict_reason"] == "weekend"
    assert len(result["suggestions"]) > 0

    for s in result["suggestions"]:
        start_dt = datetime.fromisoformat(s["start"])
        assert start_dt.weekday() < 5, (
            f"Weekend slot returned as suggestion: "
            f"{start_dt.strftime('%A')} {s['start']}"
        )


def test_after_hours_input_outside_working_hours_reason(calendar_service):
    """
    Requesting a slot at 19:00 ICT (past WORKING_HOURS_END):
    - detect_conflict must return 'outside_working_hours' without an API call.
    - Alternatives must start from the next working morning (≥ WORKING_HOURS_START).
    - 2026-06-29 is a Monday; next morning is Tuesday 2026-06-30.
    """
    calendar_service.freebusy.return_value.query.return_value.execute.return_value = (
        _free_response()
    )

    result = find_alternatives("2026-06-29T19:00:00", duration_minutes=60)

    assert result["conflict_reason"] == "outside_working_hours"
    assert result["status"] in ("found", "not_found")

    if result["suggestions"]:
        first = datetime.fromisoformat(result["suggestions"][0]["start"])
        assert first.date() > datetime(2026, 6, 29, tzinfo=ICT).date()
        assert first.hour >= WORKING_HOURS_START
        assert first.weekday() < 5


def test_invalid_iso_string_returns_error(calendar_service):
    """
    An unparseable input string must cause find_alternatives to return
    status='error' with a 'message' key. No exception must reach the caller.
    """
    result = find_alternatives("not-a-valid-date")

    assert result["status"] == "error"
    assert "message" in result
    assert result.get("conflict_reason") == "error"
    assert result.get("suggestions") == []


def test_google_api_exception_is_caught(calendar_service):
    """
    If the freebusy API call raises an exception, find_alternatives must catch it
    and return status='error' with the exception message in the 'message' field.
    The exception must NOT propagate to the caller.
    """
    calendar_service.freebusy.return_value.query.return_value.execute.side_effect = (
        Exception("Google API unavailable")
    )

    result = find_alternatives("2026-06-29T09:00:00", duration_minutes=60)

    assert result["status"] == "error"
    assert "message" in result
    assert "Google API unavailable" in result["message"]
    assert result.get("conflict_reason") == "error"
    assert result.get("suggestions") == []


# ══════════════════════════════════════════════════════════════════════════════
# Part 4 — LangGraph pipeline integration tests
# ══════════════════════════════════════════════════════════════════════════════


def _make_email(intent="schedule", confidence=0.9):
    email = MagicMock()
    email.sender = "sender@example.com"
    email.subject = "Test Subject"
    email.body = "Test body"
    email.gmail_message_id = "gmail_msg_123"
    email.thread_id = "thread_456"
    return email


def _email_result(intent="schedule", confidence=0.9, **extra):
    base = {
        "intent": intent,
        "confidence": confidence,
        "summary": "Test summary",
        "category": "Other",
        "priority": "Medium",
        "action_required": False,
        "important_note": None,
        "sentiment": None,
        "detected_language": "vi",
        "time": "2026-06-29T09:00:00",
    }
    base.update(extra)
    return base


async def test_schedule_flow_returns_schedule_type():
    """
    intent='schedule', confidence=0.9 → graph routes through node_schedule
    and returns type='schedule_flow' with the action_id from create_pending_action.
    """
    email = _make_email(intent="schedule", confidence=0.9)
    er = _email_result(intent="schedule", confidence=0.9)
    calendar_result = {"status": "free", "start": "2026-06-29T09:00:00"}

    with (
        patch("app.orchestrator.orchestrator.process_email", return_value=er),
        patch("app.orchestrator.orchestrator.priority_scoring_skill", return_value=50),
        patch("app.orchestrator.orchestrator.insert_email_insight", return_value=1),
        patch("app.orchestrator.orchestrator.check_calendar_availability", return_value=calendar_result),
        patch("app.orchestrator.orchestrator.create_pending_action", return_value=42),
        patch("app.orchestrator.orchestrator.log_event"),
    ):
        from app.orchestrator.orchestrator import run_pipeline
        result = await run_pipeline(email)

    assert result["type"] == "schedule_flow"
    assert result["data"]["action_id"] == 42
    assert result["data"]["calendar"] == calendar_result
    assert "email" in result["data"]


async def test_low_confidence_routes_to_unclear_flow():
    """
    confidence=0.3 (< LOW_CONFIDENCE_THRESHOLD=0.5) → graph routes through
    node_low_confidence regardless of intent, returning type='unclear_flow'.
    """
    email = _make_email(intent="schedule", confidence=0.3)
    er = _email_result(intent="schedule", confidence=0.3)

    with (
        patch("app.orchestrator.orchestrator.process_email", return_value=er),
        patch("app.orchestrator.orchestrator.priority_scoring_skill", return_value=20),
        patch("app.orchestrator.orchestrator.insert_email_insight", return_value=2),
        patch("app.orchestrator.orchestrator.create_pending_action", return_value=99),
        patch("app.orchestrator.orchestrator.log_event"),
    ):
        from app.orchestrator.orchestrator import run_pipeline
        result = await run_pipeline(email)

    assert result["type"] == "unclear_flow"
    assert result["data"]["action_id"] == 99
    assert "email" in result["data"]


async def test_cancel_flow_returns_cancel_type():
    """
    intent='cancel', confidence=0.85 → graph routes through node_cancel
    and returns type='cancel_flow'.
    """
    email = _make_email(intent="cancel", confidence=0.85)
    er = _email_result(intent="cancel", confidence=0.85)

    with (
        patch("app.orchestrator.orchestrator.process_email", return_value=er),
        patch("app.orchestrator.orchestrator.priority_scoring_skill", return_value=40),
        patch("app.orchestrator.orchestrator.insert_email_insight", return_value=3),
        patch("app.orchestrator.orchestrator.create_pending_action", return_value=77),
        patch("app.orchestrator.orchestrator.log_event"),
    ):
        from app.orchestrator.orchestrator import run_pipeline
        result = await run_pipeline(email)

    assert result["type"] == "cancel_flow"
    assert result["data"]["action_id"] == 77
    assert "email" in result["data"]


async def test_other_flow_calls_intelligence_agent():
    """
    intent='other', confidence=0.8 → graph routes through node_other,
    classify_intelligence is called exactly once with the email object,
    and result contains 'intelligence' in data.
    """
    email = _make_email(intent="other", confidence=0.8)
    er = _email_result(intent="other", confidence=0.8)
    intelligence_result = {
        "category": "newsletter",
        "importance_score": 20,
        "summary": "A newsletter",
        "extracted_data": {},
    }

    mock_classify = MagicMock(return_value=intelligence_result)

    with (
        patch("app.orchestrator.orchestrator.process_email", return_value=er),
        patch("app.orchestrator.orchestrator.priority_scoring_skill", return_value=20),
        patch("app.orchestrator.orchestrator.insert_email_insight", return_value=4),
        patch("app.orchestrator.orchestrator.classify_intelligence", mock_classify),
        patch("app.orchestrator.orchestrator.insert_email_analysis", return_value=5),
        patch("app.orchestrator.orchestrator.log_event"),
    ):
        from app.orchestrator.orchestrator import run_pipeline
        result = await run_pipeline(email)

    assert result["type"] == "other_flow"
    assert "intelligence" in result["data"]
    assert result["data"]["intelligence"] == intelligence_result
    mock_classify.assert_called_once_with(email)
