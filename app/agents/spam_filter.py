import logging
import re

logger = logging.getLogger(__name__)

# ── Danh sách dấu hiệu spam ───────────────────────────────────────────────────
SPAM_SENDER_KEYWORDS = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "newsletter", "mailer", "notification", "automated",
    "support@", "info@", "hello@", "team@", "news@",
    "marketing@", "promo@", "deals@", "offer@",
    "unsubscribe", "mailchimp", "sendgrid", "mailgun",
]

SPAM_SUBJECT_KEYWORDS = [
    "unsubscribe", "newsletter", "promotion", "offer",
    "discount", "sale", "% off", "free shipping",
    "click here", "limited time", "act now", "winner",
    "congratulations", "you have been selected",
    "invoice", "receipt", "order confirmation",
    "verify your email", "confirm your account",
    "password reset", "security alert",
    "khuyến mãi", "giảm giá", "miễn phí", "quà tặng",
    "trúng thưởng", "đăng ký ngay", "ưu đãi",
]

SPAM_BODY_KEYWORDS = [
    "click here to unsubscribe",
    "unsubscribe",
    "you received this email because",
    "to stop receiving these emails",
    "this is an automated message",
    "do not reply to this email",
    "©", "all rights reserved",
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    return text.lower().strip()


def _check_sender(sender: str) -> tuple[bool, str]:
    s = _normalize(sender)
    for kw in SPAM_SENDER_KEYWORDS:
        if kw in s:
            return True, f"sender contains '{kw}'"
    return False, ""


def _check_subject(subject: str) -> tuple[bool, str]:
    s = _normalize(subject)
    for kw in SPAM_SUBJECT_KEYWORDS:
        if kw in s:
            return True, f"subject contains '{kw}'"
    return False, ""


def _check_body(body: str) -> tuple[bool, str]:
    b = _normalize(body)
    for kw in SPAM_BODY_KEYWORDS:
        if kw in b:
            return True, f"body contains '{kw}'"
    return False, ""


# ── Public API ────────────────────────────────────────────────────────────────
def is_spam(email) -> tuple[bool, str]:
    """
    Kiểm tra email có phải spam/newsletter không.

    Args:
        email: object có .sender, .subject, .body

    Returns:
        (is_spam, reason) — True nếu là spam
    """
    sender = getattr(email, "sender",  "") or ""
    subject = getattr(email, "subject", "") or ""
    body = getattr(email, "body",    "") or ""

    # Check sender
    spam, reason = _check_sender(sender)
    if spam:
        logger.info(
            "[SpamFilter] ✗ Spam detected | reason=%s | from=%s", reason, sender)
        return True, reason

    # Check subject
    spam, reason = _check_subject(subject)
    if spam:
        logger.info(
            "[SpamFilter] ✗ Spam detected | reason=%s | subject=%s", reason, subject)
        return True, reason

    # Check body
    spam, reason = _check_body(body)
    if spam:
        logger.info("[SpamFilter] ✗ Spam detected | reason=%s", reason)
        return True, reason

    logger.debug("[SpamFilter] ✓ Not spam | from=%s", sender)
    return False, ""
