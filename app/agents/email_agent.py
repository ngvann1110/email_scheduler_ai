import json
import logging
import re

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ── System prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Bạn là AI chuyên phân tích email.
Đọc email và trả về JSON theo đúng schema sau.
Chỉ trả về JSON thuần, KHÔNG markdown, KHÔNG giải thích thêm.

Schema:
{
   "intent": "<schedule | reschedule | inquiry | send_email | reply_email | other>",
   "category": "<Meeting | Work | Personal | Finance | Job Opportunity | Promotion | Spam | Other>",
   "priority": "<High | Medium | Low>",
   "summary": "<tóm tắt ngắn gọn 1-2 câu, dễ đọc>",
   "action_required": <true | false>,
   "important_note": "<deadline, giờ họp, yêu cầu phản hồi, hành động quan trọng – hoặc null nếu không có>",
   "time": "<ISO 8601 giờ MỚI hoặc null>",
   "old_time": "<ISO 8601 giờ CŨ cần dời – chỉ dùng cho reschedule, còn lại null>",
   "location": "<địa điểm hoặc null>",
   "attendees": ["<email hoặc tên>"],
   "confidence": <0.0 - 1.0>,
   "raw_time_text": "<chuỗi thời gian gốc trong email hoặc null>"
}

─────────────────────────────────────────────────────
Quy tắc intent (giữ nguyên để routing):
- schedule   : muốn đặt / tạo lịch mới
- send_email  : muốn soạn / gửi email mới
- reply_email : muốn trả lời email
- reschedule : muốn dời lịch sang giờ khác
- inquiry    : hỏi về lịch, không đặt mới
- other      : không liên quan lịch họp

─────────────────────────────────────────────────────
Quy tắc category (phân loại nội dung email):
- Meeting          : email liên quan cuộc họp, lịch hẹn, hội thảo
- Work             : công việc, dự án, task, báo cáo
- Personal         : email cá nhân, không liên quan công việc
- Finance          : tài chính, hóa đơn, thanh toán, ngân hàng
- Job Opportunity  : cơ hội việc làm, tuyển dụng, offer
- Promotion        : khuyến mãi, quảng cáo, marketing
- Spam             : thư rác, lừa đảo, không mong muốn
- Other            : không thuộc các loại trên

─────────────────────────────────────────────────────
Quy tắc priority:
- High   : cần phản hồi gấp, deadline gần, khẩn cấp
- Medium : quan trọng nhưng không gấp
- Low    : thông tin tham khảo, không cần hành động

─────────────────────────────────────────────────────
Quy tắc summary:
- 1-2 câu ngắn gọn
- Giữ lại sự kiện, thời gian, tên quan trọng
- Dễ đọc, dễ hiểu nhanh

─────────────────────────────────────────────────────
Quy tắc action_required:
- true  : email yêu cầu phản hồi, hành động, hoặc có deadline
- false : email chỉ để thông báo, không cần làm gì

─────────────────────────────────────────────────────
Quy tắc important_note:
- Trích xuất deadline nếu có (VD: "Deadline: 28/04/2026")
- Trích xuất giờ họp nếu có (VD: "Meeting: 14:00 thứ Hai 28/04/2026")
- Trích xuất yêu cầu phản hồi (VD: "Cần phản hồi trước 17:00")
- Trích xuất hành động quan trọng (VD: "Cần ký hợp đồng")
- Nếu không có thông tin quan trọng → null

─────────────────────────────────────────────────────
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

# ── Valid values for validation ──────────────────────────────────────────────
VALID_CATEGORIES = {
    "Meeting", "Work", "Personal", "Finance",
    "Job Opportunity", "Promotion", "Spam", "Other",
}
VALID_PRIORITIES = {"High", "Medium", "Low"}
VALID_INTENTS = {
    "schedule", "reschedule", "inquiry",
    "send_email", "reply_email", "other",
}


