import logging
from datetime import date, datetime, timedelta, timezone

from googleapiclient.errors import HttpError

from app.core.auth import get_calendar_service
from app.core.config import settings

logger = logging.getLogger(__name__)
_get_service = get_calendar_service  # alias for test patching

DEFAULT_DURATION = 60
CALENDAR_ID = "primary"
ICT = timezone(timedelta(hours=7))
WORKING_HOURS_START = 8
WORKING_HOURS_END = 18


# ── Helpers
def _check_conflict(service, start_dt: datetime, end_dt: datetime) -> list:
    start_utc = start_dt.replace(tzinfo=ICT).astimezone(timezone.utc)
    end_utc = end_dt.replace(tzinfo=ICT).astimezone(timezone.utc)
    body = {
        "timeMin": start_utc.isoformat().replace("+00:00", "Z"),
        "timeMax": end_utc.isoformat().replace("+00:00", "Z"),
        "items":   [{"id": CALENDAR_ID}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    return busy


def _create_event(service, summary, start_dt, end_dt, location, attendees, description=""):
    event = {
        "summary":     summary,
        "location":    location or "",
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Ho_Chi_Minh"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Asia/Ho_Chi_Minh"},
        "attendees": [{"email": a} for a in attendees if "@" in str(a)],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},
                {"method": "popup", "minutes": 30},
            ],
        },
    }
    return service.events().insert(
        calendarId=CALENDAR_ID, body=event, sendUpdates="all"
    ).execute()


def _find_events_by_time(service, start_dt: datetime, end_dt: datetime) -> list:
    """Tìm tất cả event trong khoảng thời gian cho trước."""
    start_utc = start_dt.replace(tzinfo=ICT).astimezone(timezone.utc)
    end_utc = end_dt.replace(tzinfo=ICT).astimezone(timezone.utc)

    result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start_utc.isoformat().replace("+00:00", "Z"),
        timeMax=end_utc.isoformat().replace("+00:00", "Z"),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


# ── Check-only functions (HITL pipeline: inspect without side effects) ─────────

def check_calendar_availability(email_result: dict) -> dict:
    """
    Inspect whether the requested meeting slot is free without creating any event.

    Returns dict with status "free" | "conflict" | "error" and calendar metadata
    that the orchestrator stores in pending_actions.calendar_result.
    """
    time_str  = email_result.get("time")
    summary   = email_result.get("summary", "Cuộc họp")
    location  = email_result.get("location")
    attendees = email_result.get("attendees", [])

    if not time_str:
        return {"status": "error", "message": "Email không có thông tin thời gian cụ thể"}

    try:
        start_dt = datetime.fromisoformat(time_str)
    except ValueError:
        return {"status": "error", "message": f"Định dạng thời gian không hợp lệ: {time_str}"}

    end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION)
    logger.info("[CalendarAgent] Kiểm tra khả dụng | start=%s | end=%s", start_dt, end_dt)

    try:
        service = _get_service()
        busy = _check_conflict(service, start_dt, end_dt)

        base = {
            "requested_time": time_str,
            "start":          start_dt.isoformat(),
            "end":            end_dt.isoformat(),
            "summary":        summary,
            "location":       location,
            "attendees":      attendees,
        }

        if busy:
            logger.info("[CalendarAgent] Conflict tại %s | busy=%s", start_dt, busy)
            return {
                **base,
                "status":     "conflict",
                "message":    f"Khung giờ {start_dt.strftime('%H:%M %d/%m/%Y')} đã bận",
                "busy_slots": busy,
            }

        logger.info("[CalendarAgent] ✓ Khung giờ %s trống", start_dt)
        return {
            **base,
            "status":     "free",
            "message":    f"Khung giờ {start_dt.strftime('%H:%M %d/%m/%Y')} trống",
            "busy_slots": [],
        }

    except HttpError as e:
        return {"status": "error", "message": f"Google Calendar lỗi: {e}"}
    except Exception as e:
        logger.exception("[CalendarAgent] Lỗi kiểm tra khả dụng")
        return {"status": "error", "message": str(e)}


