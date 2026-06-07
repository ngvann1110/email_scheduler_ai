from app.agents.email_agent import process_email
from app.agents.calendar_agent import process_schedule, process_cancel, process_reschedule
from app.agents.conflict_agent import find_alternatives
from app.agents.notification_agent import send_notification
from app.core.logger import log_event


async def run_pipeline(email) -> dict:
    email_result = process_email(email)
    log_event(agent="email_agent", status=email_result.get(
        "intent", "unknown"), payload=email_result)
    intent = email_result.get("intent", "other")

    if intent == "schedule":
        calendar_result = process_schedule(email_result)
        log_event(agent="calendar_agent", status=calendar_result.get(
            "status"), payload=calendar_result)
        conflict_result = None
        if calendar_result.get("status") == "conflict":
            conflict_result = find_alternatives(
                requested_time=email_result.get("time"), duration_minutes=60)
            log_event(agent="conflict_agent", status=conflict_result.get(
                "status"), payload=conflict_result)
        notification_result = send_notification(
            email, email_result, calendar_result, conflict_result)
        log_event(agent="notification_agent", status=notification_result.get(
            "status"), payload=notification_result)
        return {"type": "schedule_flow", "data": {"email": email_result, "calendar": calendar_result, "conflict": conflict_result, "notification": notification_result}}

    elif intent == "cancel":
        calendar_result = process_cancel(email_result)
        log_event(agent="calendar_agent", status=calendar_result.get(
            "status"), payload=calendar_result)
        notification_result = send_notification(
            email, email_result, calendar_result)
        log_event(agent="notification_agent", status=notification_result.get(
            "status"), payload=notification_result)
        return {"type": "cancel_flow", "data": {"email": email_result, "calendar": calendar_result, "notification": notification_result}}

    elif intent == "reschedule":
        calendar_result = process_reschedule(email_result)
        log_event(agent="calendar_agent", status=calendar_result.get(
            "status"), payload=calendar_result)
        conflict_result = None
        if calendar_result.get("status") == "conflict":
            conflict_result = find_alternatives(
                requested_time=email_result.get("time"), duration_minutes=60)
            log_event(agent="conflict_agent", status=conflict_result.get(
                "status"), payload=conflict_result)
        notification_result = send_notification(
            email, email_result, calendar_result, conflict_result)
        log_event(agent="notification_agent", status=notification_result.get(
            "status"), payload=notification_result)
        return {"type": "reschedule_flow", "data": {"email": email_result, "calendar": calendar_result, "conflict": conflict_result, "notification": notification_result}}

    elif intent == "inquiry":
        log_event(agent="orchestrator",
                  status="inquiry_todo", payload=email_result)
        return {"type": "inquiry_flow", "data": {"email": email_result}}

    else:
        log_event(agent="orchestrator", status="other", payload=email_result)
        return {"type": "summary_flow", "data": {"email": email_result}}
