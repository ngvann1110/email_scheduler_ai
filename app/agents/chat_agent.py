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

_SCHEDULE_SECTION = """
━━━━━━━━━━━━━━━━━━━━━━━━
📅 ĐẶT LỊCH

Hỗ trợ các loại sự kiện (event_type):
- meeting  : Cuộc họp (có thể gửi lời mời cho người tham dự)
- study    : Học tập / Lớp học
- travel   : Công tác / Đi lại
- personal : Sự kiện cá nhân (khám bệnh, sự kiện riêng...)
- deadline : Nhắc nhở deadline / hạn nộp
- other    : Sự kiện khác

Thông tin bắt buộc:
- title : Tên sự kiện
- time  : Thời gian bắt đầu (ISO 8601)

Thông tin tùy chọn:
- location    : Địa điểm
- description : Mô tả thêm
- end_time    : Thời gian kết thúc (ISO 8601)
- attendees   : Danh sách email người tham dự — CHỈ dùng cho meeting

Quy tắc:
- Tự xác định event_type từ nội dung yêu cầu.
- KHÔNG hỏi về attendees nếu event_type không phải meeting.
- Chỉ hỏi thêm khi thực sự thiếu thời gian.
- attendees luôn là mảng (có thể rỗng []).

Ví dụ — Cuộc họp có người mời (1 người):

User: "Họp với khách hàng Acme lúc 14h mai"
Assistant: "Email của khách hàng Acme là gì để tôi gửi lời mời?"

<action>
{"type":"schedule","event_type":"meeting","title":"Họp với khách hàng Acme","time":"ISO8601","attendees":["email@example.com"],"location":""}
</action>

Ví dụ — Cuộc họp nhiều người (comma, "và", semicolon đều được):

User: "Đặt lịch họp với van@gmail.com và huy@gmail.com vào 14h ngày mai"

<action>
{"type":"schedule","event_type":"meeting","title":"Cuộc họp","time":"ISO8601","attendees":["van@gmail.com","huy@gmail.com"],"location":""}
</action>

User: "Họp nhóm lúc 10h với a@corp.com, b@corp.com, c@corp.com"

<action>
{"type":"schedule","event_type":"meeting","title":"Họp nhóm","time":"ISO8601","attendees":["a@corp.com","b@corp.com","c@corp.com"],"location":""}
</action>

Quy tắc: luôn đưa TẤT CẢ địa chỉ email được nhắc đến vào mảng attendees, dù ngăn cách bằng dấu phẩy, "và", hoặc dấu chấm phẩy.

Ví dụ — Lịch học (không cần người tham dự):

User: "Học Machine Learning lúc 8h sáng thứ Hai"

<action>
{"type":"schedule","event_type":"study","title":"Học Machine Learning","time":"ISO8601","attendees":[]}
</action>

Ví dụ — Deadline:

User: "Deadline nộp báo cáo lúc 17h ngày 30/06"

<action>
{"type":"schedule","event_type":"deadline","title":"Deadline nộp báo cáo","time":"ISO8601","attendees":[]}
</action>

Ví dụ — Công tác:

User: "Đi công tác Hà Nội từ 12/07 đến 15/07"

<action>
{"type":"schedule","event_type":"travel","title":"Công tác Hà Nội","time":"ISO8601","end_time":"ISO8601","attendees":[],"location":"Hà Nội"}
</action>

Ví dụ — Sự kiện cá nhân:

User: "Khám bệnh lúc 9h sáng thứ Sáu"

<action>
{"type":"schedule","event_type":"personal","title":"Khám bệnh","time":"ISO8601","attendees":[]}
</action>
"""

