import json
import logging
import re
from datetime import datetime, timedelta, timezone

from openai import OpenAI

from app.core.auth import get_calendar_service
from app.core.config import settings

logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)
_get_calendar_service = get_calendar_service  # alias for test patching

ICT = timezone(timedelta(hours=7))

SYSTEM_PROMPT = f"""Bạn là trợ lý lịch họp thông minh. Hôm nay là {datetime.now().strftime('%A, %d/%m/%Y')}.

Nhiệm vụ: Trò chuyện tự nhiên với người dùng để giúp họ:
1. Đặt lịch họp với người khác
2. Xem lịch sắp tới
3. Huỷ lịch họp
4. Dời lịch họp sang giờ khác

━━━━━━━━━━━━━━━━━━━━━━━━
📅 KHI ĐẶT LỊCH — hỏi đủ:
- Email người được mời
- Thời gian (ngày, giờ)
- Địa điểm (nếu có)
- Nội dung cuộc họp

Khi đủ thông tin → trả về:
<action>
{{"type":"schedule","invitee_email":"...","invitee_name":"...","time":"ISO8601","location":"...","summary":"..."}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
❌ KHI HUỶ LỊCH — hỏi:
- Thời gian của lịch muốn huỷ

Khi đủ thông tin → trả về:
<action>
{{"type":"cancel","time":"ISO8601","summary":"mô tả lịch muốn huỷ"}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
🔄 KHI DỜI LỊCH — hỏi:
- Thời gian CŨ (lịch muốn dời)
- Thời gian MỚI (muốn dời sang)

Khi đủ thông tin → trả về:
<action>
{{"type":"reschedule","old_time":"ISO8601","time":"ISO8601","summary":"mô tả lịch muốn dời"}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
📋 KHI XEM LỊCH — trả về:
<action>
{{"type":"query_calendar","range_days":7}}
</action>

Nếu chỉ trả lời câu hỏi thông thường → KHÔNG cần action tag.
Trả lời bằng tiếng Việt, thân thiện và tự nhiên. Dùng emoji cho sinh động!
"""


# ── Calendar helpers
def _fetch_upcoming_events(range_days: int = 7) -> list:
    try:
        service = _get_calendar_service()
        now = datetime.now(ICT).replace(microsecond=0)
        time_min = now.astimezone(
            timezone.utc).isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(days=range_days)
                    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e.get("start", {})
            dt_str = start.get("dateTime") or start.get("date", "")
            events.append({
                "summary":  e.get("summary", "Sự kiện"),
                "start":    dt_str,
                "location": e.get("location", ""),
                "link":     e.get("htmlLink", ""),
            })
        return events
    except Exception as ex:
        logger.error("[ChatAgent] Lỗi đọc Calendar: %s", ex)
        return []


def _format_events(events: list) -> str:
    if not events:
        return "Không có lịch nào."
    weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư",
                "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    lines = []
    for e in events:
        try:
            dt = datetime.fromisoformat(
                e["start"].replace("Z", "+00:00")).astimezone(ICT)
            fmt = dt.strftime(f"{weekdays[dt.weekday()]} %d/%m/%Y lúc %H:%M")
        except Exception:
            fmt = e["start"]
        line = f"• {e['summary']} — {fmt}"
        if e.get("location"):
            line += f" @ {e['location']}"
        lines.append(line)
    return "\n".join(lines)


# ── Main chat
def evaluate_email(pipeline_result: dict) -> dict:
    """Evaluate whether a pipeline result is acceptable.

    Uses LLM to judge if the scheduling/processing result is satisfactory.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "Bạn là hệ thống đánh giá kết quả xử lý email. "
                    "Đánh giá xem kết quả có chấp nhận được không. "
                    "Trả về JSON với format: "
                    '{"acceptable": true/false, "reason": "lý do"}'
                )},
                {"role": "user", "content": json.dumps(
                    pipeline_result, default=str)},
            ],
            temperature=0.3,
            max_tokens=256,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)
    except Exception as e:
        logger.warning("[ChatAgent] evaluate_email failed: %s", e)
        return {"acceptable": True, "reason": "evaluation skipped due to error"}


def chat(messages: list) -> dict:
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *messages,
            ],
            temperature=0.7,
            max_tokens=1024,
        )

        reply = response.choices[0].message.content
        action = None

        action_match = re.search(r"<action>(.*?)</action>", reply, re.DOTALL)
        if action_match:
            try:
                action = json.loads(action_match.group(1).strip())
                reply = re.sub(r"<action>.*?</action>", "",
                               reply, flags=re.DOTALL).strip()
            except json.JSONDecodeError:
                pass

        # ── Query calendar
        if action and action.get("type") == "query_calendar":
            range_days = action.get("range_days", 7)
            events = _fetch_upcoming_events(range_days)
            event_text = _format_events(events)

            summary_resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Bạn là trợ lý lịch họp. Tóm tắt lịch trình dưới đây một cách tự nhiên, thân thiện bằng tiếng Việt. Dùng emoji. Nếu không có lịch thì thông báo lịch trống vui vẻ."},
                    {"role": "user",
                        "content": f"Danh sách lịch {range_days} ngày tới:\n{event_text}"},
                ],
                temperature=0.7,
                max_tokens=512,
            )
            reply = summary_resp.choices[0].message.content
            action = None

        logger.info("[ChatAgent] Reply: %s... | action=%s",
                    reply[:80], action.get("type") if action else None)
        return {"reply": reply, "action": action}

    except Exception as e:
        logger.exception("[ChatAgent] Lỗi: %s", e)
        return {"reply": "Xin lỗi, hệ thống đang gặp sự cố 😅 Vui lòng thử lại!", "action": None}
