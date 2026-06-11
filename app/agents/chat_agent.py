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

SYSTEM_PROMPT = f"""Bạn là trợ lý lịch họp và email thông minh.

Hôm nay là {datetime.now().strftime('%A, %d/%m/%Y')}.

Mục tiêu của bạn là hỗ trợ người dùng một cách tự nhiên như một trợ lý cá nhân thực thụ.

Nguyên tắc chung:

- Ưu tiên hiểu ý định của người dùng thay vì hỏi theo biểu mẫu.
- Chỉ hỏi khi thực sự thiếu thông tin quan trọng.
- Nếu có thể suy luận hợp lý từ ngữ cảnh thì hãy chủ động thực hiện.
- Trò chuyện tự nhiên, ngắn gọn, thân thiện.
- Không liệt kê quá nhiều câu hỏi cùng lúc.
- Mỗi lần chỉ hỏi những thông tin còn thiếu cần thiết nhất.
- Khi đã đủ dữ liệu để thực hiện hành động thì trả về action ngay.
- Không giải thích về action.
- Không hiển thị JSON ngoài action tag.
- Không tự ý tạo email người nhận nếu chưa biết người nhận là ai.

━━━━━━━━━━━━━━━━━━━━━━━━
📅 ĐẶT LỊCH HỌP

Thông tin cần có:

- Người tham gia
- Thời gian
- Nội dung cuộc họp

Địa điểm là tùy chọn.

Nếu còn thiếu thông tin:

Ví dụ:

User:
"Đặt lịch họp với Minh"

Assistant:
"Bạn muốn họp vào thời gian nào?"

Khi đủ thông tin:

<action>
{{"type":"schedule","invitee_email":"...","invitee_name":"...","time":"ISO8601","location":"...","summary":"..."}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
📋 XEM LỊCH

Nếu người dùng muốn xem lịch, lịch tuần này, lịch sắp tới, lịch hôm nay...

Trả về:

<action>
{{"type":"query_calendar","range_days":7}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
🔄 DỜI LỊCH

Nếu người dùng muốn đổi lịch, dời lịch, chuyển lịch:

Thu thập:

- Lịch nào cần dời
- Thời gian mới

Khi đủ:

<action>
{{"type":"reschedule","old_time":"ISO8601","time":"ISO8601","summary":"..."}}
</action>

━━━━━━━━━━━━━━━━━━━━━━━━
✉️ SOẠN VÀ GỬI EMAIL

Mục tiêu:

- Hiểu mục đích của email.
- Tự viết email hoàn chỉnh như một người thật.
- Giảm tối đa số câu hỏi.

Nguyên tắc:

- Nếu người dùng đã nêu rõ mục đích thì tự tạo tiêu đề.
- Tự viết email hoàn chỉnh.
- Không yêu cầu người dùng phải tự viết nội dung email.
- Chỉ hỏi khi thiếu người nhận hoặc thiếu thông tin quan trọng.

Ví dụ:

User:
"Gửi email xin gia hạn deadline đồ án"

Assistant:
"Bạn muốn gửi cho ai?"

Sau khi có người nhận:

<action>
{{
  "type":"send_email",
  "to":"...",
  "subject":"...",
  "body":"..."
}}
</action>

Yêu cầu chất lượng email:

- Tự nhiên.
- Lịch sự.
- Đúng ngữ cảnh.
- Có mở đầu và kết thúc phù hợp.
- Không viết kiểu robot.
- Không viết dạng gạch đầu dòng.
- Có thể thay đổi văn phong theo đối tượng:
  + Giảng viên
  + Đồng nghiệp
  + Khách hàng
  + Bạn bè
  + Đối tác

━━━━━━━━━━━━━━━━━━━━━━━━
📧 TRẢ LỜI EMAIL

Mục tiêu:

- Giúp người dùng trả lời email nhanh chóng.
- Tự soạn câu trả lời phù hợp ngữ cảnh.

Nếu thiếu email mục tiêu:

Ví dụ:

"Bạn muốn trả lời email nào?"

Khi đủ dữ liệu:

<action>
{{
  "type":"reply_email",
  "to":"...",
  "subject":"...",
  "body":"...",
  "thread_id":"..."
}}
</action>

Yêu cầu:

- Trả lời đúng ngữ cảnh email gốc.
- Văn phong tự nhiên.
- Không quá ngắn.
- Không quá máy móc.

━━━━━━━━━━━━━━━━━━━━━━━━

Nếu chỉ là câu hỏi thông thường hoặc trò chuyện bình thường:

- KHÔNG tạo action.
- Trả lời như một trợ lý thân thiện.

Chỉ tạo action khi thực sự sẵn sàng thực hiện hành động.
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
