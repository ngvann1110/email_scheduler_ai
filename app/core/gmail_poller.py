import asyncio
import base64
import logging
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from app.agents.evaluation_agent import evaluate_and_retry
from app.agents.spam_filter import is_spam
from app.core.auth import get_gmail_service
from app.core.config import settings
from app.core.logger import log_event
from app.orchestrator.orchestrator import run_pipeline
from app.schemas.email import EmailSchema

logger = logging.getLogger(__name__)


def decode_mime_header(value: str) -> str:
    """
    Decode MIME encoded-word headers (RFC 2047) like:
        =?UTF-8?B?QsOBTyBDw4FPIFTJThkgxJDu?=
    back to human-readable Vietnamese text.

    Handles mixed encoded + plain text parts.
    Falls back to original value if decoding fails.
    """
    if not value:
        return value
    try:
        parts = decode_header(value)
        decoded_parts: list[str] = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded_parts.append(
                    part.decode(charset or "utf-8", errors="ignore")
                )
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts).strip()
    except Exception:
        return value


def _parse_date_header(mail) -> str:
    """
    Convert the email Date header (RFC 2822) to ISO 8601 string.

    Handles real-world Gmail headers like:
        Thu, 11 Jun 2026 00:24:18 +0700
        Mon, 01 Jan 2025 12:30:00 +0000

    Fallback behaviour:
        - Missing / empty header → current UTC time in ISO 8601
        - Unparseable header     → warning + current UTC time in ISO 8601
    """
    raw_date = mail.get("Date", "")
    if not raw_date:
        logger.warning("[Poller] Email thiếu Date header → dùng UTC hiện tại")
        return datetime.now(timezone.utc).isoformat()

    try:
        dt = parsedate_to_datetime(raw_date)
        return dt.isoformat()
    except (ValueError, TypeError) as e:
        logger.warning(
            "[Poller] Không parse được Date header '%s': %s → dùng UTC hiện tại",
            raw_date, e,
        )
        return datetime.now(timezone.utc).isoformat()


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

        raw_sender = mail.get("From", "")
        raw_subject = mail.get("Subject", "(no subject)")

        # decode MIME encoded-words (RFC 2047) → Vietnamese text
        decoded_sender = decode_mime_header(raw_sender)
        decoded_subject = decode_mime_header(raw_subject)

        # Extract bare email address from "Display Name <email>" format
        _name, addr = parseaddr(decoded_sender)
        sender_addr = addr or decoded_sender  # fallback to full string

        return {
            "sender":    sender_addr,
            "subject":   decoded_subject,
            "body":      body.strip(),
            "timestamp": _parse_date_header(mail),
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
