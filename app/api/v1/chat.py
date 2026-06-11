import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.agents.chat_agent import chat
from app.core.auth import get_calendar_service, get_gmail_service
from app.core.config import settings
from app.core.jwt_auth import get_current_user
from app.core.logger import log_event
from app.db.sqlite import get_connection

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role:    str
    content: str


class ChatRequest(BaseModel):
    messages:   list[ChatMessage]
    session_id: str = ""
    emails:     list[dict] | None = None


class ChatResponse(BaseModel):
    reply:      str
    action:     dict | None = None
    session_id: str = ""


class SendEmailRequest(BaseModel):
    to:      str
    subject: str
    body:   str


# ── Helpers
def _fmt_time(time_str: str) -> str:
    try:
        # Xử lý chuỗi có timezone
        clean = time_str.replace("Z", "").split("+")[0]
        dt = datetime.fromisoformat(clean)
        weekdays = ["Thu Hai", "Thu Ba", "Thu Tu",
                    "Thu Nam", "Thu Sau", "Thu Bay", "Chu Nhat"]
        return dt.strftime(f"{weekdays[dt.weekday()]}, %d/%m/%Y luc %H:%M")
    except Exception:
        return time_str


def _send_email(to: str, subject: str, body: str):
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    service = get_gmail_service()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _save_pending(token: str, action: dict):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_invites (
            token TEXT PRIMARY KEY, action TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("INSERT INTO pending_invites (token, action) VALUES (?, ?)",
                (token, json.dumps(action, ensure_ascii=False)))
    conn.commit()
    conn.close()


# ── Email builders
def _save_pending_reschedule(token: str, action: dict):
    """Lưu yêu cầu dời lịch chờ xác nhận."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_reschedules (
            token      TEXT PRIMARY KEY,
            action     TEXT,
            status     TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("INSERT INTO pending_reschedules (token, action) VALUES (?, ?)",
                (token, json.dumps(action, ensure_ascii=False)))
    conn.commit()
    conn.close()


def _send_reschedule_invite_email(action: dict, confirm_token: str):
    """Gửi email xin phép dời lịch cho người được mời."""
    attendees = action.get("attendees", [])
    event_title = action.get("event_title", "Cuoc hop")
    old_time = _fmt_time(action.get("old_time", ""))
    new_time = _fmt_time(action.get("time", ""))
    confirm_url = f"{settings.BASE_URL}/chat/reschedule/confirm/{confirm_token}"
    decline_url = f"{settings.BASE_URL}/chat/reschedule/decline/{confirm_token}"

    body = f"""Xin chao,

Nguoi to chuc muon doi lich cuoc hop sau:

━━━━━━━━━━━━━━━━━━━━━━━━
YEU CAU DOI LICH HOP
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung  : {event_title}
Gio cu    : {old_time}
Gio moi   : {new_time}
━━━━━━━━━━━━━━━━━━━━━━━━

Ban co dong y voi viec doi lich nay khong?

Dong y doi lich : {confirm_url}
Giu nguyen gio cu: {decline_url}

Tran trong,
Email Scheduler AI
"""
    subject = f"Xin phep doi lich: {event_title}"
    for att in attendees:
        if "@" in str(att):
            try:
                _send_email(att, subject, body)
                logger.info("[ChatAPI] Gui email xin phep doi lich → %s", att)
            except Exception as e:
                logger.error(
                    "[ChatAPI] Loi gui email doi lich cho %s: %s", att, e)


def _send_invite_email(action: dict, confirm_token: str):
    to = action["invitee_email"]
    invitee_name = action.get("invitee_name", "Anh/Chi")
    summary = action.get("summary", "cuoc hop")
    time_fmt = _fmt_time(action.get("time", ""))
    location = action.get("location") or "Chua xac dinh"
    confirm_url = f"{settings.BASE_URL}/chat/confirm/{confirm_token}"
    decline_url = f"{settings.BASE_URL}/chat/decline/{confirm_token}"

    body = f"""Xin chao {invitee_name},

Ban duoc moi tham gia cuoc hop sau:

━━━━━━━━━━━━━━━━━━━━━━━━
THU MOI HOP
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung : {summary}
Thoi gian: {time_fmt}
Dia diem : {location}
━━━━━━━━━━━━━━━━━━━━━━━━

Dong y tham du : {confirm_url}
Tu choi        : {decline_url}

Hoac reply email nay voi "Dong y" hoac "Tu choi".

Tran trong,
Email Scheduler AI
"""
    _send_email(to, f"Thu moi hop: {summary}", body)
    logger.info("[ChatAPI] Gui email moi → %s", to)


def _send_reschedule_notification(cal_result: dict):
    """Gửi email thông báo dời lịch cho attendees."""
    event_title = cal_result.get("event_title", "Cuoc hop")
    old_time = _fmt_time(cal_result.get("old_start", ""))
    new_time = _fmt_time(cal_result.get("new_start", ""))
    event_link = cal_result.get("event_link", "")
    attendees = cal_result.get("attendees", [])

    body = f"""Xin chao,

Lich hop sau da duoc doi sang thoi gian moi:

━━━━━━━━━━━━━━━━━━━━━━━━
THONG BAO DOI LICH HOP
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung  : {event_title}
Gio cu    : {old_time}
Gio moi   : {new_time}
Trang thai: Da cap nhat tren Google Calendar
━━━━━━━━━━━━━━━━━━━━━━━━

Xem lich moi: {event_link}

Tran trong,
Email Scheduler AI
"""
    subject = f"Thong bao doi lich: {event_title}"
    for att in attendees:
        if "@" in str(att):
            try:
                _send_email(att, subject, body)
                logger.info("[ChatAPI] Da gui thong bao doi lich cho: %s", att)
            except Exception as e:
                logger.error(
                    "[ChatAPI] Loi gui thong bao doi lich cho %s: %s", att, e)


def _notify_organizer(action: dict, status: str, event_link: str = ""):
    invitee = action.get("invitee_email", "")
    summary = action.get("summary", "cuoc hop")
    time_fmt = _fmt_time(action.get("time", ""))

    if status == "confirmed":
        subject = f"Lich hop da duoc xac nhan: {summary}"
        body = f"""Xin chao,

Cuoc hop cua ban da nhan duoc phan hoi tich cuc.

━━━━━━━━━━━━━━━━━━━━━━━━
LICH HOP DA DUOC XAC NHAN
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung     : {summary}
Thoi gian    : {time_fmt}
Nguoi tham du: {invitee}
Trang thai   : Da tao tren Google Calendar

Xem lich: {event_link}
━━━━━━━━━━━━━━━━━━━━━━━━

Chuc ban co buoi hop hieu qua!

Tran trong,
Email Scheduler AI
"""
    else:
        subject = f"Loi moi bi tu choi: {summary}"
        body = f"""Xin chao,

Rat tiec, loi moi tham du cuoc hop da bi tu choi.

━━━━━━━━━━━━━━━━━━━━━━━━
THONG TIN CUOC HOP
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung     : {summary}
Thoi gian    : {time_fmt}
Nguoi tu choi: {invitee}
Trang thai   : Khong duoc xac nhan
━━━━━━━━━━━━━━━━━━━━━━━━

Goi y:
- Dat lai lich vao thoi gian khac
- Lien he truc tiep voi {invitee}
- Dung chat bot de gui loi moi moi

Tran trong,
Email Scheduler AI
"""
    _send_email(settings.ORGANIZER_EMAIL, subject, body)
    logger.info("[ChatAPI] Thong bao organizer | status=%s", status)


def _create_calendar_event(action: dict):
    from app.agents.calendar_agent import _create_event, DEFAULT_DURATION
    service = get_calendar_service()
    time_str = action.get("time", "")
    start_dt = datetime.fromisoformat(time_str)
    end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION)
    event = _create_event(
        service=service, summary=action.get("summary", "Cuoc hop"),
        start_dt=start_dt, end_dt=end_dt,
        location=action.get("location"),
        attendees=[action.get("invitee_email", "")],
        description="Tao tu Email Scheduler AI Chat",
    )
    return event.get("htmlLink", "")


def _build_email_summary_prompt(emails: list[dict]) -> str:
    """
    Build a structured email summary prompt from raw email data.
    Groups emails by category and formats them for the AI.
    """
    if not emails:
        return "Không có email mới nào."

    count = len(emails)

    # Group by category
    categories = {
        "important": [],
        "need_action": [],
        "informational": [],
        "other": [],
    }

    category_map = {
        "meeting": "important",
        "report": "need_action",
        "partnership": "important",
        "support": "need_action",
        "announcement": "informational",
    }

    for email in emails:
        cat = email.get("category", "other")
        mapped = category_map.get(cat, "other")
        if mapped in categories:
            categories[mapped].append(email)
        else:
            categories["other"].append(email)

    parts = []
    parts.append(f"📬 Bạn có {count} email mới cần chú ý.\n")

    # Important (red)
    if categories["important"]:
        parts.append("🔴 Quan trọng")
        for email in categories["important"]:
            sender = email.get("sender", "Không rõ")
            subject = email.get("subject", "Không có tiêu đề")
            summary = email.get("summary", "")
            parts.append(f"\n• {sender}")
            parts.append(f"  * {subject}")
            if summary:
                parts.append(f"  * {summary}")
        parts.append("")

    # Need action (yellow)
    if categories["need_action"]:
        parts.append("🟡 Cần xử lý")
        for email in categories["need_action"]:
            sender = email.get("sender", "Không rõ")
            subject = email.get("subject", "Không có tiêu đề")
            summary = email.get("summary", "")
            parts.append(f"\n• {sender}")
            parts.append(f"  * {subject}")
            if summary:
                parts.append(f"  * {summary}")
        parts.append("")

    # Informational (green)
    if categories["informational"]:
        parts.append("🟢 Thông tin")
        for email in categories["informational"]:
            sender = email.get("sender", "Không rõ")
            subject = email.get("subject", "Không có tiêu đề")
            parts.append(f"\n• {sender}")
            parts.append(f"  * {subject}")
        parts.append("")

    # Other
    if categories["other"]:
        parts.append("⚪ Khác")
        for email in categories["other"]:
            sender = email.get("sender", "Không rõ")
            subject = email.get("subject", "Không có tiêu đề")
            parts.append(f"\n• {sender}")
            parts.append(f"  * {subject}")
        parts.append("")

    # Action suggestions
    if categories["important"] or categories["need_action"]:
        parts.append("💡 Gợi ý hành động:")
        idx = 1
        for email in categories["important"]:
            parts.append(
                f"{idx}. Xem và phản hồi email từ {email.get('sender', '')}")
            idx += 1
        for email in categories["need_action"]:
            parts.append(f"{idx}. Xử lý email từ {email.get('sender', '')}")
            idx += 1

    return "\n".join(parts)


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    messages = [m.dict() for m in req.messages]

    # If emails provided (from Quick Action card), generate summary directly
    if req.emails:
        reply = _build_email_summary_prompt(req.emails)
        session_id = req.session_id or str(uuid.uuid4())
        log_event(agent="chat", status="ok", payload={
            "reply": reply[:200], "action": None, "user_id": current_user["id"],
            "email_summary": len(req.emails)
        })
        return ChatResponse(reply=reply, action=None, session_id=session_id)

    result = chat(messages)
    reply = result["reply"]
    action = result["action"]
    session_id = req.session_id or str(uuid.uuid4())

    # ── Đặt lịch ─────────────────────────────────────────────────────────────
    if action and action.get("type") == "schedule":
        try:
            token = str(uuid.uuid4())
            _save_pending(token, action)
            _send_invite_email(action, token)
            reply += f"\n\n📧 Đã gửi email mời đến **{action['invitee_email']}**. Đang chờ xác nhận..."
        except Exception as e:
            logger.error("[ChatAPI] Loi gui email moi: %s", e)
            reply += f"\n\n⚠️ Không thể gửi email mời: {e}"

    # ── Dời lịch ─────────────────────────────────────────────────────────────
    elif action and action.get("type") == "reschedule":
        try:
            from app.agents.calendar_agent import process_reschedule
            cal = process_reschedule(action)
            if cal.get("status") == "rescheduled":
                reply += "\n\n✅ Đã dời lịch thành công sang giờ mới!"
                logger.info("[ChatAPI] Doi lich thanh cong")
                # Gửi email thông báo cho attendees
                try:
                    _send_reschedule_notification(cal)
                except Exception as ex:
                    logger.error(
                        "[ChatAPI] Loi gui thong bao doi lich: %s", ex)
            elif cal.get("status") == "conflict":
                reply += "\n\n⚠️ Giờ mới bị trùng lịch! Bạn muốn chọn giờ khác không?"
            elif cal.get("status") == "not_found":
                reply += "\n\n⚠️ Không tìm thấy lịch họp vào giờ cũ đó."
            else:
                reply += f"\n\n❌ Lỗi: {cal.get('message','')}"
            action = None
        except Exception as e:
            logger.error("[ChatAPI] Loi doi lich: %s", e)
            reply += f"\n\n⚠️ Lỗi dời lịch: {e}"

    # ── Gửi email ───────────────────────────────────────────────────────────
    elif action and action.get("type") == "send_email":
        # Build preview in reply – email will be sent after user confirmation
        preview = (
            f"📧 **Xem trước email:**\n\n"
            f"**Người nhận:** {action.get('to', '')}\n"
            f"**Tiêu đề:** {action.get('subject', '')}\n"
            f"**Nội dung:**\n{action.get('body', '')}"
        )
        reply = preview if not reply else reply
        logger.info(
            "[ChatAPI] send_email preview | to=%s | subject=%s",
            action.get("to"), action.get("subject"),
        )

    log_event(agent="chat", status="ok", payload={
              "reply": reply[:200], "action": action, "user_id": current_user["id"]})
    return ChatResponse(reply=reply, action=action, session_id=session_id)


@router.post("/chat/send-email")
async def send_email_endpoint(req: SendEmailRequest, current_user: dict = Depends(get_current_user)):
    """
    Send an email through Gmail API after user confirmation in chat.
    """
    send_time = datetime.now().isoformat()
    try:
        _send_email(req.to, req.subject, req.body)
        logger.info(
            "[ChatAPI] Email sent | to=%s | subject=%s | time=%s",
            req.to, req.subject, send_time,
        )
        log_event(agent="chat", status="sent", payload={
            "type": "send_email",
            "to": req.to,
            "subject": req.subject,
            "send_time": send_time,
            "user_id": current_user["id"],
        })
        return {"status": "ok", "message": f"✅ Đã gửi email cho {req.to}"}
    except Exception as e:
        logger.error("[ChatAPI] Loi gui email: %s", e)
        log_event(agent="chat", status="failed", payload={
            "type": "send_email",
            "to": req.to,
            "subject": req.subject,
            "send_time": send_time,
            "error": str(e),
            "user_id": current_user["id"],
        })
        return {"status": "error", "message": f"❌ Lỗi gửi email: {e}"}


@router.get("/chat/confirm/{token}")
async def confirm_invite(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT action, status FROM pending_invites WHERE token=?", (token,))
    row = cur.fetchone()

    if not row:
        return HTMLResponse("<h2>Link khong hop le hoac da het han.</h2>")
    if row[1] != "pending":
        return HTMLResponse(f"<h2>Loi moi nay da duoc xu ly ({row[1]}).</h2>")

    action = json.loads(row[0])
    try:
        event_link = _create_calendar_event(action)
        cur.execute(
            "UPDATE pending_invites SET status='confirmed' WHERE token=?", (token,))
        conn.commit()
        conn.close()
        try:
            _notify_organizer(action, "confirmed", event_link)
        except Exception as e:
            logger.error("[ChatAPI] Loi thong bao organizer: %s", e)
        invitee_email = action.get("invitee_email", "")
        summary = action.get("summary", "Cuoc hop")
        time_fmt = _fmt_time(action.get("time", ""))
        log_event(agent="chat", status="meeting_accepted", payload={
            "invitee_email": invitee_email,
            "summary": summary,
            "meeting_time": time_fmt,
            "organizer_email": settings.ORGANIZER_EMAIL,
        })
        logger.info("[ChatAPI] Xac nhan → tao lich: %s", event_link)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f0fdf4">
        <h1 style="color:#16a34a">Xac nhan thanh cong!</h1>
        <p>Lich hop da duoc tao tren Google Calendar.</p>
        <a href="{event_link}" target="_blank"
           style="display:inline-block;margin-top:20px;padding:12px 24px;
                  background:#16a34a;color:white;border-radius:8px;text-decoration:none">
           Xem lich tren Google Calendar
        </a></body></html>""")
    except Exception as e:
        conn.close()
        return HTMLResponse(f"<h2>Loi tao lich: {e}</h2>")


