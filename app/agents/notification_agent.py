import base64
import logging
from datetime import datetime
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.auth import get_gmail_service

logger = logging.getLogger(__name__)
_get_gmail_service = get_gmail_service  # alias for test patching

SENDER_NAME = "Email Scheduler AI"


# ── Helpers
def _decode_subject(subject: str) -> str:
    """Decode subject dạng =?UTF-8?B?...?= về chuỗi thường."""
    if subject is None:
        return "None"
    try:
        parts = decode_header(subject)
        decoded = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                decoded += part.decode(enc or "utf-8", errors="ignore")
            else:
                decoded += part
        return decoded.strip()
    except Exception:
        return subject


def _format_datetime(iso_str: str) -> str:
    """Chuyển ISO 8601 → chuỗi tiếng Việt dễ đọc."""
    try:
        # Handle various ISO formats (with/without timezone)
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
                    "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
        wd = weekdays[dt.weekday()]
        # Build string manually instead of using strftime with Unicode
        return f"{wd}, {dt.day:02d}/{dt.month:02d}/{dt.year} lúc {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return iso_str


# ── Email builders
def _build_success_email(to, subject, calendar_result, email_result) -> MIMEMultipart:
    # Dùng time từ email_result (đã parse đúng từ GPT-4o)
    time_str = _format_datetime(email_result.get("time", ""))
    location = calendar_result.get("location") or "Chưa xác định"
    event_link = calendar_result.get("event_link", "")
    summary = email_result.get("summary", "")
    clean_subj = _decode_subject(subject)

    body = f"""Xin chào,

Hệ thống đã nhận và xử lý yêu cầu đặt lịch của bạn.

━━━━━━━━━━━━━━━━━━━━━━━━
📅 THÔNG TIN LỊCH HỌP
━━━━━━━━━━━━━━━━━━━━━━━━
📌 Nội dung  : {summary}
🕐 Thời gian : {time_str}
📍 Địa điểm  : {location}
⏱  Thời lượng: 60 phút

🔗 Xem trên Google Calendar:
{event_link}
━━━━━━━━━━━━━━━━━━━━━━━━

Lịch họp đã được thêm vào Google Calendar tự động.
Nếu cần thay đổi, vui lòng reply email này.

Trân trọng,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Da xac nhan lich hop"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_conflict_email(to, subject, calendar_result, email_result, conflict_result) -> MIMEMultipart:
    req_time = _format_datetime(email_result.get("time", ""))
    summary = email_result.get("summary", "")
    clean_subj = _decode_subject(subject)

    suggestions = conflict_result.get("suggestions", [])
    suggestion_text = ""
    for i, s in enumerate(suggestions, 1):
        suggestion_text += f"  {i}. {s['label']}\n"

    if not suggestion_text:
        suggestion_text = "  Khong tim duoc khung gio trong trong 7 ngay toi.\n"

    body = f"""Xin chào,

Hệ thống đã nhận yêu cầu đặt lịch của bạn, tuy nhiên có xung đột lịch.

━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  XUNG ĐỘT LỊCH HỌP
━━━━━━━━━━━━━━━━━━━━━━━━
📌 Nội dung    : {summary}
🕐 Giờ yêu cầu: {req_time}
❌ Khung giờ này đã có lịch khác.

💡 CÁC KHUNG GIỜ TRỐNG GỢI Ý:
{suggestion_text}
━━━━━━━━━━━━━━━━━━━━━━━━

Vui lòng reply email này với khung giờ bạn muốn đặt,
hệ thống sẽ tự động xử lý.

Trân trọng,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Xung dot lich, co goi y gio thay the"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_cancel_email(to, subject, calendar_result, email_result) -> MIMEMultipart:
    event_title = calendar_result.get("event_title", "Cuộc họp")
    time_str = _format_datetime(email_result.get("time", ""))
    clean_subj = _decode_subject(subject)

    body = f"""Xin chào,

Hệ thống đã xử lý yêu cầu huỷ lịch của bạn.

━━━━━━━━━━━━━━━━━━━━━━━━
❌ XÁC NHẬN HUỶ LỊCH HỌP
━━━━━━━━━━━━━━━━━━━━━━━━
📌 Lịch đã huỷ : {event_title}
🕐 Thời gian   : {time_str}
✅ Trạng thái  : Đã xoá khỏi Google Calendar

Nếu muốn đặt lịch mới, vui lòng gửi email với thời gian mong muốn.

Trân trọng,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Da huy lich hop"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_cancel_not_found_email(to, subject, calendar_result, email_result) -> MIMEMultipart:
    time_str = _format_datetime(email_result.get("time", ""))
    clean_subj = _decode_subject(subject)

    body = f"""Xin chào,

Hệ thống đã nhận yêu cầu huỷ lịch của bạn nhưng không tìm thấy lịch phù hợp.

━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  KHÔNG TÌM THẤY LỊCH
━━━━━━━━━━━━━━━━━━━━━━━━
🕐 Thời gian yêu cầu: {time_str}
❌ Không có lịch họp nào vào khung giờ này.

Vui lòng kiểm tra lại thời gian hoặc liên hệ trực tiếp.