# ── Helpers ──────────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict:
    """Extract JSON object from raw GPT response (robust)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Không tìm thấy JSON: {raw[:300]}")
    return json.loads(match.group())


def _validate_and_normalise(result: dict) -> dict:
    """Ensure all fields are present with valid values; apply defaults/corrections."""
    # ── intent ───────────────────────────────────────────────────────────
    intent = result.get("intent", "other")
    if intent not in VALID_INTENTS:
        logger.warning(
            "[EmailAgent] Invalid intent '%s', fallback to 'other'", intent)
        intent = "other"

    # ── category ─────────────────────────────────────────────────────────
    category = result.get("category", "Other")
    if category not in VALID_CATEGORIES:
        logger.warning(
            "[EmailAgent] Invalid category '%s', fallback to 'Other'", category)
        category = "Other"

    # ── priority ─────────────────────────────────────────────────────────
    priority = result.get("priority", "Low")
    if priority not in VALID_PRIORITIES:
        logger.warning(
            "[EmailAgent] Invalid priority '%s', fallback to 'Low'", priority)
        priority = "Low"

    # ── summary ──────────────────────────────────────────────────────────
    summary = result.get("summary", "")
    if not summary or not summary.strip():
        summary = "Không thể tóm tắt"

    # ── action_required ──────────────────────────────────────────────────
    action_required = result.get("action_required", False)
    if not isinstance(action_required, bool):
        action_required = bool(action_required)

    # ── important_note ───────────────────────────────────────────────────
    important_note = result.get("important_note", None)
    if important_note is not None and not isinstance(important_note, str):
        important_note = None
    if isinstance(important_note, str) and not important_note.strip():
        important_note = None

    # ── confidence ───────────────────────────────────────────────────────
    confidence = result.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        confidence = 0.5
    confidence = round(max(0.0, min(1.0, confidence)), 2)

    return {
        "intent": intent,
        "category": category,
        "priority": priority,
        "summary": summary,
        "action_required": action_required,
        "important_note": important_note,
        "time": result.get("time", None),
        "old_time": result.get("old_time", None),
        "location": result.get("location", None),
        "attendees": result.get("attendees", []) or [],
        "confidence": confidence,
        "raw_time_text": result.get("raw_time_text", None),
    }


def _fallback(reason: str) -> dict:
    """Return a safe fallback dict when classification fails."""
    return {
        "intent": "other",
        "category": "Other",
        "priority": "Low",
        "summary": f"Không thể phân tích: {reason}",
        "action_required": False,
        "important_note": None,
        "time": None,
        "old_time": None,
        "location": None,
        "attendees": [],
        "confidence": 0.0,
        "raw_time_text": None,
        "error": reason,
    }


# ── Public API ───────────────────────────────────────────────────────────────
def process_email(email) -> dict:
    """
    Analyse an incoming email and return structured intelligence.

    Accepts any object with .sender .subject .body .timestamp

    Returns:
        dict: {
            "intent": str,            # schedule | reschedule | inquiry | ...
            "category": str,          # Meeting | Work | Personal | ...
            "priority": str,          # High | Medium | Low
            "summary": str,           # Concise 1-2 sentence summary
            "action_required": bool,  # Does this email need action?
            "important_note": str|None,  # Deadline / meeting time / key action
            "time": str|None,         # ISO 8601 proposed time
            "old_time": str|None,     # ISO 8601 old time (reschedule only)
            "location": str|None,
            "attendees": list[str],
            "confidence": float,      # 0.0 - 1.0
            "raw_time_text": str|None,
        }
    """
    subject = getattr(email, "subject", "") or ""
    body = getattr(email, "body", "") or ""
    sender = getattr(email, "sender", "unknown")
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
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        logger.debug("[EmailAgent] Raw: %s", raw)

        result = _extract_json(raw)
        normalised = _validate_and_normalise(result)

        logger.info(
            "[EmailAgent] ✓ intent=%s | category=%s | priority=%s | action_required=%s | confidence=%.2f",
            normalised["intent"],
            normalised["category"],
            normalised["priority"],
            normalised["action_required"],
            normalised["confidence"],
        )
        return normalised

    except Exception as e:
        logger.exception("[EmailAgent] Lỗi: %s", e)
        return _fallback(str(e))
