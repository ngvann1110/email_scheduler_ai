"""
Dashboard API router.

All /dashboard/... endpoints live here, separated from chat/confirm/decline
concerns in api/v1/chat.py. URL paths are identical to what chat.py had
before the extraction so no clients need updating.
"""
import json
import logging

from fastapi import APIRouter, Depends

from app.core.jwt_auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


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
