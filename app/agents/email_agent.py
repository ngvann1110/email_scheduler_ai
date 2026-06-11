import json
import logging
import re

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ── Prompt
SYSTEM_PROMPT = """Bạn là AI chuyên phân tích email lịch họp.
Đọc email và trả về JSON theo đúng schema sau.
Chỉ trả về JSON thuần, KHÔNG markdown, KHÔNG giải thích thêm.

Schema:
{
   "intent": "<schedule | reschedule | inquiry | send_email | reply_email | other>",
  "summary": "<tóm tắt 1-2 câu>",
  "time": "<ISO 8601 giờ MỚI hoặc null>",
  "old_time": "<ISO 8601 giờ CŨ cần dời - chỉ dùng cho reschedule, còn lại null>",
  "location": "<địa điểm hoặc null>",
  "attendees": ["<email hoặc tên>"],
  "confidence": <0.0 - 1.0>,
  "raw_time_text": "<chuỗi thời gian gốc trong email hoặc null>"
}

Quy tắc intent:
- schedule   : muốn đặt / tạo lịch mới
- send_email  : muốn soạn / gửi email mới
- reply_email : muốn trả lời email
- reschedule : muốn dời lịch sang giờ khác
- inquiry    : hỏi về lịch, không đặt mới
- other      : không liên quan lịch họp

Quy tắc time:
- Chuyển sang ISO 8601 nếu suy ra được
  VD: "14:00 thứ Hai 28/04/2026" → "2026-04-28T14:00:00"
- Nếu thiếu năm, dùng năm hiện tại
- Nếu không có thời gian → null

Quy tắc old_time (chỉ cho reschedule):
- Là giờ CŨ của lịch muốn dời, trích từ email
- VD: "dời lịch 14h thứ Hai sang 10h thứ Tư" → old_time="2026-04-27T14:00:00", time="2026-04-29T10:00:00"
- Nếu không phải reschedule → null
"""


# ── Helpers
def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Không tìm thấy JSON: {raw[:300]}")
    return json.loads(match.group())


def _fallback(reason: str) -> dict:
    return {
        "intent":        "other",
        "summary":       f"Không thể phân tích: {reason}",
        "time":          None,
        "location":      None,
        "attendees":     [],
        "confidence":    0.0,
        "raw_time_text": None,
        "error":         reason,
    }


# ── Public API
def process_email(email) -> dict:
    """
    Nhận EmailSchema (hoặc bất kỳ object có .sender .subject .body .timestamp)
    Trả về dict: intent, summary, time, location, attendees, confidence, raw_time_text
    """
    subject = getattr(email, "subject",   "") or ""
    body = getattr(email, "body",      "") or ""
    sender = getattr(email, "sender",    "unknown")
    timestamp = getattr(email, "timestamp", "")

    user_message = (
        f"From: {sender}\n"
        f"Timestamp: {timestamp}\n"
        f"Subject: {subject}\n\n"
        f"{body}"
    )

    logger.info(
        "[EmailAgent] Gọi GPT-4o | sender=%s | subject=%s", sender, subject)

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        logger.debug("[EmailAgent] Raw: %s", raw)

        result = _extract_json(raw)
        result.setdefault("intent",        "other")
        result.setdefault("summary",       "")
        result.setdefault("time",          None)
        result.setdefault("location",      None)
        result.setdefault("attendees",     [])
        result.setdefault("confidence",    0.5)
        result.setdefault("raw_time_text", None)

        logger.info(
            "[EmailAgent] ✓ intent=%s | confidence=%.2f | time=%s",
            result["intent"], result["confidence"], result["time"],
        )
        return result

    except Exception as e:
        logger.exception("[EmailAgent] Lỗi: %s", e)
        return _fallback(str(e))
