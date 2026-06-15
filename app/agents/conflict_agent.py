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
    """Kiểm tra 1 khung giờ có trống không."""
    start_utc = start_dt.replace(tzinfo=ICT).astimezone(timezone.utc)
    end_utc   = end_dt.replace(tzinfo=ICT).astimezone(timezone.utc)
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

    end_search = from_dt + timedelta(days=MAX_DAYS_AHEAD)

    while current < end_search:
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
          - status: "found" | "not_found" | "error"
          - suggestions: list các dict {start, end, label}
    """
    logger.info(
        "[ConflictAgent] Tìm khung giờ thay thế | requested=%s | duration=%dm",
        requested_time, duration_minutes,
    )

    try:
        requested_dt = datetime.fromisoformat(requested_time)
    except ValueError:
        return {"status": "error", "message": f"Thời gian không hợp lệ: {requested_time}"}

    try:
        service = _get_service()
        suggestions = []

        for start_dt, end_dt in _candidate_slots(requested_dt, duration_minutes):
            if start_dt == requested_dt:
                continue  # Bỏ qua khung giờ bị conflict
            if _is_slot_free(service, start_dt, end_dt):
                weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
                            "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                wd = weekdays[start_dt.weekday()]
                label = f"{wd} {start_dt.strftime('%d/%m/%Y')} luc {start_dt.strftime('%H:%M')}"

                suggestions.append({
                    "start": start_dt.isoformat(),
                    "end":   end_dt.isoformat(),
                    "label": label,
                })

                if len(suggestions) >= MAX_SUGGESTIONS:
                    break

        if suggestions:
            logger.info(
                "[ConflictAgent] ✓ Tìm được %d khung giờ trống", len(
                    suggestions)
            )
            return {"status": "found", "suggestions": suggestions}
        else:
            logger.warning(
                "[ConflictAgent] Không tìm được khung giờ trống nào")
            return {"status": "not_found", "suggestions": []}

    except Exception as e:
        logger.exception("[ConflictAgent] Lỗi: %s", e)
        return {"status": "error", "message": str(e)}