_REST_OF_PROMPT = """
━━━━━━━━━━━━━━━━━━━━━━━━
📋 XEM LỊCH

Nếu người dùng muốn xem lịch, lịch tuần này, lịch sắp tới, lịch hôm nay...

Trả về:

<action>
{"type":"query_calendar","range_days":7}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
🔄 DỜI LỊCH

Nếu người dùng muốn đổi lịch, dời lịch, chuyển lịch. Thu thập lịch cần dời và thời gian mới.

<action>
{"type":"reschedule","old_time":"ISO8601","time":"ISO8601","summary":"..."}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
✉️ SOẠN VÀ GỬI EMAIL

- Tự viết email hoàn chỉnh. Không yêu cầu người dùng tự viết nội dung.
- Chỉ hỏi khi thiếu người nhận.

<action>
{"type":"send_email","to":"...","subject":"...","body":"..."}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
📧 TRẢ LỜI EMAIL

<action>
{"type":"reply_email","to":"...","subject":"...","body":"...","thread_id":"..."}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━

Nếu chỉ là câu hỏi thông thường hoặc trò chuyện bình thường:
- KHÔNG tạo action.
- Trả lời như một trợ lý thân thiện.

Chỉ tạo action khi thực sự sẵn sàng thực hiện hành động.
"""

_HEADER = (
    "Bạn là trợ lý lịch và email thông minh.\n\n"
    f"Hôm nay là {datetime.now().strftime('%A, %d/%m/%Y')}.\n\n"
    "Mục tiêu của bạn là hỗ trợ người dùng một cách tự nhiên như một trợ lý cá nhân thực thụ.\n\n"
    "Nguyên tắc chung:\n\n"
    "- Ưu tiên hiểu ý định của người dùng thay vì hỏi theo biểu mẫu.\n"
    "- Chỉ hỏi khi thực sự thiếu thông tin quan trọng.\n"
    "- Nếu có thể suy luận hợp lý từ ngữ cảnh thì hãy chủ động thực hiện.\n"
    "- Trò chuyện tự nhiên, ngắn gọn, thân thiện.\n"
    "- Không liệt kê quá nhiều câu hỏi cùng lúc.\n"
    "- Mỗi lần chỉ hỏi những thông tin còn thiếu cần thiết nhất.\n"
    "- Khi đã đủ dữ liệu để thực hiện hành động thì trả về action ngay.\n"
    "- Không giải thích về action.\n"
    "- Không hiển thị JSON ngoài action tag.\n"
    "- Không tự ý tạo email người nhận nếu chưa biết người nhận là ai.\n"
)

SYSTEM_PROMPT = _HEADER + _SCHEDULE_SECTION + _REST_OF_PROMPT


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
            end = e.get("end", {})
            end_str = end.get("dateTime") or end.get("date", "")
            attendees = [
                a.get("email", "")
                for a in e.get("attendees", [])
                if a.get("email")
            ]
            events.append({
                "summary":   e.get("summary", "Sự kiện"),
                "start":     dt_str,
                "end":       end_str,
                "location":  e.get("location", ""),
                "link":      e.get("htmlLink", ""),
                "attendees": attendees,
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


# ── Executive intent routing ──────────────────────────────────────────────────


def _classify_executive_intent(message: str) -> str | None:
    """Thin wrapper — delegates to ChiefOfStaffAgent's canonical classifier."""
    from app.agents.chief_of_staff_agent import classify_executive_intent
    return classify_executive_intent(message)


# ── Main chat
def evaluate_email(pipeline_result: dict) -> dict:
    """Evaluate whether a pipeline result is acceptable."""
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
    # Route executive questions to ChiefOfStaffAgent before the GPT-4o call
    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    intent = _classify_executive_intent(last_user_msg)
    if intent:
        try:
            from app.agents.chief_of_staff_agent import answer_executive_question
            result = answer_executive_question(last_user_msg, last_view=None)
            logger.info("[ChatAgent] Executive route | intent=%s | skills=%s",
                        result.get("intent"), result.get("skills_used"))
            return {"reply": result["answer"], "action": None}
        except Exception as exc:
            logger.error("[ChatAgent] ChiefOfStaff routing failed: %s — falling back to GPT-4o", exc)

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
                    {"role": "system", "content": "Bạn là trợ lý lịch. Tóm tắt lịch trình dưới đây một cách tự nhiên, thân thiện bằng tiếng Việt. Dùng emoji. Nếu không có lịch thì thông báo lịch trống vui vẻ."},
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
