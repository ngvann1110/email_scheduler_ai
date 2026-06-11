import json
import logging

from app.agents.email_agent import process_email
from app.agents.calendar_agent import process_schedule, process_reschedule
from app.agents.conflict_agent import find_alternatives
from app.agents.notification_agent import send_notification, send_reply
from app.agents.chat_agent import chat
from app.agents.email_intelligence_agent import process_email as classify_intelligence
from app.db.sqlite import insert_email_analysis
from app.core.logger import log_event

logger = logging.getLogger(__name__)


def _send_intelligence_notification(email, intelligence_result: dict) -> dict:
    """
    Gửi email phản hồi dựa trên kết quả phân tích của Email Intelligence Agent.

    Định dạng: category, summary, importance_score, extracted_data.
    """
    category = intelligence_result.get("category", "other")
    importance_score = intelligence_result.get("importance_score", 30)
    summary = intelligence_result.get("summary", "- Không có tóm tắt")
    extracted_data = intelligence_result.get("extracted_data", {})

    # ── Map category to Vietnamese label ──────────────────────────────
    category_labels = {
        "meeting": "Họp hành",
        "report": "Báo cáo",
        "partnership": "Hợp tác / Đối tác",
        "support": "Hỗ trợ",
        "announcement": "Thông báo",
        "other": "Khác",
    }
    category_display = category_labels.get(category, category)

    # ── Build extracted info section ──────────────────────────────────
    info_lines = []
    if extracted_data.get("deadline"):
        info_lines.append(f"Deadline: {extracted_data['deadline']}")
    if extracted_data.get("owner"):
        info_lines.append(f"Người phụ trách: {extracted_data['owner']}")
    if extracted_data.get("project"):
        info_lines.append(f"Dự án: {extracted_data['project']}")
    if extracted_data.get("meeting_date"):
        info_lines.append(f"Ngày họp: {extracted_data['meeting_date']}")
    if extracted_data.get("meeting_location"):
        info_lines.append(f"Địa điểm: {extracted_data['meeting_location']}")
    attendees = extracted_data.get("meeting_attendees", [])
    if attendees:
        info_lines.append(f"Người tham dự: {', '.join(attendees)}")
    key_points = extracted_data.get("key_points", [])
    if key_points:
        info_lines.append("\nÝ chính:")
        for kp in key_points:
            info_lines.append(f"  • {kp}")
    action_items = extracted_data.get("action_items", [])
    if action_items:
        info_lines.append("\nHành động cần làm:")
        for ai in action_items:
            info_lines.append(f"  ☐ {ai}")

    info_text = "\n".join(
        f"  {line}" for line in info_lines) if info_lines else "  (không có thông tin bổ sung)"

    body = (
        "Xin chào,\n\n"
        "Chúng tôi đã phân tích email của bạn.\n\n"
        f"Phân loại: {category_display}\n\n"
        f"Tóm tắt:\n{summary}\n\n"
        f"Độ ưu tiên: {importance_score}/100\n\n"
        "Thông tin quan trọng:\n"
        f"{info_text}\n\n"
        "Trân trọng,\n"
        "Email Scheduler AI"
    )

    return send_reply(
        to_email=getattr(email, "sender", ""),
        subject="Phản hồi tự động — Email Scheduler",
        body_text=body,
    )


async def _handle_send_email(email, email_result: dict) -> dict:
    """Xử lý intent send_email: soạn và gửi email mới."""
    try:
        summary = email_result.get("summary", "")
        sender = getattr(email, "sender", "")
        log_event(agent="orchestrator",
                  status="send_email_started", payload=email_result)

        # Gửi email xác nhận đã nhận yêu cầu soạn email
        body = "Xin chào,\n\n"
        body += "Hệ thống đã nhận yêu cầu soạn email của bạn.\n\n"
        if summary:
            body += f"Nội dung yêu cầu: {summary}\n\n"
        body += "Chức năng soạn email đang được phát triển. "
        body += "Tính năng đầy đủ sẽ sớm được triển khai.\n\n"
        body += "Trân trọng,\nEmail Scheduler AI"

        noti_result = send_reply(
            to_email=sender,
            subject="Phản hồi tự động — Email Scheduler",
            body_text=body,
        )
        log_event(agent="notification_agent", status=noti_result.get(
            "status"), payload=noti_result)
        return {"type": "send_email_flow", "data": {"email": email_result, "notification": noti_result}}
    except Exception as e:
        log_event(agent="orchestrator",
                  status="send_email_error", payload={"error": str(e)})
        return {"type": "send_email_flow", "data": {"email": email_result, "error": str(e)}}