Trân trọng,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Khong tim thay lich hop"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_reschedule_email(to, subject, calendar_result, email_result) -> MIMEMultipart:
    event_title = calendar_result.get('event_title', 'Cuoc hop')
    old_time = _format_datetime(calendar_result.get('old_start', ''))
    new_time = _format_datetime(calendar_result.get('new_start', ''))
    event_link = calendar_result.get('event_link', '')
    clean_subj = _decode_subject(subject)

    body = f"""Xin chao,

He thong da xu ly yeu cau doi lich cua ban.

━━━━━━━━━━━━━━━━━━━━━━━━
🔄 XAC NHAN DOI LICH HOP
━━━━━━━━━━━━━━━━━━━━━━━━
📌 Lich hop  : {event_title}
🕐 Gio cu    : {old_time}
🕐 Gio moi   : {new_time}
✅ Trang thai: Da cap nhat tren Google Calendar

🔗 Xem tren Google Calendar:
{event_link}
━━━━━━━━━━━━━━━━━━━━━━━━

Lich hop da duoc cap nhat tu dong.
Neu can thay doi, vui long reply email nay.

Tran trong,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Da doi lich hop thanh cong"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_reschedule_not_found_email(to, subject, calendar_result, email_result) -> MIMEMultipart:
    old_time = _format_datetime(email_result.get("old_time", ""))
    clean_subj = _decode_subject(subject)

    body = f"""Xin chao,

He thong da nhan yeu cau doi lich nhung khong tim thay lich phu hop.

━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  KHONG TIM THAY LICH
━━━━━━━━━━━━━━━━━━━━━━━━
🕐 Gio cu yeu cau: {old_time}
❌ Khong co lich hop nao vao khung gio nay.

Vui long kiem tra lai thoi gian hoac lien he truc tiep.

Tran trong,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Khong tim thay lich can doi"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _build_error_email(to, subject, error_msg) -> MIMEMultipart:
    clean_subj = _decode_subject(subject)
    body = f"""Xin chào,

Hệ thống đã nhận email của bạn nhưng không thể xử lý tự động.

Lý do: {error_msg}

Vui lòng liên hệ trực tiếp hoặc thử lại với thông tin rõ hơn
(thời gian, địa điểm cụ thể).

Trân trọng,
{SENDER_NAME} 🤖
"""
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = f"Re: {clean_subj} — Khong xu ly duoc yeu cau"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


# ── Send ──────────────────────────────────────────────────────────────────────
def _send(service, msg: MIMEMultipart) -> bool:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return bool(result.get("id"))


# ── Public API ────────────────────────────────────────────────────────────────
def send_reply(to_email: str, subject: str, body_text: str) -> dict:
    """
    Gửi một email trả lời đơn giản (plain text) đến địa chỉ cho trước.

    Args:
        to_email   : địa chỉ email người nhận
        subject    : tiêu đề email
        body_text  : nội dung email (plain text)

    Returns:
        dict: {"status": "sent"} | {"status": "error", "message": str}
    """
    logger.info(
        "[NotificationAgent] Gửi reply → %s | subject=%s", to_email, subject)

    try:
        service = _get_gmail_service()

        msg = MIMEText(body_text, "plain", "utf-8")
        msg["To"] = to_email
        msg["Subject"] = subject

        ok = _send(service, msg)
        if ok:
            logger.info("[NotificationAgent] ✓ Đã gửi reply → %s", to_email)
            return {"status": "sent", "to": to_email}
        else:
            return {"status": "error", "message": "Gửi không thành công"}

    except Exception as e:
        logger.exception("[NotificationAgent] Lỗi send_reply: %s", e)
        return {"status": "error", "message": str(e)}


def send_notification(
    email_obj,
    email_result: dict,
    calendar_result: dict,
    conflict_result: dict = None,
) -> dict:
    """
    Gửi email thông báo cho người gửi dựa trên kết quả pipeline.

    Args:
        email_obj       : EmailSchema gốc (.sender, .subject)
        email_result    : kết quả từ email_agent
        calendar_result : kết quả từ calendar_agent
        conflict_result : kết quả từ conflict_agent (nếu có conflict)

    Returns:
        dict: status "sent" | "error"
    """
    to = getattr(email_obj, "sender",  "")
    subject = getattr(email_obj, "subject", "")
    status = calendar_result.get("status", "error")

    logger.info(
        "[NotificationAgent] Gửi thông báo → %s | status=%s", to, status)

    try:
        service = _get_gmail_service()

        if status == "created":
            msg = _build_success_email(
                to, subject, calendar_result, email_result)
        elif status == "rescheduled":
            msg = _build_reschedule_email(
                to, subject, calendar_result, email_result)
        elif status == "cancelled":
            msg = _build_cancel_email(
                to, subject, calendar_result, email_result)
        elif status == "not_found":
            msg = _build_cancel_not_found_email(
                to, subject, calendar_result, email_result)
        elif status == "conflict":
            msg = _build_conflict_email(
                to, subject, calendar_result, email_result,
                conflict_result or {"suggestions": []},
            )
        else:
            msg = _build_error_email(
                to, subject, calendar_result.get(
                    "message", "Lỗi không xác định")
            )

        ok = _send(service, msg)
        if ok:
            logger.info("[NotificationAgent] ✓ Đã gửi email → %s", to)
            return {"status": "sent", "to": to}
        else:
            return {"status": "error", "message": "Gửi không thành công"}

    except Exception as e:
        logger.exception("[NotificationAgent] Lỗi: %s", e)
        return {"status": "error", "message": str(e)}