def check_reschedule_availability(email_result: dict) -> dict:
    """
    Inspect whether a reschedule is feasible without modifying any event.

    Finds the old event (±1 h around old_time), then checks whether the new
    slot is free.  Returns dict with status "free" | "conflict" | "not_found" |
    "error" and the old event metadata so the orchestrator can store it in
    pending_actions.calendar_result.
    """
    new_time_str = email_result.get("time")
    old_time_str = email_result.get("old_time")

    if not new_time_str:
        return {"status": "error", "message": "Email không có thông tin giờ mới"}
    if not old_time_str:
        return {"status": "error", "message": "Email không có thông tin giờ cũ cần dời"}

    try:
        new_dt = datetime.fromisoformat(new_time_str)
        old_dt = datetime.fromisoformat(old_time_str)
    except ValueError as e:
        return {"status": "error", "message": f"Định dạng thời gian không hợp lệ: {e}"}

    new_end_dt = new_dt + timedelta(minutes=DEFAULT_DURATION)
    logger.info("[CalendarAgent] Kiểm tra dời lịch | old=%s → new=%s", old_dt, new_dt)

    try:
        service = _get_service()

        search_start = old_dt - timedelta(hours=1)
        search_end   = old_dt + timedelta(hours=1)
        events = _find_events_by_time(service, search_start, search_end)

        if not events:
            return {
                "status":   "not_found",
                "message":  f"Không tìm thấy lịch họp nào vào lúc {old_dt.strftime('%H:%M %d/%m/%Y')}",
                "old_time": old_time_str,
                "new_time": new_time_str,
            }

        target_event  = events[0]
        event_id      = target_event["id"]
        event_title   = target_event.get("summary", "Cuộc họp")
        attendees = [
            a.get("email", "")
            for a in target_event.get("attendees", [])
            if a.get("email") and a.get("email") != settings.ORGANIZER_EMAIL
        ]

        busy = _check_conflict(service, new_dt, new_end_dt)

        base = {
            "old_time":        old_time_str,
            "new_time":        new_time_str,
            "start":           new_dt.isoformat(),
            "end":             new_end_dt.isoformat(),
            "old_event_id":    event_id,
            "old_event_title": event_title,
            "attendees":       attendees,
        }

        if busy:
            return {
                **base,
                "status":     "conflict",
                "message":    f"Khung giờ mới {new_dt.strftime('%H:%M %d/%m/%Y')} đã bận",
                "busy_slots": busy,
            }

        logger.info("[CalendarAgent] ✓ Giờ mới %s trống, có thể dời", new_dt)
        return {
            **base,
            "status":     "free",
            "message":    f"Khung giờ mới {new_dt.strftime('%H:%M %d/%m/%Y')} trống",
            "busy_slots": [],
        }

    except HttpError as e:
        logger.error("[CalendarAgent] Google API error: %s", e)
        return {"status": "error", "message": f"Google Calendar lỗi: {e}"}
    except Exception as e:
        logger.exception("[CalendarAgent] Lỗi kiểm tra dời lịch")
        return {"status": "error", "message": str(e)}