async def _handle_reply_email(email, email_result: dict) -> dict:
    """Xử lý intent reply_email: trả lời email."""
    try:
        summary = email_result.get("summary", "")
        sender = getattr(email, "sender", "")
        log_event(agent="orchestrator",
                  status="reply_email_started", payload=email_result)

        # Gửi email xác nhận đã nhận yêu cầu trả lời email
        body = "Xin chào,\n\n"
        body += "Hệ thống đã nhận yêu cầu trả lời email của bạn.\n\n"
        if summary:
            body += f"Nội dung yêu cầu: {summary}\n\n"
        body += "Chức năng trả lời email đang được phát triển. "
        body += "Tính năng đầy đủ sẽ sớm được triển khai.\n\n"
        body += "Trân trọng,\nEmail Scheduler AI"

        noti_result = send_reply(
            to_email=sender,
            subject="Phản hồi tự động — Email Scheduler",
            body_text=body,
        )
        log_event(agent="notification_agent", status=noti_result.get(
            "status"), payload=noti_result)
        return {"type": "reply_email_flow", "data": {"email": email_result, "notification": noti_result}}
    except Exception as e:
        log_event(agent="orchestrator",
                  status="reply_email_error", payload={"error": str(e)})
        return {"type": "reply_email_flow", "data": {"email": email_result, "error": str(e)}}


async def _send_fallback_notification(email) -> dict:
    """
    Gửi email phản hồi fallback khi không thể phân tích email.
    """
    fallback_body = (
        "Xin chào,\n\n"
        "Hệ thống Email Scheduler đã nhận được email của bạn "
        "nhưng không thể xác định yêu cầu cụ thể.\n\n"
        "Vui lòng gửi lại email với nội dung rõ ràng hơn "
        "(ví dụ: đặt lịch họp, dời lịch, soạn email, hoặc "
        "hỏi về lịch trống).\n\n"
        "Trân trọng,\n"
        "Email Scheduler AI"
    )
    return send_reply(
        to_email=getattr(email, "sender", ""),
        subject="Phản hồi tự động — Email Scheduler",
        body_text=fallback_body,
    )


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

    elif intent == "send_email":
        # Compose and send a new email based on user request
        return await _handle_send_email(email, email_result)

    elif intent == "reply_email":
        # Reply to an email thread based on user request
        return await _handle_reply_email(email, email_result)

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
        # "other" intent — email không liên quan lịch họp
        # → phân tích bằng Email Intelligence Agent
        try:
            intelligence_result = classify_intelligence(email)
            log_event(agent="email_intelligence_agent",
                      status=intelligence_result.get("category", "other"),
                      payload=intelligence_result)

            # ── Lưu kết quả phân tích vào SQLite ──────────────────────────
            extracted_data = intelligence_result.get("extracted_data", {})
            extracted_data_json_str = json.dumps(
                extracted_data, ensure_ascii=False)

            category = intelligence_result.get("category", "other")
            importance_score = intelligence_result.get("importance_score", 30)

            logger.info(
                "[EmailIntelligence] Saving analysis | category=%s | score=%d | db=%s",
                category, importance_score, "logs.db",
            )

            try:
                row_id = insert_email_analysis(
                    email_id=None,
                    sender=getattr(email, "sender", ""),
                    subject=getattr(email, "subject", ""),
                    category=category,
                    summary=intelligence_result.get("summary", ""),
                    extracted_data_json=extracted_data_json_str,
                    importance_score=importance_score,
                )
                logger.info(
                    "[EmailIntelligence] Saved row_id=%d | category=%s",
                    row_id, category,
                )
            except Exception as db_err:
                logger.error(
                    "[EmailIntelligence] DB Error: %s: %s | category=%s | sender=%s",
                    type(db_err).__name__, db_err,
                    category, getattr(email, "sender", ""),
                )
                log_event(agent="email_intelligence_agent",
                          status="db_error",
                          payload={
                              "error_type": type(db_err).__name__,
                              "error": str(db_err),
                              "category": category,
                              "sender": getattr(email, "sender", ""),
                          })

            # ── Gửi email phản hồi với kết quả phân tích ──────────────────
            noti_result = _send_intelligence_notification(
                email, intelligence_result)
            log_event(agent="notification_agent", status=noti_result.get(
                "status"), payload=noti_result)
            log_event(agent="orchestrator",
                      status="other_analyzed", payload={"category": intelligence_result.get("category")})
            return {
                "type": "other_flow",
                "data": {
                    "email": email_result,
                    "intelligence": intelligence_result,
                    "notification": noti_result,
                },
            }
        except Exception as e:
            log_event(agent="orchestrator",
                      status="other_error", payload={"error": str(e)})
            # Gửi email phản hồi cố định khi intelligence agent lỗi
            try:
                noti_result = await _send_fallback_notification(email)
                return {"type": "other_flow", "data": {"email": email_result, "notification": noti_result}}
            except Exception:
                return {"type": "other_flow", "data": {"email": email_result, "notification": {"status": "error", "message": str(e)}}}
