import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.agents.chat_agent import chat
from app.core.auth import get_calendar_service, get_gmail_service
from app.core.config import settings
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


class ChatResponse(BaseModel):
    reply:      str
    action:     dict | None = None
    session_id: str = ""


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


def _send_cancel_notification(cal_result: dict):
    """Gửi email thông báo huỷ lịch cho tất cả attendees trong event."""
    event_title = cal_result.get("event_title", "Cuoc hop")
    event_start = cal_result.get("event_start", "")
    time_fmt = _fmt_time(event_start)
    attendees = cal_result.get("attendees", [])

    body = f"""Xin chao,

Chung toi xin thong bao rang cuoc hop sau da bi huy:

━━━━━━━━━━━━━━━━━━━━━━━━
THONG BAO HUY LICH HOP
━━━━━━━━━━━━━━━━━━━━━━━━
Noi dung  : {event_title}
Thoi gian : {time_fmt}
Trang thai: Da huy va xoa khoi Google Calendar
━━━━━━━━━━━━━━━━━━━━━━━━

Xin loi vi su bat tien nay.
Neu co thac mac, vui long lien he lai voi nguoi to chuc.

Tran trong,
Email Scheduler AI
"""
    subject = f"Thong bao huy lich: {event_title}"
    for att in attendees:
        if "@" in str(att):
            try:
                _send_email(att, subject, body)
                logger.info("[ChatAPI] Da gui thong bao huy cho: %s", att)
            except Exception as e:
                logger.error(
                    "[ChatAPI] Loi gui thong bao huy cho %s: %s", att, e)


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


def _save_pending_cancel(token: str, action: dict):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_cancels (
            token TEXT PRIMARY KEY,
            action TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute(
        "INSERT INTO pending_cancels (token, action) VALUES (?, ?)",
        (token, json.dumps(action, ensure_ascii=False))
    )

    conn.commit()
    conn.close()


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    messages = [m.dict() for m in req.messages]
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

    # ── Huỷ lịch ─────────────────────────────────────────────────────────────
    elif action and action.get("type") == "cancel":
        try:
            from uuid import uuid4
            from app.agents.calendar_agent import find_event_for_cancel
            from app.agents.notification_agent import send_cancel_confirmation_email

            # ── Tìm event cần huỷ
            event = find_event_for_cancel(action)

            if not event:
                reply += "\n\n⚠️ Không tìm thấy lịch họp cần huỷ."
                action = None

            else:
                # ── Tạo token xác nhận huỷ
                cancel_token = str(uuid4())

                pending_cancel = {
                    "token": cancel_token,
                    "event_id": event.get("event_id"),
                    "summary": event.get("summary"),
                    "time": event.get("time"),
                    "attendees": event.get("attendees", []),
                }

                _save_pending_cancel(cancel_token, pending_cancel)

                # TODO:
                # lưu pending_cancel vào DB / SQLite / cache
                # save_pending_cancel(pending_cancel)

                # ── Gửi email xác nhận cho attendees
                try:
                    send_cancel_confirmation_email(
                        attendees=event.get("attendees", []),
                        organizer=event.get("organizer"),
                        summary=event.get("summary"),
                        time=event.get("time"),
                        location=event.get("location"),
                        token=cancel_token,
                    )

                    logger.info(
                        "[ChatAPI] Da gui email xac nhan huy lich | event=%s",
                        event.get("summary"),
                    )

                    reply += (
                        f"\n\n📩 Đã gửi email xác nhận huỷ lịch "
                        f"**{event.get('summary','')}** tới người tham gia."
                        "\nLịch sẽ chỉ bị huỷ khi họ xác nhận đồng ý."
                    )

                except Exception as ex:
                    logger.error(
                        "[ChatAPI] Loi gui email xac nhan huy: %s", ex
                    )

                    reply += (
                        "\n\n❌ Không thể gửi email xác nhận huỷ lịch."
                    )

                action = None

        except Exception as e:
            logger.error("[ChatAPI] Loi huy lich: %s", e)

            reply += f"\n\n⚠️ Lỗi xử lý yêu cầu huỷ lịch: {e}"

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

    log_event(agent="chat", status="ok", payload={
              "reply": reply[:200], "action": action})
    return ChatResponse(reply=reply, action=action, session_id=session_id)


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


@router.get("/chat/cancel/confirm/{token}")
async def confirm_cancel(token: str):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT action, status FROM pending_cancels WHERE token=?",
        (token,)
    )

    row = cur.fetchone()

    if not row:
        return HTMLResponse(
            "<h2>Link khong hop le hoac da het han.</h2>"
        )

    if row[1] != "pending":
        return HTMLResponse(
            f"<h2>Yeu cau nay da duoc xu ly ({row[1]}).</h2>"
        )

    action = json.loads(row[0])
    try:
        from app.agents.calendar_agent import process_cancel
        cal = process_cancel(action)

        cur.execute(
            "UPDATE pending_cancels SET status='confirmed' WHERE token=?", (token,))
        conn.commit()
        conn.close()

        if cal.get("status") == "cancelled":
            event_title = cal.get("event_title", "Cuoc hop")
            event_start = cal.get("event_start", "")
            time_fmt = _fmt_time(event_start)

            return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f0fdf4">
            <h1 style="color:#16a34a">Da huy lich thanh cong!</h1>
            <p>Lich hop <b>{event_title}</b> ({time_fmt}) da duoc xoa khoi Google Calendar.</p>
            </body></html>""")
        else:
            return HTMLResponse(f"<h2>Loi huy lich: {cal.get('message','')}</h2>")

    except Exception as e:
        conn.close()
        return HTMLResponse(f"<h2>Loi: {e}</h2>")


# ── Dashboard endpoints ────────────────────────────────────────────────────────


@router.get("/dashboard/stats")
async def dashboard_stats():
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
