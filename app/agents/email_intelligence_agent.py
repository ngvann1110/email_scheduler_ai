"""
Email Intelligence Agent

Classifies non-calendar business emails, generates summaries, and extracts
structured information for analytics and the daily digest.

Covers Phase 3 (classification), Phase 4 (summarization), Phase 5 (extraction).

Public API:
    process_email(email) -> dict
"""

import json
import logging
import re

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── OpenAI client ──────────────────────────────────────────────────────────
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ── Prompt ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Bạn là AI chuyên phân tích email kinh doanh (không liên quan đặt lịch họp).
Đọc email và trả về JSON theo đúng schema sau.
Chỉ trả về JSON thuần, KHÔNG markdown, KHÔNG giải thích thêm.

Schema:
{
  "category": "<meeting | report | partnership | support | announcement | other>",
  "importance_score": <0-100>,
  "summary": "<tóm tắt 3-5 gạch đầu dòng, mỗi dòng bắt đầu bằng \"- \", tối đa 100 từ, tiếng Việt>",
  "extracted_data": {
    "deadline": "<ISO 8601 hoặc null>",
    "owner": "<tên người phụ trách hoặc null>",
    "project": "<tên dự án hoặc null>",
    "meeting_date": "<ISO 8601 hoặc null>",
    "meeting_location": "<địa điểm hoặc null>",
    "meeting_attendees": ["<tên hoặc email>"],
    "key_points": ["<ý chính>"],
    "action_items": ["<hành động cần làm>"]
  }
}

Quy tắc category:
- meeting     : email liên quan cuộc họp, hội thảo, meeting (nhưng KHÔNG phải email đặt/cancel/reschedule lịch)
- report      : báo cáo, báo cáo tiến độ, báo cáo tài chính, KPI
- partnership : hợp tác, đối tác, MOU, proposal, ký kết
- support     : hỗ trợ khách hàng, yêu cầu giúp đỡ, ticket, vấn đề kỹ thuật
- announcement: thông báo nội bộ, tin tức, cập nhật chính sách
- other       : không thuộc các loại trên

Quy tắc importance_score:
- 80-100: khẩn cấp, cần hành động ngay, deadline gần
- 60-79 : quan trọng, cần phản hồi trong ngày
- 40-59 : bình thường
- 20-39 : thông tin tham khảo
- 0-19  : spam hoặc không quan trọng

Quy tắc summary:
- 3-5 gạch đầu dòng
- Mỗi dòng bắt đầu bằng "- "
- Tối đa 100 từ
- Giữ lại sự kiện, số liệu, tên quan trọng
- Loại bỏ từ không cần thiết
- Tiếng Việt

Quy tắc extracted_data:
- deadline: chỉ trích xuất nếu email đề cập deadline cụ thể (ISO 8601)
- owner: tên người được giao nhiệm vụ
- project: tên dự án được nhắc đến
- meeting_date/meeting_location/meeting_attendees: chỉ cho category=meeting
- key_points: 1-3 ý chính của email
- action_items: các hành động cần thực hiện
- Nếu không có thông tin → null hoặc mảng rỗng []
"""


# ── Helpers ─────────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict:
    """Extract JSON object from raw GPT response (robust)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Không tìm thấy JSON: {raw[:300]}")
    return json.loads(match.group())


def _fallback(reason: str) -> dict:
    """Return a safe fallback dict when classification fails."""
    return {
        "category": "other",
        "importance_score": 30,
        "summary": f"- Không thể phân tích: {reason}",
        "extracted_data": {
            "deadline": None,
            "owner": None,
            "project": None,
            "meeting_date": None,
            "meeting_location": None,
            "meeting_attendees": [],
            "key_points": [],
            "action_items": [],
        },
        "error": reason,
    }


def _clean_extracted_data(data: dict) -> dict:
    """Ensure extracted_data has all required fields with correct defaults."""
    defaults = {
        "deadline": None,
        "owner": None,
        "project": None,
        "meeting_date": None,
        "meeting_location": None,
        "meeting_attendees": [],
        "key_points": [],
        "action_items": [],
    }
    # Filter out any unexpected keys from the model
    cleaned = {}
    for key in defaults:
        cleaned[key] = data.get(key, defaults[key])
    # Ensure list fields are actually lists
    for list_key in ("meeting_attendees", "key_points", "action_items"):
        if not isinstance(cleaned[list_key], list):
            cleaned[list_key] = []
    return cleaned


# ── Public API ──────────────────────────────────────────────────────────────
def process_email(email) -> dict:
    """
    Classify a non-calendar business email and extract intelligence.

    Args:
        email: EmailSchema or any object with .sender .subject .body .timestamp

    Returns:
        dict: {
            "category": str,
            "importance_score": int,
            "summary": str,
            "extracted_data": dict,
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
        "[EmailIntelligenceAgent] Gọi GPT-4o | sender=%s | subject=%s",
        sender, subject,
    )

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
        logger.debug("[EmailIntelligenceAgent] Raw: %s", raw)

        result = _extract_json(raw)

        # ── Normalise ──────────────────────────────────────────────────
        category = result.get("category", "other")
        # Map LLM output to allowed categories
        valid_categories = {
            "meeting", "report", "partnership",
            "support", "announcement", "other",
        }
        if category not in valid_categories:
            logger.warning(
                "[EmailIntelligenceAgent] Invalid category '%s', fallback to 'other'",
                category,
            )
            category = "other"

        importance_score = result.get("importance_score", 50)
        try:
            importance_score = int(importance_score)
        except (ValueError, TypeError):
            importance_score = 50
        importance_score = max(0, min(100, importance_score))

        summary = result.get("summary", "")
        if not summary:
            summary = "- Không có tóm tắt"

        extracted_data = _clean_extracted_data(
            result.get("extracted_data", {}))

        logger.info(
            "[EmailIntelligenceAgent] ✓ category=%s | score=%d",
            category, importance_score,
        )
        return {
            "category": category,
            "importance_score": importance_score,
            "summary": summary,
            "extracted_data": extracted_data,
        }

    except Exception as e:
        logger.exception("[EmailIntelligenceAgent] Lỗi: %s", e)
        return _fallback(str(e))
