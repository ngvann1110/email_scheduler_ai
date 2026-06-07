import asyncio
import base64
import logging
from email import message_from_bytes

from app.agents.evaluation_agent import evaluate_and_retry
from app.agents.spam_filter import is_spam
from app.core.auth import get_gmail_service
from app.core.config import settings
from app.core.logger import log_event
from app.orchestrator.orchestrator import run_pipeline
from app.schemas.email import EmailSchema

logger = logging.getLogger(__name__)


# ── Parse
def _parse_message(service, msg_id: str) -> dict | None:
    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="raw").execute()
        raw = base64.urlsafe_b64decode(msg["raw"])
        mail = message_from_bytes(raw)

        body = ""
        if mail.is_multipart():
            for part in mail.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(
                        "utf-8", errors="ignore")
                    break
        else:
            body = mail.get_payload(decode=True).decode(
                "utf-8", errors="ignore")

        return {
            "sender":    mail.get("From", ""),
            "subject":   mail.get("Subject", "(no subject)"),
            "body":      body.strip(),
            "timestamp": mail.get("Date", ""),
        }
    except Exception as e:
        logger.error("[Poller] Không parse được email %s: %s", msg_id, e)
        return None


def _mark_as_read(service, msg_id: str):
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as e:
        logger.warning("[Poller] Không mark read được %s: %s", msg_id, e)


# ── Main loop ─────────────────────────────────────────────────────────────────
async def poll_gmail():
    logger.info("[Poller] Khởi động Gmail Poller (interval=%ds)",
                settings.GMAIL_POLL_INTERVAL_SECONDS)

    try:
        service = get_gmail_service()
    except Exception as e:
        logger.error("[Poller] Không kết nối được Gmail: %s", e)
        return

    while True:
        try:
            result = service.users().messages().list(
                userId="me",
                maxResults=10,
                q=f"newer_than:1d is:unread in:inbox -from:{settings.ORGANIZER_EMAIL}",
            ).execute()
            messages = result.get("messages", [])

            if messages:
                logger.info("[Poller] Tìm thấy %d email mới", len(messages))

            for msg in messages:
                msg_id = msg["id"]
                parsed = _parse_message(service, msg_id)
                if not parsed:
                    continue

                logger.info(
                    "[Poller] Email mới | from=%s | subject=%s",
                    parsed["sender"], parsed["subject"],
                )

                # Đánh dấu đã đọc trước
                _mark_as_read(service, msg_id)

                try:
                    email_obj = EmailSchema(**parsed)

                    # ── Spam filter ───────────────────────────────────────────
                    spam, reason = is_spam(email_obj)
                    if spam:
                        logger.info(
                            "[Poller] ✗ Bỏ qua spam | reason=%s", reason)
                        log_event(
                            agent="spam_filter",
                            status="spam",
                            payload={"msg_id": msg_id, "reason": reason},
                        )
                        continue

                    logger.info("[Poller] ✓ Không phải spam → vào pipeline")

                    # ── Pipeline + Evaluation ─────────────────────────────────
                    final_result = await evaluate_and_retry(
                        pipeline_fn=run_pipeline,
                        email=email_obj,
                    )

                    log_event(
                        agent="gmail_poller",
                        status="processed",
                        payload={
                            "msg_id": msg_id,
                            "flow":   final_result.get("type"),
                        },
                    )

                    logger.info(
                        "[Poller] ✓ Pipeline xong | flow=%s",
                        final_result.get("type"),
                    )

                except Exception as e:
                    logger.error("[Poller] Lỗi email %s: %s", msg_id, e)
                    log_event(
                        agent="gmail_poller",
                        status="error",
                        payload={"msg_id": msg_id, "error": str(e)},
                    )

        except Exception as e:
            logger.error("[Poller] Lỗi khi poll Gmail: %s", e)

        await asyncio.sleep(settings.GMAIL_POLL_INTERVAL_SECONDS)