# ── Public API
def process_schedule(email_result: dict) -> dict:
    """Tạo lịch mới trên Google Calendar."""
    time_str = email_result.get("time")
    summary = email_result.get("title") or email_result.get("summary", "Sự kiện")
    location = email_result.get("location")
    attendees = email_result.get("attendees", [])
    event_type = email_result.get("event_type", "other")

    if not time_str:
        return {"status": "error", "message": "Email không có thông tin thời gian cụ thể"}

    try:
        start_dt = datetime.fromisoformat(time_str)
    except ValueError:
        return {"status": "error", "message": f"Định dạng thời gian không hợp lệ: {time_str}"}

    end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION)
    logger.info("[CalendarAgent] Xử lý lịch | start=%s | end=%s | location=%s",
                start_dt, end_dt, location)

    try:
        service = _get_service()
        busy = _check_conflict(service, start_dt, end_dt)

        if busy:
            logger.warning("[CalendarAgent] Conflict! Busy slots: %s", busy)
            return {
                "status":         "conflict",
                "message":        f"Khung giờ {start_dt.strftime('%H:%M %d/%m/%Y')} đã bận",
                "busy_slots":     busy,
                "requested_time": time_str,
            }

        event = _create_event(service, summary, start_dt, end_dt, location, attendees,
                              email_result.get("raw_time_text", ""))
        event_link = event.get("htmlLink", "")
        logger.info("[CalendarAgent] ✓ Event tạo thành công: %s", event_link)

        return {
            "status":     "created",
            "message":    f"Đã tạo lịch thành công lúc {start_dt.strftime('%H:%M %d/%m/%Y')}",
            "event_id":   event.get("id"),
            "event_link": event_link,
            "event_type": event_type,
            "start":      start_dt.isoformat(),
            "end":        end_dt.isoformat(),
            "location":   location,
            "attendees":  attendees,
        }

    except HttpError as e:
        return {"status": "error", "message": f"Google Calendar lỗi: {e}"}
    except Exception as e:
        logger.exception("[CalendarAgent] Lỗi không xác định")
        return {"status": "error", "message": str(e)}


# ── Skills ────────────────────────────────────────────────────────────────────

def schedule_risk_skill(events: list) -> dict:
    """
    Analyse a list of calendar events for scheduling risks. Rule-based, no LLM.

    Args:
        events: list of event dicts with {summary, start: {dateTime}, end: {dateTime}}

    Returns:
        {"risks": [{"type", "description", "severity"}], "risk_count": int, "generated_at": str}
    """
    def _parse_event_dt(ev: dict, key: str) -> datetime | None:
        raw = ev.get(key, {})
        dt_str = raw.get("dateTime") or raw.get("date")
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _count_events_on_date(sorted_evs: list, target_date: date) -> int:
        return sum(
            1 for ev in sorted_evs
            if _parse_event_dt(ev, "start") and
            _parse_event_dt(ev, "start").astimezone(ICT).date() == target_date
        )

    sorted_events = sorted(
        [ev for ev in events if _parse_event_dt(ev, "start")],
        key=lambda e: _parse_event_dt(e, "start"),
    )

    risks = []
    seen_overloaded_dates: set = set()

    for i, ev in enumerate(sorted_events):
        ev_start = _parse_event_dt(ev, "start")
        ev_name = ev.get("summary", "Cuộc họp")
        ev_local = ev_start.astimezone(ICT)

        # Back-to-back: gap < 15 min with the previous event
        if i > 0:
            prev_end = _parse_event_dt(sorted_events[i - 1], "end")
            if prev_end:
                gap_minutes = (ev_start - prev_end).total_seconds() / 60
                if 0 <= gap_minutes < 15:
                    risks.append({
                        "type": "back_to_back",
                        "description": (
                            f"'{sorted_events[i-1].get('summary', 'Cuộc họp')}' → '{ev_name}': "
                            f"chỉ {int(gap_minutes)} phút giữa 2 cuộc họp"
                        ),
                        "severity": "warning",
                    })

        # Outside working hours
        if ev_local.hour < WORKING_HOURS_START or ev_local.hour >= WORKING_HOURS_END:
            risks.append({
                "type": "outside_hours",
                "description": (
                    f"'{ev_name}' bắt đầu lúc {ev_local.strftime('%H:%M')} "
                    f"(ngoài giờ làm việc {WORKING_HOURS_START:02d}:00–{WORKING_HOURS_END:02d}:00)"
                ),
                "severity": "info",
            })

        # Overloaded day: > 5 events
        ev_date = ev_local.date()
        if ev_date not in seen_overloaded_dates:
            day_count = _count_events_on_date(sorted_events, ev_date)
            if day_count > 5:
                seen_overloaded_dates.add(ev_date)
                risks.append({
                    "type": "overloaded_day",
                    "description": (
                        f"{ev_date.strftime('%d/%m/%Y')} có {day_count} cuộc họp "
                        f"(quá tải lịch trình)"
                    ),
                    "severity": "warning",
                })

    return {
        "risks": risks,
        "risk_count": len(risks),
        "generated_at": datetime.now(ICT).isoformat(),
    }


