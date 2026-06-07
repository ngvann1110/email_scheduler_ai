from app.agents.email_agent import process_email
from app.agents.calendar_agent import process_schedule, process_cancel, process_reschedule
from app.agents.conflict_agent import find_alternatives
from app.agents.notification_agent import send_notification, send_reply
from app.agents.chat_agent import chat
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
        try:
            reply_text = chat(
                [{"role": "user", "content": getattr(email, "body", "")}]).get("reply", "")
            noti_result = send_reply(
                to_email=getattr(email, "sender", ""),
                subject="Phản hồi tự động — Email Scheduler",
                body_text=reply_text,
            )
            log_event(agent="notification_agent", status=noti_result.get(
                "status"), payload=noti_result)
            log_event(agent="orchestrator",
                      status="inquiry_done", payload=email_result)
            return {"type": "inquiry_flow", "data": {"email": email_result, "reply": reply_text, "notification": noti_result}}
        except Exception as e:
            log_event(agent="orchestrator",
                      status="inquiry_error", payload={"error": str(e)})
            return {"type": "inquiry_flow", "data": {"email": email_result, "notification": {"status": "error", "message": str(e)}}}

    else:
        # "other" intent — gửi email phản hồi cố định, không gọi LLM
        try:
            fallback_body = (
                "Xin chào,\n\n"
                "Hệ thống Email Scheduler đã nhận được email của bạn "
                "nhưng không thể xác định yêu cầu cụ thể.\n\n"
                "Vui lòng gửi lại email với nội dung rõ ràng hơn "
                "(ví dụ: đặt lịch họp, huỷ lịch, dời lịch, hoặc "
                "hỏi về lịch trống).\n\n"
                "Trân trọng,\n"
                "Email Scheduler AI"
            )
            noti_result = send_reply(
                to_email=getattr(email, "sender", ""),
                subject="Phản hồi tự động — Email Scheduler",
                body_text=fallback_body,
            )
            log_event(agent="notification_agent", status=noti_result.get(
                "status"), payload=noti_result)
            log_event(agent="orchestrator",
                      status="other_replied", payload=email_result)
            return {"type": "other_flow", "data": {"email": email_result, "notification": noti_result}}
        except Exception as e:
            log_event(agent="orchestrator",
                      status="other_error", payload={"error": str(e)})
            return {"type": "other_flow", "data": {"email": email_result, "notification": {"status": "error", "message": str(e)}}}
