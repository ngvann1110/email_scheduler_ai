import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Config
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]
BASE_DIR = Path(__file__).resolve().parents[1]
CREDENTIALS_FILE = BASE_DIR / settings.GOOGLE_CREDENTIALS_PATH
TOKEN_FILE = BASE_DIR / settings.GOOGLE_TOKEN_PATH
CALENDAR_ID = "primary"

WORKING_HOURS_START = 8    # 8:00 sáng
WORKING_HOURS_END = 18   # 6:00 chiều
MAX_DAYS_AHEAD = 7    # tìm trong vòng 7 ngày tới
MAX_SUGGESTIONS = 3    # đề xuất tối đa 3 khung giờ
ICT = timezone(timedelta(hours=7))


# ── Auth
def _get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


# ── Helpers
def _is_slot_free(service, start_dt: datetime, end_dt: datetime) -> bool:
    """Kiểm tra 1 khung giờ có trống không. Expects timezone-aware datetimes."""
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc   = end_dt.astimezone(timezone.utc)
    body = {
        "timeMin": start_utc.isoformat().replace("+00:00", "Z"),
        "timeMax": end_utc.isoformat().replace("+00:00", "Z"),
        "items":   [{"id": CALENDAR_ID}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    return len(busy) == 0


def _candidate_slots(from_dt: datetime, duration_minutes: int):
    """
    Generator sinh ra các khung giờ ứng viên bắt đầu từ from_dt,
    bước nhảy 30 phút, trong giờ làm việc, tối đa MAX_DAYS_AHEAD ngày.
    """
    current = from_dt.replace(second=0, microsecond=0)
    # Làm tròn lên 30 phút gần nhất
    if current.minute % 30 != 0:
        current += timedelta(minutes=30 - current.minute % 30)

    # Bug 3: if already past working hours, start from next working morning
    if current.hour >= WORKING_HOURS_END:
        current = (current + timedelta(days=1)).replace(
            hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
        )

    end_search = from_dt + timedelta(days=MAX_DAYS_AHEAD)

    while current < end_search:
        # Bug 1: skip weekend days
        if current.weekday() >= 5:
            days_to_monday = (7 - current.weekday()) % 7
            current = (current + timedelta(days=days_to_monday)).replace(
                hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0
            )
            continue

        # Bỏ qua ngoài giờ làm việc
        if WORKING_HOURS_START <= current.hour < WORKING_HOURS_END:
            slot_end = current + timedelta(minutes=duration_minutes)
            # Đảm bảo slot kết thúc trước giờ tan làm
            if slot_end.hour <= WORKING_HOURS_END:
                yield current, slot_end

        # Bước nhảy 30 phút
        current += timedelta(minutes=30)

        # Sang ngày mới → bắt đầu từ giờ làm việc
        if current.hour >= WORKING_HOURS_END:
            current = (current + timedelta(days=1)).replace(
                hour=WORKING_HOURS_START, minute=0, second=0
            )


# ── Public API
def detect_conflict(requested_time: str, duration_minutes: int = 60) -> dict:
    """
    Check whether the requested slot is actually busy.
    Returns:
      {
        "is_conflict": bool,
        "reason": "slot_is_free" | "slot_is_busy" | "outside_working_hours" | "weekend"
      }
    """
    try:
        requested_dt = datetime.fromisoformat(requested_time)
    except ValueError:
        return {"is_conflict": True, "reason": "outside_working_hours"}

    if requested_dt.tzinfo is None:
        requested_dt = requested_dt.replace(tzinfo=ICT)

    if requested_dt.weekday() >= 5:
        return {"is_conflict": True, "reason": "weekend"}

    if requested_dt.hour < WORKING_HOURS_START or requested_dt.hour >= WORKING_HOURS_END:
        return {"is_conflict": True, "reason": "outside_working_hours"}

    service = _get_service()
    slot_end = requested_dt + timedelta(minutes=duration_minutes)
    if _is_slot_free(service, requested_dt, slot_end):
        return {"is_conflict": False, "reason": "slot_is_free"}
    return {"is_conflict": True, "reason": "slot_is_busy"}


def find_alternatives(
    requested_time: str,
    duration_minutes: int = 60,
) -> dict:
    """
    Tìm các khung giờ trống thay thế gần với requested_time nhất.

    Args:
        requested_time  : ISO 8601 string của giờ bị conflict
        duration_minutes: thời lượng cuộc họp (phút)

    Returns:
        dict với:
          - status        : "found" | "not_found" | "no_conflict" | "error"
          - conflict_reason: reason from detect_conflict, or "error"
          - suggestions   : list các dict {start, end, label}
    """
    logger.info(
        "[ConflictAgent] Tìm khung giờ thay thế | requested=%s | duration=%dm",
        requested_time, duration_minutes,
    )

    try:
        requested_dt = datetime.fromisoformat(requested_time)
        # Attach ICT if the parsed datetime is naive
        if requested_dt.tzinfo is None:
            requested_dt = requested_dt.replace(tzinfo=ICT)
    except ValueError:
        return {
            "status": "error",
            "conflict_reason": "error",
            "message": f"Thời gian không hợp lệ: {requested_time}",
            "suggestions": [],
        }

    try:
        conflict = detect_conflict(requested_time, duration_minutes)
        conflict_reason = conflict["reason"]

        if not conflict["is_conflict"]:
            logger.info("[ConflictAgent] Slot trống — không cần tìm giờ thay thế")
            return {
                "status": "no_conflict",
                "conflict_reason": conflict_reason,
                "suggestions": [],
            }

        service = _get_service()
        suggestions = []
        weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
                    "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

        for start_dt, end_dt in _candidate_slots(requested_dt, duration_minutes):
            if start_dt == requested_dt:
                continue  # Bỏ qua khung giờ bị conflict
            if _is_slot_free(service, start_dt, end_dt):
                wd = weekdays[start_dt.weekday()]
                label = (
                    f"{wd}, {start_dt.strftime('%d/%m/%Y')} lúc "
                    f"{start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')} (ICT)"
                )
                proximity_hours = round(
                    abs((start_dt - requested_dt).total_seconds() / 3600), 1
                )
                suggestions.append({
                    "start":           start_dt.isoformat(),
                    "end":             end_dt.isoformat(),
                    "label":           label,
                    "proximity_hours": proximity_hours,
                })

                if len(suggestions) >= MAX_SUGGESTIONS:
                    break

        suggestions.sort(key=lambda s: s["proximity_hours"])

        if suggestions:
            logger.info(
                "[ConflictAgent] ✓ Tìm được %d khung giờ trống", len(suggestions)
            )
            return {
                "status": "found",
                "conflict_reason": conflict_reason,
                "suggestions": suggestions,
            }
        else:
            logger.warning("[ConflictAgent] Không tìm được khung giờ trống nào")
            return {
                "status": "not_found",
                "conflict_reason": conflict_reason,
                "suggestions": [],
            }

    except Exception as e:
        logger.exception("[ConflictAgent] Lỗi: %s", e)
        return {
            "status": "error",
            "conflict_reason": "error",
            "message": str(e),
            "suggestions": [],
        }