def availability_intelligence_skill(days_ahead: int = 3) -> dict:
    """
    Compute a free/busy summary for the next `days_ahead` working days.

    Uses the Google Calendar freebusy API (one call per day). Skips weekends
    and past time slots. Returns free windows as contiguous blocks.

    Returns:
        {
          "summary": {"total_slots", "busy_slots", "free_slots", "busy_percentage"},
          "free_windows": [{"date", "windows": [{"start", "end", "duration_minutes"}]}],
          "generated_at": str
        }
    """
    now = datetime.now(ICT)
    today = now.date()
    SLOT_MINUTES = 30

    try:
        service = _get_service()
    except Exception as exc:
        logger.error("[CalendarAgent] availability_intelligence_skill: service error: %s", exc)
        return {
            "error": str(exc),
            "summary": {"total_slots": 0, "busy_slots": 0, "free_slots": 0, "busy_percentage": 0},
            "free_windows": [],
            "generated_at": now.isoformat(),
        }

    total_slots = 0
    busy_slots = 0
    free_windows = []

    days_checked = 0
    offset = 0

    while days_checked < days_ahead:
        check_date = today + timedelta(days=offset)
        offset += 1
        if check_date.weekday() >= 5:  # skip weekends
            continue
        days_checked += 1

        # Fetch busy intervals for the full working-hours window in one API call
        day_start = datetime(check_date.year, check_date.month, check_date.day,
                             WORKING_HOURS_START, 0, tzinfo=ICT)
        day_end = datetime(check_date.year, check_date.month, check_date.day,
                           WORKING_HOURS_END, 0, tzinfo=ICT)
        start_utc = day_start.astimezone(timezone.utc)
        end_utc = day_end.astimezone(timezone.utc)

        try:
            body = {
                "timeMin": start_utc.isoformat().replace("+00:00", "Z"),
                "timeMax": end_utc.isoformat().replace("+00:00", "Z"),
                "items": [{"id": CALENDAR_ID}],
            }
            result = service.freebusy().query(body=body).execute()
            busy_intervals = result.get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
        except Exception as exc:
            logger.warning("[CalendarAgent] freebusy error for %s: %s", check_date, exc)
            busy_intervals = []

        # Walk 30-min slots and classify each as free or busy
        slot_dt = day_start
        day_free_windows = []
        window_start = None

        while slot_dt < day_end:
            slot_end = slot_dt + timedelta(minutes=SLOT_MINUTES)
            total_slots += 1

            # Past slots count as busy
            is_past = slot_end <= now
            if is_past:
                is_busy = True
            else:
                slot_start_utc = slot_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                slot_end_utc = slot_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                is_busy = any(
                    b["start"] < slot_end_utc and b["end"] > slot_start_utc
                    for b in busy_intervals
                )

            if is_busy:
                busy_slots += 1
                if window_start is not None:
                    day_free_windows.append({
                        "start": window_start.strftime("%H:%M"),
                        "end": slot_dt.strftime("%H:%M"),
                        "duration_minutes": int((slot_dt - window_start).total_seconds() / 60),
                    })
                    window_start = None
            else:
                if window_start is None:
                    window_start = slot_dt

            slot_dt = slot_end

        if window_start is not None:
            day_free_windows.append({
                "start": window_start.strftime("%H:%M"),
                "end": f"{WORKING_HOURS_END:02d}:00",
                "duration_minutes": int((day_end - window_start).total_seconds() / 60),
            })

        if day_free_windows:
            free_windows.append({"date": check_date.isoformat(), "windows": day_free_windows})

    free_slot_count = total_slots - busy_slots
    busy_pct = round(busy_slots / total_slots * 100) if total_slots > 0 else 0

    return {
        "summary": {
            "total_slots": total_slots,
            "busy_slots": busy_slots,
            "free_slots": free_slot_count,
            "busy_percentage": busy_pct,
        },
        "free_windows": free_windows,
        "generated_at": now.isoformat(),
    }


