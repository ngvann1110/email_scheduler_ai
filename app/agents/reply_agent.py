import json
import logging
from datetime import datetime, timedelta, timezone

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)

ICT = timezone(timedelta(hours=7))

_TONE_MAP = {
    "urgent": {
        "tone": "direct",
        "style": "concise",
        "opener_vi": "Kính gửi",
        "opener_en": "Dear",
    },
    "negative": {
        "tone": "empathetic",
        "style": "apologetic",
        "opener_vi": "Xin lỗi vì sự bất tiện này",
        "opener_en": "I apologize for the inconvenience",
    },
    "positive": {
        "tone": "warm",
        "style": "friendly",
        "opener_vi": "Cảm ơn bạn đã liên hệ",
        "opener_en": "Thank you for reaching out",
    },
    "neutral": {
        "tone": "professional",
        "style": "formal",
        "opener_vi": "Kính gửi",
        "opener_en": "Dear",
    },
}

_LANG_FULL = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}


# ── Skills ────────────────────────────────────────────────────────────────────

def tone_matching_skill(sentiment: str, language: str = "vi") -> dict:
    """
    Deterministic mapping: sentiment + language → tone guidelines for reply drafting.

    Args:
        sentiment: "positive" | "neutral" | "negative" | "urgent"
        language:  detected language code ("vi" | "en" | "ja" | "ko" | "other")

    Returns:
        {"tone": str, "style": str, "opener": str, "language_full": str}
    """
    guidelines = _TONE_MAP.get(sentiment, _TONE_MAP["neutral"])
    language_full = _LANG_FULL.get(language, "Vietnamese")
    opener_key = f"opener_{language}"
    opener = guidelines.get(opener_key, guidelines["opener_vi"])

    return {
        "tone": guidelines["tone"],
        "style": guidelines["style"],
        "opener": opener,
        "language_full": language_full,
    }


def followup_draft_skill(
    original_email: dict,
    tone_guidelines: dict,
) -> dict:
    """
    Draft a follow-up email using GPT-4o-mini, language- and tone-aware.

    Args:
        original_email:  {sender, subject, summary, days_waiting}
        tone_guidelines: output of tone_matching_skill()

    Returns:
        {"subject": str, "draft": str, "generated_at": str}
    """
    sender = original_email.get("sender", "")
    subject = original_email.get("subject", "")
    summary = original_email.get("summary", "")
    days_waiting = original_email.get("days_waiting", 0)
    language_full = tone_guidelines.get("language_full", "Vietnamese")
    tone = tone_guidelines.get("tone", "professional")
    opener = tone_guidelines.get("opener", "Kính gửi")

    prompt = (
        f"Generate a follow-up email.\n\n"
        f"IMPORTANT:\n"
        f"- Write the entire email in {language_full}\n"
        f"- Do not use any other language\n"
        f"- Use a {tone} tone\n\n"
        f"Context:\n"
        f"- Originally sent to: {sender}\n"
        f"- Original subject: {subject}\n"
        f"- Summary: {summary}\n"
        f"- Waiting for response for: {days_waiting} days\n\n"
        f"Requirements:\n"
        f"- Politely follow up on the unanswered email\n"
        f"- Reference the original topic briefly\n"
        f"- Keep it under 100 words\n"
        f"- Do NOT include a sign-off line\n"
        f'- Return JSON: {{"subject": "<subject>", "draft": "<body>"}}'
    )

    logger.info(
        "[ReplyAgent] language=%s tone=%s subject=%s",
        language_full,
        tone,
        subject,
    )

    now = datetime.now(ICT).isoformat()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are an email assistant.\n"
                        f"You MUST write the email entirely in {language_full}.\n"
                        f"If the language is Vietnamese, every sentence must be Vietnamese.\n"
                        f"If the language is English, every sentence must be English.\n"
                        f"Never switch languages.\n"
                        f"Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.4,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        logger.info(
            "[ReplyAgent] generated draft language=%s",
            language_full,
        )
        return {
            "subject": data.get("subject", f"Re: {subject}"),
            "draft": data.get("draft", ""),
            "generated_at": now,
        }
    except Exception as exc:
        logger.error("[ReplyAgent] followup_draft_skill failed: %s", exc)
        if language_full == "Vietnamese":
            fallback_draft = (
                f"{opener},\n\n"
                f"Tôi muốn theo dõi thêm về nội dung: {summary}. "
                f"Anh/Chị có thể cập nhật tình hình giúp tôi được không?"
            )
        elif language_full == "English":
            fallback_draft = (
                f"{opener},\n\n"
                f"I wanted to follow up regarding: {summary}. "
                f"Could you please provide an update?"
            )
        else:
            fallback_draft = (
                f"{opener},\n\n"
                f"I wanted to follow up regarding: {summary}. "
                f"Could you please provide an update?"
            )
        return {
            "subject": f"Follow-up: {subject}",
            "draft": fallback_draft,
            "generated_at": now,
        }
