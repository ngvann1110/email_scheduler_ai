"""
Dashboard API router.

All /dashboard/... endpoints live here, separated from chat/confirm/decline
concerns in api/v1/chat.py. URL paths are identical to what chat.py had
before the extraction so no clients need updating.
"""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.jwt_auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))


# ── Request body schemas ──────────────────────────────────────────────────────

class EditDraftBody(BaseModel):
    draft_response: str


class SuggestTimeBody(BaseModel):
    selected_slot: str  # ISO datetime string


class SendActionBody(BaseModel):
    draft_response: str | None = None  # if None, use stored draft


# ── Internal helpers ──────────────────────────────────────────────────────────

_LANG_NAME = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}


def _generate_email_draft(
    action_type: str,
    context: dict,
    detected_language: str | None = None,
) -> str:
    """Generate an email draft via OpenAI for the given action type."""
    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    lang_name = _LANG_NAME.get(detected_language or "", "English")

    sender       = context.get("sender", "")
    subject      = context.get("subject", "")
    summary      = context.get("summary", "")
    cal          = context.get("calendar_result") or {}
    selected_time = context.get("selected_time", "")

    def _fmt(iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso)
            days = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
            return f"{days[dt.weekday()]}, {dt.strftime('%d/%m/%Y lúc %H:%M')}"
        except Exception:
            return iso

    if action_type == "accept_meeting":
        meeting_time = _fmt(cal.get("start", ""))
        prompt = (
            f"Write a professional email in {lang_name} accepting a meeting request.\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n"
            f"Confirmed time: {meeting_time}\n\n"
            f"Requirements: thank the sender, confirm the meeting time, mention the event is now on the calendar. "
            f"Professional and friendly tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "reject_meeting":
        prompt = (
            f"Write a professional email in {lang_name} politely declining a meeting request.\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n\n"
            f"Requirements: apologise for being unavailable, suggest they reach out to reschedule. "
            f"Professional tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "suggest_new_time":
        prompt = (
            f"Write a professional email in {lang_name} proposing a new meeting time.\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n"
            f"Proposed new time: {_fmt(selected_time)}\n\n"
            f"Requirements: thank the sender, explain the original time doesn't work, "
            f"propose the new time and ask if it suits them. "
            f"Professional tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "accept_reschedule":
        new_time = _fmt(cal.get("start", ""))
        prompt = (
            f"Write a professional email in {lang_name} confirming acceptance of a reschedule request.\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n"
            f"New meeting time: {new_time}\n\n"
            f"Requirements: confirm agreement to the rescheduled time, mention calendar has been updated. "
            f"Professional tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "confirm_cancel":
        prompt = (
            f"Write a professional email in {lang_name} confirming that a meeting has been cancelled.\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n\n"
            f"Requirements: confirm the cancellation, apologise for any inconvenience, "
            f"leave the door open for rescheduling. "
            f"Professional tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "decline_cancel":
        prompt = (
            f"Write a professional email in {lang_name} declining a meeting cancellation request "
            f"(i.e., the meeting will still proceed as planned).\n\n"
            f"From: {sender}\nOriginal subject: {subject}\nContext: {summary}\n\n"
            f"Requirements: politely explain that the meeting is still on, "
            f"confirm the original schedule, invite them to reach out if they have concerns. "
            f"Professional tone. Do NOT include a sign-off line. Return only the email body."
        )
    elif action_type == "reply_required":
        prompt = (
            f"Write a professional reply email in {lang_name} to the following message.\n\n"
            f"From: {sender}\nSubject: {subject}\nContext: {summary}\n\n"
            f"Requirements: address the sender's query or request appropriately, "
            f"be concise and professional. "
            f"Do NOT include a sign-off line. Return only the email body."
        )
    else:
        prompt = (
            f"Write a short professional reply email in {lang_name}.\n\n"
            f"From: {sender}\nSubject: {subject}\nContext: {summary}\n\n"
            f"Requirements: appropriate response, professional tone. "
            f"Do NOT include a sign-off line. Return only the email body."
        )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are an email assistant.\n"
                        f"Always reply in the requested language: {lang_name}.\n"
                        f"Never switch languages.\n"
                        f"Return only the email body."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("[Dashboard] Draft generation failed: %s", exc)
        return f"[Auto-draft unavailable — please type your reply here]\n\nRegards"


def _find_free_slots(from_dt: datetime, n_slots: int = 3) -> list[dict]:
    """Return up to n_slots free 60-minute calendar slots starting from from_dt."""
    from app.agents.calendar_agent import _check_conflict, _get_service

    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=ICT)

    try:
        service = _get_service()
    except Exception as exc:
        logger.error("[Dashboard] Cannot get calendar service for free slots: %s", exc)
        return []

    slots: list[dict] = []
    candidate = from_dt
    max_attempts = 48  # scan up to 48 candidate hours

    for _ in range(max_attempts):
        if len(slots) >= n_slots:
            break

        # Skip weekends
        if candidate.weekday() >= 5:
            days_ahead = 7 - candidate.weekday()
            candidate = (candidate + timedelta(days=days_ahead)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            continue

        # Normalise to business hours 09:00–17:00
        if candidate.hour < 9:
            candidate = candidate.replace(hour=9, minute=0, second=0, microsecond=0)
        elif candidate.hour >= 17:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            continue

        slot_end = candidate + timedelta(hours=1)

        try:
            busy = _check_conflict(service, candidate.replace(tzinfo=None), slot_end.replace(tzinfo=None))
            if not busy:
                days_vi = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
                label = f"{days_vi[candidate.weekday()]}, {candidate.strftime('%d/%m/%Y lúc %H:%M')}"
                slots.append({
                    "start": candidate.replace(tzinfo=None).isoformat(),
                    "end":   slot_end.replace(tzinfo=None).isoformat(),
                    "label": label,
                })
        except Exception as exc:
            logger.warning("[Dashboard] free-slot check error at %s: %s", candidate, exc)

        candidate += timedelta(hours=1)

    return slots


def _send_gmail(to: str, subject: str, body: str, triggered_by: str = "hitl_action"):
    """Send an email via Gmail API and log to sent_emails."""
    from app.core.auth import get_gmail_service
    from app.db.sqlite import insert_sent_email

    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    service = get_gmail_service()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    try:
        insert_sent_email(to, subject, body, triggered_by)
    except Exception as exc:
        logger.warning("[Dashboard] Could not log sent email: %s", exc)


def _mark_gmail_read(gmail_message_id: str):
    """Mark a Gmail message as read via the API."""
    try:
        from app.core.auth import get_gmail_service
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me",
            id=gmail_message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as exc:
        logger.warning("[Dashboard] Could not mark Gmail message as read: %s", exc)


# ── Summary (new) ─────────────────────────────────────────────────────────────


@router.get("/dashboard/summary")
async def dashboard_summary(
    current_user: dict = Depends(get_current_user),
    date_from: str = None,
    date_to: str = None,
):
    """
    Return KPI counts for the Dashboard Summary section.

    Counters respect the optional date_from / date_to window.
    pending_actions is always current (not date-filtered).
    """
    from app.db.sqlite import get_dashboard_summary

    return get_dashboard_summary(date_from=date_from, date_to=date_to)


# ── Pending Actions (new) ────────────────────────────────────────────────────


@router.get("/dashboard/pending-actions")
async def pending_actions(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    date_from: str = None,
    date_to: str = None,
):
    """
    Return paginated pending actions from three sources:
    email_insights (action_required), pending_invites, and pending_reschedules.
    """
    from app.db.sqlite import get_pending_actions

    return get_pending_actions(
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
    )


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get("/dashboard/stats")
async def dashboard_stats(
    current_user: dict = Depends(get_current_user),
    range_days: int = 7,
):
    """
    Return upcoming Google Calendar events + aggregate log statistics.

    Args:
        range_days: how many calendar days ahead to fetch (default 7)
    """
    from app.db.sqlite import get_log_stats
    from app.agents.chat_agent import _fetch_upcoming_events

    upcoming = _fetch_upcoming_events(range_days=range_days)
    stats = get_log_stats()
    return {"upcoming_events": upcoming, "stats": stats}


# ── Logs ──────────────────────────────────────────────────────────────────────


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
    from app.db.sqlite import get_logs

    return get_logs(
        agent=agent,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
    )


# ── Email Intelligence ────────────────────────────────────────────────────────


@router.get("/dashboard/email-stats")
async def email_stats(
    current_user: dict = Depends(get_current_user),
    track_view: bool = False,
    date_from: str = None,
    date_to: str = None,
):
    """
    Return email intelligence category statistics.

    When date_from / date_to are provided they override the implicit
    last_dashboard_view_at window.
    """
    from app.db.sqlite import (
        get_email_statistics_since,
        get_email_statistics_range,
        update_last_dashboard_view,
    )

    if date_from or date_to:
        return get_email_statistics_range(date_from=date_from, date_to=date_to)

    last_view = current_user.get("last_dashboard_view_at")
    stats = get_email_statistics_since(since=last_view)
    if track_view:
        update_last_dashboard_view(current_user["id"])
    return stats


@router.get("/dashboard/recent-emails")
async def recent_emails(current_user: dict = Depends(get_current_user)):
    from app.db.sqlite import get_recent_emails_for_summary

    last_view = current_user.get("last_dashboard_view_at")
    emails = get_recent_emails_for_summary(since=last_view)
    return {"emails": emails, "count": len(emails)}


# ── AI Executive Summary ──────────────────────────────────────────────────────


@router.get("/dashboard/executive-summary")
async def executive_summary(current_user: dict = Depends(get_current_user)):
    """Delegate to ChiefOfStaffAgent.executive_briefing_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, executive_briefing_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return executive_briefing_skill(context)


@router.get("/dashboard/risks")
async def dashboard_risks(current_user: dict = Depends(get_current_user)):
    """Rule-based risk alerts — ChiefOfStaffAgent.risk_detection_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, risk_detection_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return risk_detection_skill(context)


@router.get("/dashboard/priorities")
async def dashboard_priorities(current_user: dict = Depends(get_current_user)):
    """Ranked action priorities — ChiefOfStaffAgent.priority_recommendation_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, priority_recommendation_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return priority_recommendation_skill(context)


@router.get("/dashboard/waiting-responses")
async def dashboard_waiting_responses(current_user: dict = Depends(get_current_user)):
    """Waiting-for-reply analysis — ChiefOfStaffAgent.waiting_response_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, waiting_response_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return waiting_response_skill(context)


@router.get("/dashboard/deadlines")
async def dashboard_deadlines(current_user: dict = Depends(get_current_user)):
    """Deadline intelligence — ChiefOfStaffAgent.deadline_intelligence_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, deadline_intelligence_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return deadline_intelligence_skill(context)


@router.get("/dashboard/productivity")
async def dashboard_productivity(current_user: dict = Depends(get_current_user)):
    """Productivity metrics + AI insights — ChiefOfStaffAgent.productivity_insight_skill()."""
    from app.agents.chief_of_staff_agent import gather_context, productivity_insight_skill

    context = gather_context(current_user.get("last_dashboard_view_at"))
    return productivity_insight_skill(context)


# ── Email Insights ────────────────────────────────────────────────────────────


@router.get("/dashboard/email-insights")
async def email_insights(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "created_at",
    category: str = None,
    priority: str = None,
    search: str = None,
    date_from: str = None,
    date_to: str = None,
    is_read: int = None,
):
    from app.db.sqlite import get_email_insights

    return get_email_insights(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        category=category,
        priority=priority,
        search=search,
        date_from=date_from,
        date_to=date_to,
        is_read=is_read,
    )


# ── Sent Emails ───────────────────────────────────────────────────────────────


@router.get("/dashboard/sent-emails")
async def sent_emails(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    date_from: str = None,
    date_to: str = None,
):
    """Return paginated outgoing email log from sent_emails table."""
    from app.db.sqlite import get_sent_emails

    return get_sent_emails(
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
    )


# ── Read status ───────────────────────────────────────────────────────────────


@router.post("/dashboard/mark-read/{insight_id}")
async def mark_email_read(
    insight_id: int,
    current_user: dict = Depends(get_current_user),
):
    from app.db.sqlite import mark_email_read as db_mark_read

    updated = db_mark_read(insight_id)
    if not updated:
        return {"status": "not_found", "message": "Email insight not found"}
    return {"status": "ok"}


@router.get("/dashboard/unread-count")
async def unread_count(current_user: dict = Depends(get_current_user)):
    from app.db.sqlite import count_unread_emails

    return {"unread": count_unread_emails()}


# ── Meeting Confirmations ─────────────────────────────────────────────────────


@router.get("/dashboard/meeting-confirmations")
async def meeting_confirmations(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 10,
):
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
            payload = (
                json.loads(item["payload"])
                if isinstance(item["payload"], str)
                else item["payload"]
            )
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


# ── HITL Pending Actions ──────────────────────────────────────────────────────


@router.get("/dashboard/actions")
async def list_actions(
    current_user: dict = Depends(get_current_user),
    page: int = 1,
    page_size: int = 20,
    status: str = None,
    action_type: str = None,
):
    """Return paginated pending_actions from the new HITL table."""
    from app.db.sqlite import list_pending_actions

    # Default: show only active actions (pending + waiting_send_confirmation)
    if status is None:
        from app.db.sqlite import get_connection
        conn = get_connection()
        cur = conn.cursor()

        conditions = ["status IN ('pending', 'draft_ready', 'waiting_send_confirmation', 'waiting_external_reply')"]
        params: list = []
        if action_type:
            conditions.append("action_type = ?")
            params.append(action_type)

        where = "WHERE " + " AND ".join(conditions)
        cur.execute(f"SELECT COUNT(*) FROM pending_actions {where}", params)
        total = cur.fetchone()[0]

        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT id, gmail_message_id, email_insight_id, action_type, sender, "
            f"subject, summary, recommendation, confidence, draft_response, options, "
            f"calendar_result, status, created_at, updated_at, thread_id "
            f"FROM pending_actions {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )
        rows = cur.fetchall()
        conn.close()

        from app.db.sqlite import _pending_action_row_to_dict
        items = [_pending_action_row_to_dict(r) for r in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    return list_pending_actions(
        page=page, page_size=page_size, status=status, action_type=action_type
    )


@router.get("/dashboard/actions/{action_id}")
async def get_action(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return a single pending_action by id."""
    from app.db.sqlite import get_pending_action

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.post("/dashboard/actions/{action_id}/accept")
async def accept_action(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Accept flow:
    - meeting_request/reschedule: re-check calendar → create event → draft confirmation
    - meeting_cancel: draft cancellation-confirm email (no calendar call needed)
    Status transitions atomically: pending → draft_ready → waiting_send_confirmation.
    """
    from app.db.sqlite import get_pending_action, update_pending_action_fields, claim_action_status
    from app.agents.calendar_agent import _create_event, _check_conflict, _get_service, DEFAULT_DURATION

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot accept action with status '{action['status']}'")

    # Atomic lock — prevents duplicate calendar creation if two requests race
    if not claim_action_status(action_id, from_statuses="pending", to_status="draft_ready"):
        raise HTTPException(status_code=409, detail="Hành động đang được xử lý bởi yêu cầu khác.")

    existing_options = action.get("options") or {}
    if isinstance(existing_options, list):
        existing_options = {"available": existing_options}
    available = existing_options.get("available", [])

    atype = action.get("action_type")

    try:
        # ── meeting_cancel: just generate cancellation-confirm email ──────────
        if atype == "meeting_cancel":
            draft = _generate_email_draft("confirm_cancel", {
                "sender":  action["sender"],
                "subject": action["subject"],
                "summary": action["summary"],
            })
            update_pending_action_fields(
                action_id,
                status="waiting_send_confirmation",
                draft_response=draft,
                options={**existing_options, "step": "confirm_cancel"},
            )
            from app.core.logger import log_event
            log_event(agent="dashboard", status="cancel_confirmed", payload={"action_id": action_id})
            return {
                "status": "ok",
                "draft": draft,
                "message": "Đã soạn email xác nhận huỷ lịch. Xem trước và bấm Gửi để hoàn tất.",
            }

        # ── meeting_request / meeting_reschedule: calendar flow ───────────────
        cal = action.get("calendar_result") or {}
        start_str = cal.get("start")
        if not start_str:
            raise HTTPException(status_code=400, detail="No meeting time found in calendar_result")

        start_dt = datetime.fromisoformat(start_str)
        end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION)

        try:
            service = _get_service()
        except Exception as exc:
            logger.error("[Dashboard] Cannot connect to Google Calendar: %s", exc)
            raise HTTPException(status_code=500, detail=f"Không thể kết nối Google Calendar: {exc}")

        # Re-check availability before creating
        try:
            busy = _check_conflict(service, start_dt, end_dt)
            if busy:
                # Rollback lock so user can retry or choose suggest-time
                claim_action_status(action_id, from_statuses="draft_ready", to_status="pending")
                return {
                    "status": "conflict",
                    "message": f"Khung giờ {start_dt.strftime('%H:%M %d/%m/%Y')} đã bận, không thể tạo lịch.",
                    "busy_slots": busy,
                }
        except Exception as exc:
            logger.warning("[Dashboard] Re-check calendar failed (proceeding anyway): %s", exc)

        summary_text = action.get("subject") or action.get("summary") or "Cuộc họp"
        location     = cal.get("location")
        attendees    = list(cal.get("attendees") or [])
        if action.get("sender") and action["sender"] not in attendees:
            attendees = [action["sender"]] + attendees

        event = _create_event(
            service=service,
            summary=summary_text,
            start_dt=start_dt,
            end_dt=end_dt,
            location=location,
            attendees=attendees,
            description=f"Tạo từ HITL Dashboard — action_id={action_id}",
        )
        event_id   = event.get("id", "")
        event_link = event.get("htmlLink", "")
        logger.info("[Dashboard] Created calendar event id=%s link=%s", event_id, event_link)

        draft_type = "accept_meeting" if atype == "meeting_request" else "accept_reschedule"
        draft = _generate_email_draft(draft_type, {
            "sender":          action["sender"],
            "subject":         action["subject"],
            "summary":         action["summary"],
            "calendar_result": cal,
        })

        update_pending_action_fields(
            action_id,
            status="waiting_send_confirmation",
            draft_response=draft,
            options={
                **existing_options,
                "step":       "accept",
                "event_id":   event_id,
                "event_link": event_link,
            },
        )

        from app.core.logger import log_event
        log_event(agent="dashboard", status="action_accepted", payload={
            "action_id": action_id, "event_id": event_id, "event_link": event_link,
        })

        return {
            "status": "ok",
            "draft": draft,
            "calendar_event_link": event_link,
            "message": "Lịch đã được tạo. Xem trước email và bấm Gửi để hoàn tất.",
        }

    except HTTPException:
        raise
    except Exception as exc:
        # Rollback so user can retry
        claim_action_status(action_id, from_statuses="draft_ready", to_status="pending")
        logger.error("[Dashboard] accept_action failed, rolled back: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {exc}")


@router.post("/dashboard/actions/{action_id}/reject")
async def reject_action(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Reject flow: generate rejection draft → status = waiting_send_confirmation.
    For meeting_cancel: generates a 'decline cancel' draft (meeting stays on).
    """
    from app.db.sqlite import get_pending_action, update_pending_action_fields, claim_action_status

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot reject action with status '{action['status']}'")

    if not claim_action_status(action_id, from_statuses="pending", to_status="draft_ready"):
        raise HTTPException(status_code=409, detail="Hành động đang được xử lý bởi yêu cầu khác.")

    existing_options = action.get("options") or {}
    if isinstance(existing_options, list):
        existing_options = {"available": existing_options}

    atype = action.get("action_type")
    draft_type = "decline_cancel" if atype == "meeting_cancel" else "reject_meeting"

    try:
        draft = _generate_email_draft(draft_type, {
            "sender":  action["sender"],
            "subject": action["subject"],
            "summary": action["summary"],
        })

        update_pending_action_fields(
            action_id,
            status="waiting_send_confirmation",
            draft_response=draft,
            options={**existing_options, "step": "reject"},
        )

        return {
            "status": "ok",
            "draft": draft,
            "message": "Đã tạo email từ chối. Xem trước và bấm Gửi để hoàn tất.",
        }
    except Exception as exc:
        claim_action_status(action_id, from_statuses="draft_ready", to_status="pending")
        logger.error("[Dashboard] reject_action failed, rolled back: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {exc}")


@router.get("/dashboard/actions/{action_id}/free-slots")
async def get_free_slots(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return 3 upcoming free 60-minute calendar slots."""
    from app.db.sqlite import get_pending_action

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    # Start searching from now (or from the requested meeting time + 1 day)
    cal = action.get("calendar_result") or {}
    start_str = cal.get("start")
    try:
        base_dt = datetime.fromisoformat(start_str) if start_str else datetime.now()
        # Always search from now onwards
        base_dt = max(base_dt, datetime.now())
    except Exception:
        base_dt = datetime.now()

    slots = _find_free_slots(base_dt, n_slots=3)
    return {"slots": slots}


@router.post("/dashboard/actions/{action_id}/suggest-time")
async def suggest_time(
    action_id: int,
    body: SuggestTimeBody,
    current_user: dict = Depends(get_current_user),
):
    """
    User selects a free slot → generate counter-proposal draft
    → status = waiting_send_confirmation.
    """
    from app.db.sqlite import get_pending_action, update_pending_action_fields, claim_action_status

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot suggest time for action with status '{action['status']}'")

    if not claim_action_status(action_id, from_statuses="pending", to_status="draft_ready"):
        raise HTTPException(status_code=409, detail="Hành động đang được xử lý bởi yêu cầu khác.")

    existing_options = action.get("options") or {}
    if isinstance(existing_options, list):
        existing_options = {"available": existing_options}

    selected_slot = body.selected_slot
    draft_type = "accept_reschedule" if action["action_type"] == "meeting_reschedule" else "suggest_new_time"

    try:
        draft = _generate_email_draft(draft_type, {
            "sender":          action["sender"],
            "subject":         action["subject"],
            "summary":         action["summary"],
            "selected_time":   selected_slot,
            "calendar_result": action.get("calendar_result") or {},
        })

        update_pending_action_fields(
            action_id,
            status="waiting_send_confirmation",
            draft_response=draft,
            options={**existing_options, "step": "suggest_time", "selected_slot": selected_slot},
        )

        return {
            "status": "ok",
            "draft": draft,
            "selected_slot": selected_slot,
            "message": "Đã tạo email đề xuất giờ mới. Xem trước và bấm Gửi để hoàn tất.",
        }
    except Exception as exc:
        claim_action_status(action_id, from_statuses="draft_ready", to_status="pending")
        logger.error("[Dashboard] suggest_time failed, rolled back: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {exc}")


@router.post("/dashboard/actions/{action_id}/send")
async def send_action(
    action_id: int,
    body: SendActionBody,
    current_user: dict = Depends(get_current_user),
):
    """
    Send the draft email → status = completed (or waiting_external_reply for suggest-time).
    This is the ONLY endpoint that actually sends an email.
    Atomic lock prevents duplicate sends from concurrent requests.
    """
    from app.db.sqlite import (
        get_pending_action, update_pending_action_fields,
        mark_email_read, get_insights_by_message_id, claim_action_status,
    )
    from app.core.logger import log_event
    from app.core.config import settings

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "waiting_send_confirmation":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send email for action with status '{action['status']}'"
        )

    draft = body.draft_response or action.get("draft_response") or ""
    if not draft.strip():
        raise HTTPException(status_code=400, detail="No email draft to send")

    # Determine final status: suggest_time flow waits for external reply
    opts = action.get("options") or {}
    if isinstance(opts, list):
        opts = {}
    step = opts.get("step", "")
    final_status = "waiting_external_reply" if step == "suggest_time" else "completed"

    # Atomic claim: only one request can proceed to send
    if not claim_action_status(action_id, from_statuses="waiting_send_confirmation", to_status=final_status):
        raise HTTPException(status_code=409, detail="Email đã được gửi hoặc hành động đã thay đổi trạng thái.")

    to_addr   = action["sender"]
    subject   = f"Re: {action['subject'] or 'Your request'}"
    organizer_name = current_user.get("name") or settings.ORGANIZER_EMAIL
    full_body = f"{draft}\n\nBest regards,\n{organizer_name}"

    try:
        _send_gmail(to_addr, subject, full_body, triggered_by=f"hitl_action_{action_id}")
        logger.info("[Dashboard] Email sent to %s for action_id=%d", to_addr, action_id)
    except Exception as exc:
        # Rollback so user can retry
        claim_action_status(action_id, from_statuses=final_status, to_status="waiting_send_confirmation")
        logger.error("[Dashboard] Failed to send email, rolled back: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể gửi email: {exc}")

    # Best-effort: mark original email as read (non-critical, after send succeeded)
    gmail_id = action.get("gmail_message_id")
    if gmail_id:
        try:
            insight = get_insights_by_message_id(gmail_id)
            if insight:
                mark_email_read(insight["id"])
        except Exception as exc:
            logger.warning("[Dashboard] DB mark-read failed: %s", exc)
        _mark_gmail_read(gmail_id)

    log_event(agent="dashboard", status="action_completed", payload={
        "action_id":    action_id,
        "action_type":  action["action_type"],
        "sent_to":      to_addr,
        "final_status": final_status,
    })

    msg = (
        f"Email đã gửi đến {to_addr}. Đang chờ phản hồi."
        if final_status == "waiting_external_reply"
        else f"Email đã gửi đến {to_addr}. Hành động hoàn tất."
    )
    return {"status": "ok", "sent_to": to_addr, "message": msg}


@router.post("/dashboard/actions/{action_id}/cancel")
async def cancel_action(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Cancel a pending action without sending any email.
    If the user had already accepted (step=accept) and a calendar event was
    created, the event is deleted from Google Calendar before cancelling.
    """
    from app.db.sqlite import get_pending_action, update_pending_action_fields

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Action is already '{action['status']}'")

    # Delete orphaned calendar event if accept was already performed
    opts = action.get("options") or {}
    if isinstance(opts, list):
        opts = {}
    if opts.get("step") == "accept":
        event_id = opts.get("event_id")
        if event_id:
            try:
                from app.agents.calendar_agent import _get_service
                svc = _get_service()
                svc.events().delete(calendarId="primary", eventId=event_id).execute()
                logger.info("[Dashboard] Deleted orphaned calendar event %s for action_id=%d",
                            event_id, action_id)
            except Exception as exc:
                logger.warning("[Dashboard] Could not delete calendar event %s: %s", event_id, exc)

    update_pending_action_fields(action_id, status="cancelled")
    return {"status": "ok", "message": "Hành động đã bị huỷ."}


@router.post("/dashboard/actions/{action_id}/generate-reply")
async def generate_reply(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    For reply_required / confirmation_required / deadline_notice actions:
    generate an AI draft reply → status = waiting_send_confirmation.
    """
    from app.db.sqlite import get_pending_action, update_pending_action_fields, claim_action_status, get_email_insight_language

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot generate reply for status '{action['status']}'")

    if not claim_action_status(action_id, from_statuses="pending", to_status="draft_ready"):
        raise HTTPException(status_code=409, detail="Hành động đang được xử lý bởi yêu cầu khác.")

    insight_id = action.get("email_insight_id")
    detected_language = None
    if insight_id:
        lang_info = get_email_insight_language(insight_id)
        detected_language = lang_info.get("detected_language")

    logger.info(
        "[GenerateReply] language=%s action_id=%s",
        detected_language,
        action_id,
    )

    existing_options = action.get("options") or {}
    if isinstance(existing_options, list):
        existing_options = {"available": existing_options}

    try:
        draft = _generate_email_draft("reply_required", {
            "sender":  action["sender"],
            "subject": action["subject"],
            "summary": action["summary"],
        }, detected_language=detected_language)

        logger.info(
            "[GenerateReply] draft generated language=%s",
            detected_language,
        )

        update_pending_action_fields(
            action_id,
            status="waiting_send_confirmation",
            draft_response=draft,
            options={**existing_options, "step": "reply"},
        )

        return {
            "status": "ok",
            "draft": draft,
            "message": "Đã tạo email phản hồi. Xem trước và bấm Gửi để hoàn tất.",
        }
    except Exception as exc:
        claim_action_status(action_id, from_statuses="draft_ready", to_status="pending")
        logger.error("[Dashboard] generate_reply failed, rolled back: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi tạo draft: {exc}")


@router.post("/dashboard/actions/{action_id}/acknowledge")
async def acknowledge_action(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    For deadline_notice / unclear actions where no reply is needed:
    mark original email as read → status = completed.  No email is sent.
    """
    from app.db.sqlite import (
        get_pending_action, update_pending_action_fields,
        mark_email_read, get_insights_by_message_id,
    )

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot acknowledge action with status '{action['status']}'")

    update_pending_action_fields(action_id, status="completed")

    gmail_id = action.get("gmail_message_id")
    if gmail_id:
        try:
            insight = get_insights_by_message_id(gmail_id)
            if insight:
                mark_email_read(insight["id"])
        except Exception as exc:
            logger.warning("[Dashboard] DB mark-read failed: %s", exc)
        _mark_gmail_read(gmail_id)

    return {"status": "ok", "message": "Đã đánh dấu đã xử lý."}


@router.get("/dashboard/actions/{action_id}/original-email")
async def get_original_email(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the original email content for a pending action.

    Primary source: email_insights.body (stored at ingestion time).
    Fallback: live Gmail API fetch using gmail_message_id (covers rows ingested
    before the body column was added).
    """
    from app.db.sqlite import get_pending_action, get_insights_by_message_id

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    gmail_id = action.get("gmail_message_id")
    thread_id = action.get("thread_id")

    # Try DB first (fast path)
    insight = None
    if action.get("email_insight_id"):
        from app.db.sqlite import get_email_insight
        insight = get_email_insight(action["email_insight_id"])
    if insight is None and gmail_id:
        insight = get_insights_by_message_id(gmail_id)

    body = insight.get("body") if insight else None
    received_at = insight.get("created_at") if insight else action.get("created_at")

    # Fallback: fetch live from Gmail if body not stored
    if not body and gmail_id:
        try:
            from app.core.auth import get_gmail_service
            import base64 as _b64
            from email import message_from_bytes as _mfb
            svc = get_gmail_service()
            msg = svc.users().messages().get(userId="me", id=gmail_id, format="raw").execute()
            raw = _b64.urlsafe_b64decode(msg["raw"])
            mail = _mfb(raw)
            if mail.is_multipart():
                for part in mail.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                body = mail.get_payload(decode=True).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("[Dashboard] Gmail fallback fetch failed for action %d: %s", action_id, exc)

    return {
        "action_id":   action_id,
        "gmail_id":    gmail_id,
        "thread_id":   thread_id,
        "sender":      action.get("sender"),
        "subject":     action.get("subject"),
        "received_at": received_at,
        "body":        body or "",
        "body_source": "db" if (insight and insight.get("body")) else ("gmail_api" if body else "unavailable"),
    }


@router.get("/dashboard/actions/{action_id}/thread")
async def get_email_thread(
    action_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the full Gmail thread for a pending action in chronological order.
    The message that triggered this action is flagged with is_trigger=True.
    """
    from app.db.sqlite import get_pending_action

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    thread_id = action.get("thread_id")
    gmail_id  = action.get("gmail_message_id")

    if not thread_id:
        raise HTTPException(status_code=404, detail="Thread ID not available for this action")

    try:
        from app.core.auth import get_gmail_service
        import base64 as _b64
        from email import message_from_bytes as _mfb
        from app.core.gmail_poller import decode_mime_header, _parse_date_header
        from email.utils import parseaddr

        svc = get_gmail_service()
        thread = svc.users().threads().get(userId="me", id=thread_id, format="raw").execute()
        messages_raw = thread.get("messages", [])

        messages = []
        for m in messages_raw:
            try:
                raw = _b64.urlsafe_b64decode(m["raw"])
                mail = _mfb(raw)

                body = ""
                if mail.is_multipart():
                    for part in mail.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            break
                else:
                    body = mail.get_payload(decode=True).decode("utf-8", errors="ignore")

                raw_from = str(mail.get("From", "") or "")
                decoded_from = decode_mime_header(raw_from)
                _, sender_addr = parseaddr(decoded_from)

                messages.append({
                    "id":         m["id"],
                    "thread_id":  m.get("threadId"),
                    "sender":     sender_addr or decoded_from,
                    "subject":    decode_mime_header(str(mail.get("Subject", "") or "")),
                    "date":       _parse_date_header(mail),
                    "body":       body.strip(),
                    "is_trigger": m["id"] == gmail_id,
                })
            except Exception as exc:
                logger.warning("[Dashboard] Could not decode thread message %s: %s", m.get("id"), exc)

        # Sort chronologically
        messages.sort(key=lambda x: x["date"])

        return {
            "action_id":     action_id,
            "thread_id":     thread_id,
            "message_count": len(messages),
            "messages":      messages,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Dashboard] Thread fetch failed for action %d: %s", action_id, exc)
        raise HTTPException(status_code=500, detail=f"Không thể tải email thread: {exc}")


@router.patch("/dashboard/actions/{action_id}/draft")
async def update_action_draft(
    action_id: int,
    body: EditDraftBody,
    current_user: dict = Depends(get_current_user),
):
    """Update the draft_response text for a pending action."""
    from app.db.sqlite import get_pending_action, update_pending_action_draft

    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    update_pending_action_draft(action_id, body.draft_response)
    return {"status": "ok", "message": "Draft đã được cập nhật."}
