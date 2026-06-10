"""
Daily Digest – scheduled background task that sends a morning summary.

The digest aggregates email intelligence data from the previous day,
counts emails by category, selects the top 3 most important ones,
and sends the digest via the existing Notification Agent to the
organiser's email address.

Architecture
------------
* Uses ``asyncio.create_task`` (same pattern as ``gmail_poller``).
* Scheduled via a simple time‑of‑day loop; no external scheduler needed.
* Re‑uses ``notification_agent.send_reply`` for delivery.
* Configurable via ``DAILY_DIGEST_HOUR`` in ``.env`` / ``settings``.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from app.agents.notification_agent import send_reply
from app.core.config import settings
from app.db.sqlite import get_email_statistics, get_top_important_emails

logger = logging.getLogger(__name__)

SENDER_NAME = "Email Scheduler AI"


def _build_digest_body(
    stats: dict,
    top_emails: list[dict],
    date_label: str,
) -> str:
    """Build a Vietnamese morning digest email body.

    Args:
        stats      : output of ``get_email_statistics()``
        top_emails : output of ``get_top_important_emails()``
        date_label : human‑readable date string for the digest

    Returns:
        plain‑text body string (UTF‑8)
    """
    lines = [
        f"Chào buổi sáng! ☀️",
        f"",
        f"Đây là bản tóm tắt email ngày {date_label}:",
        f"",
        f"Bạn đã nhận được:",
        f"  • {stats.get('report', 0)} Báo cáo",
        f"  • {stats.get('partnership', 0)} Đối tác",
        f"  • {stats.get('support', 0)} Hỗ trợ",
        f"  • {stats.get('announcement', 0)} Thông báo",
        f"  • {stats.get('meeting', 0)} Cuộc họp",
        f"  • {stats.get('other', 0)} Khác",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 TOP EMAIL QUAN TRỌNG",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not top_emails:
        lines.append("  (Không có email nào được phân tích hôm qua.)")
    else:
        for i, email in enumerate(top_emails, 1):
            sender = email.get("sender", "???")
            subject = email.get("subject", "(không tiêu đề)")
            score = email.get("importance_score", 0)
            summary = email.get("summary", "")
            category = email.get("category", "other")

            lines.append(f"  {i}. [{category.upper()}] {subject}")
            lines.append(f"     Từ: {sender}  |  Độ quan trọng: {score}/100")
            if summary:
                lines.append(f"     Tóm tắt: {summary}")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"Trân trọng,")
    lines.append(f"{SENDER_NAME} 🤖")

    return "\n".join(lines)


async def run_daily_digest():
    """
    Background task that waits until ``DAILY_DIGEST_HOUR`` each day,
    then builds and sends the digest email to ``ORGANIZER_EMAIL``.
    """
    digest_hour = settings.DAILY_DIGEST_HOUR

    logger.info(
        "[DailyDigest] Khởi động, gửi digest lúc %02d:00 mỗi ngày.", digest_hour)

    while True:
        try:
            # ── Compute seconds until next scheduled hour ─────────────────
            now = datetime.now()
            target = now.replace(
                hour=digest_hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()

            logger.info(
                "[DailyDigest] Đợi %.0f giây đến %s.",
                wait_seconds,
                target.isoformat(),
            )
            await asyncio.sleep(wait_seconds)

            # ── Aggregate yesterday's data ────────────────────────────────
            yesterday = (datetime.now() - timedelta(days=1)
                         ).strftime("%Y-%m-%d")
            yesterday_start = f"{yesterday}T00:00:00"
            yesterday_end = f"{yesterday}T23:59:59"
            date_label = datetime.now().strftime("%d/%m/%Y")

            stats = get_email_statistics()
            top_emails = get_top_important_emails(
                yesterday_start, top_n=3)

            if stats.get("total", 0) == 0:
                logger.info(
                    "[DailyDigest] Không có email nào hôm qua, bỏ qua digest.")
                continue

            body = _build_digest_body(stats, top_emails, date_label)

            # ── Send digest via Notification Agent ────────────────────────
            result = send_reply(
                to_email=settings.ORGANIZER_EMAIL,
                subject=f"📊 Daily Digest – {date_label}",
                body_text=body,
            )

            if result.get("status") == "sent":
                logger.info("[DailyDigest] ✓ Đã gửi digest đến %s",
                            settings.ORGANIZER_EMAIL)
            else:
                logger.error("[DailyDigest] ✗ Gửi thất bại: %s",
                             result.get("message", "unknown"))

        except Exception as e:
            logger.exception("[DailyDigest] Lỗi: %s", e)
            # Wait a bit before retrying to avoid tight loop on persistent errors
            await asyncio.sleep(60)