@router.get("/chat/decline/{token}")
async def decline_invite(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT action FROM pending_invites WHERE token=?", (token,))
    row = cur.fetchone()
    cur.execute(
        "UPDATE pending_invites SET status='declined' WHERE token=?", (token,))
    conn.commit()
    conn.close()
    if row:
        try:
            _notify_organizer(json.loads(row[0]), "declined")
        except Exception as e:
            logger.error("[ChatAPI] Loi thong bao organizer: %s", e)
    logger.info("[ChatAPI] Tu choi | token=%s", token)
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#fff7f7">
    <h1 style="color:#dc2626">Da tu choi loi moi</h1>
    <p>Cam on ban da phan hoi. Nguoi to chuc se duoc thong bao.</p>
    </body></html>""")


@router.get("/chat/reschedule/confirm/{token}")
async def confirm_reschedule(token: str):
    """Người được mời đồng ý dời lịch."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT action, status FROM pending_reschedules WHERE token=?", (token,))
    row = cur.fetchone()

    if not row:
        return HTMLResponse("<h2>Link khong hop le hoac da het han.</h2>")
    if row[1] != "pending":
        return HTMLResponse(f"<h2>Yeu cau nay da duoc xu ly ({row[1]}).</h2>")

    action = json.loads(row[0])
    try:
        from app.agents.calendar_agent import process_reschedule
        cal = process_reschedule(action)

        cur.execute(
            "UPDATE pending_reschedules SET status='confirmed' WHERE token=?", (token,))
        conn.commit()
        conn.close()

        if cal.get("status") == "rescheduled":
            event_link = cal.get("event_link", "")
            event_title = cal.get("event_title", "Cuoc hop")
            old_time = _fmt_time(cal.get("old_start", ""))
            new_time = _fmt_time(cal.get("new_start", ""))

            # Thông báo cho organizer
            try:
                body = f"""Tin vui!

Yeu cau doi lich cua ban da duoc chap nhan.

━━━━━━━━━━━━━━━━━━━━━━━━
LICH HOP DA DUOC DOI
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung : {event_title}
Gio cu   : {old_time}
Gio moi  : {new_time}
Trang thai: Da cap nhat tren Google Calendar

Xem lich: {event_link}
━━━━━━━━━━━━━━━━━━━━━━━━

Tran trong,
Email Scheduler AI
"""
                _send_email(settings.ORGANIZER_EMAIL,
                            f"Doi lich duoc chap nhan: {event_title}", body)
                logger.info("[ChatAPI] Da thong bao doi lich cho organizer")
            except Exception as e:
                logger.error("[ChatAPI] Loi thong bao organizer: %s", e)

            return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f0fdf4">
            <h1 style="color:#16a34a">Da dong y doi lich!</h1>
            <p>Lich hop da duoc cap nhat tren Google Calendar.</p>
            <p><b>Gio moi:</b> {new_time}</p>
            <a href="{event_link}" target="_blank"
               style="display:inline-block;margin-top:20px;padding:12px 24px;
                      background:#16a34a;color:white;border-radius:8px;text-decoration:none">
               Xem lich moi
            </a></body></html>""")
        else:
            return HTMLResponse(f"<h2>Loi cap nhat lich: {cal.get('message','')}</h2>")

    except Exception as e:
        conn.close()
        return HTMLResponse(f"<h2>Loi: {e}</h2>")


@router.get("/chat/reschedule/decline/{token}")
async def decline_reschedule(token: str):
    """Người được mời từ chối dời lịch."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT action FROM pending_reschedules WHERE token=?", (token,))
    row = cur.fetchone()
    cur.execute(
        "UPDATE pending_reschedules SET status='declined' WHERE token=?", (token,))
    conn.commit()
    conn.close()

    if row:
        try:
            action = json.loads(row[0])
            event_title = action.get("event_title", "Cuoc hop")
            old_time = _fmt_time(action.get("old_time", ""))
            body = f"""Thong bao:

Yeu cau doi lich bi tu choi. Lich hop van giu nguyen.

━━━━━━━━━━━━━━━━━━━━━━━━
THONG TIN
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung   : {event_title}
Gio giu lai: {old_time}
Trang thai : Khong doi - giu nguyen lich cu
━━━━━━━━━━━━━━━━━━━━━━━━

Tran trong,
Email Scheduler AI
"""
            _send_email(settings.ORGANIZER_EMAIL,
                        f"Tu choi doi lich: {event_title}", body)
        except Exception as e:
            logger.error("[ChatAPI] Loi thong bao tu choi doi lich: %s", e)

    logger.info("[ChatAPI] Tu choi doi lich | token=%s", token)
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#fff7f7">
    <h1 style="color:#dc2626">Da tu choi doi lich</h1>
    <p>Lich hop van giu nguyen thoi gian cu. Nguoi to chuc se duoc thong bao.</p>
    </body></html>""")


# ── Dashboard endpoints ────────────────────────────────────────────────────────


@router.get("/dashboard/stats")
async def dashboard_stats(current_user: dict = Depends(get_current_user)):
    """
    Return dashboard statistics:
      - Upcoming events from Google Calendar
      - Log statistics from system_logs table
    """
    from app.db.sqlite import get_log_stats

    # 1. Fetch upcoming events from Google Calendar
    from app.agents.chat_agent import _fetch_upcoming_events
    upcoming = _fetch_upcoming_events(range_days=7)

    # 2. Fetch log stats
    stats = get_log_stats()

    return {
        "upcoming_events": upcoming,
        "stats": stats,
    }


@router.get("/dashboard/logs")
async def dashboard_logs(
    current_user: dict = Depends(get_current_user),
    agent: str = None,
    status: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 20,
    date_from: str = None,
    date_to: str = None,
):
    """
    Return paginated, filtered event logs from system_logs table.
    """
    from app.db.sqlite import get_logs

    result = get_logs(
        agent=agent,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
    )
    return result


# ── Email Intelligence Dashboard ────────────────────────────────────────────────


@router.get("/dashboard/email-stats")
async def email_stats(
    current_user: dict = Depends(get_current_user),
    track_view: bool = False,
):
    """
    Return email intelligence statistics since the user's last Dashboard visit.

    - If last_dashboard_view_at is NULL (first visit), count ALL emails.
    - Otherwise, only count emails with processed_at >= last_dashboard_view_at.
    - When track_view=True (initial manual Dashboard load), update
      last_dashboard_view_at to now() after returning stats.
    - When track_view=False (auto-refresh polling), do NOT update the timestamp
      so the "since last view" window stays anchored to the user's actual visit.
    """
    from app.db.sqlite import get_email_statistics_since, update_last_dashboard_view

    last_view = current_user.get("last_dashboard_view_at")
    stats = get_email_statistics_since(since=last_view)

    # Only update the "last viewed" timestamp on an explicit Dashboard visit,
    # NOT on automatic background refreshes.
    if track_view:
        user_id = current_user["id"]
        update_last_dashboard_view(user_id)

    return stats


@router.get("/dashboard/recent-emails")
async def recent_emails(
    current_user: dict = Depends(get_current_user),
):
    """
    Return emails received since the user's last Dashboard view.

    Read current_user["last_dashboard_view_at"]:
      - If NULL → return the latest 20 emails.
      - Otherwise → return emails with processed_at > that timestamp.

    This endpoint is READ-ONLY – it does NOT update last_dashboard_view_at.

    Returns:
        { "emails": [...], "count": N }
    """
    from app.db.sqlite import get_recent_emails_for_summary

    last_view = current_user.get("last_dashboard_view_at")
    emails = get_recent_emails_for_summary(since=last_view)
    return {"emails": emails, "count": len(emails)}


# ── Meeting Confirmation Notifications ─────────────────────────────────────────


@router.get("/dashboard/meeting-confirmations")
async def meeting_confirmations(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 10,
):
    """
    Return recent meeting_accepted notifications for the Dashboard notification card.

    Queries system_logs WHERE status='meeting_accepted', ordered by most recent first.
    Returns structured notification objects.
    """
    from app.db.sqlite import get_logs

    result = get_logs(
        agent="chat",
        status="meeting_accepted",
        page=page,
        page_size=page_size,
    )
    items = result.get("items", [])
    notifications = []
    for item in items:
        try:
            payload = json.loads(item["payload"]) if isinstance(
                item["payload"], str) else item["payload"]
        except (json.JSONDecodeError, TypeError):
            payload = {}
        notifications.append({
            "type": "meeting_accepted",
            "invitee_email": payload.get("invitee_email", ""),
            "summary": payload.get("summary", ""),
            "meeting_time": payload.get("meeting_time", ""),
            "organizer_email": payload.get("organizer_email", ""),
            "created_at": item["timestamp"],
            "event_id": item["event_id"],
        })
    return {
        "notifications": notifications,
        "total": result.get("total", 0),
        "page": page,
        "page_size": page_size,
    }