# ── Public API
def process_reschedule(email_result: dict) -> dict:
    """
    Tìm event cũ theo old_time và dời sang new_time.

    email_result cần có:
      - time     : giờ MỚI muốn dời đến (ISO 8601)
      - old_time : giờ CŨ của event cần dời (ISO 8601)

    Returns:
        dict với status: "rescheduled" | "not_found" | "conflict" | "error"
    """
    new_time_str = email_result.get("time")
    old_time_str = email_result.get("old_time")

    if not new_time_str:
        return {"status": "error", "message": "Email không có thông tin giờ mới"}
    if not old_time_str:
        return {"status": "error", "message": "Email không có thông tin giờ cũ cần dời"}

    try:
        new_dt = datetime.fromisoformat(new_time_str)
        old_dt = datetime.fromisoformat(old_time_str)
    except ValueError as e:
        return {"status": "error", "message": f"Định dạng thời gian không hợp lệ: {e}"}

    new_end_dt = new_dt + timedelta(minutes=DEFAULT_DURATION)

    logger.info("[CalendarAgent] Dời lịch | old=%s → new=%s", old_dt, new_dt)

    try:
        service = _get_service()

        # Tìm event cũ trong khoảng ±1 giờ quanh old_time
        search_start = old_dt - timedelta(hours=1)
        search_end = old_dt + timedelta(hours=1)
        events = _find_events_by_time(service, search_start, search_end)

        if not events:
            logger.warning("[CalendarAgent] Không tìm thấy event cũ để dời")
            return {
                "status":  "not_found",
                "message": f"Không tìm thấy lịch họp nào vào lúc {old_dt.strftime('%H:%M %d/%m/%Y')}",
            }

        # Kiểm tra giờ mới có conflict không
        busy = _check_conflict(service, new_dt, new_end_dt)
        if busy:
            logger.warning("[CalendarAgent] Giờ mới bị conflict: %s", busy)
            return {
                "status":         "conflict",
                "message":        f"Khung giờ mới {new_dt.strftime('%H:%M %d/%m/%Y')} đã bận",
                "busy_slots":     busy,
                "requested_time": new_time_str,
            }

        # Cập nhật event sang giờ mới
        target_event = events[0]
        event_id = target_event["id"]
        event_title = target_event.get("summary", "Cuộc họp")

        target_event["start"] = {
            "dateTime": new_dt.isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        }
        target_event["end"] = {
            "dateTime": new_end_dt.isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        }

        updated = service.events().update(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            body=target_event,
            sendUpdates="all",
        ).execute()
        event_link = updated.get("htmlLink", "")

        logger.info("[CalendarAgent] ✓ Đã dời event: %s | %s → %s",
                    event_title, old_dt, new_dt)

        # Lấy danh sách attendees từ event
        attendees = [
            a.get("email", "")
            for a in target_event.get("attendees", [])
            if a.get("email") and a.get("email") != settings.ORGANIZER_EMAIL
        ]

        return {
            "status":      "rescheduled",
            "message":     f"Da doi lich sang {new_dt.strftime('%H:%M %d/%m/%Y')}",
            "event_id":    event_id,
            "event_title": event_title,
            "old_start":   old_dt.isoformat(),
            "new_start":   new_dt.isoformat(),
            "new_end":     new_end_dt.isoformat(),
            "event_link":  event_link,
            "attendees":   attendees,
        }

    except HttpError as e:
        logger.error("[CalendarAgent] Google API error: %s", e)
        return {"status": "error", "message": f"Google Calendar lỗi: {e}"}
    except Exception as e:
        logger.exception("[CalendarAgent] Lỗi không xác định")
        return {"status": "error", "message": str(e)}
