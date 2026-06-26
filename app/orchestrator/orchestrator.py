import json
import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.agents.email_agent import process_email, priority_scoring_skill
from app.agents.calendar_agent import (
    check_calendar_availability,
    check_reschedule_availability,
)
from app.agents.email_intelligence_agent import process_email as classify_intelligence
from app.db.sqlite import insert_email_analysis, insert_email_insight, create_pending_action
from app.core.logger import log_event

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.5


# ── Pipeline state ─────────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    email: Any
    email_result: dict
    insight_id: Optional[int]
    intent: str
    confidence: float
    calendar_result: dict
    action_id: Optional[int]
    output: dict


# ── Helpers (unchanged from original) ─────────────────────────────────────────

def _build_schedule_recommendation(calendar_result: dict) -> str:
    from datetime import datetime
    status = calendar_result.get("status")
    time_str = calendar_result.get("start", "")
    try:
        dt = datetime.fromisoformat(time_str)
        time_label = dt.strftime("%H:%M %d/%m/%Y")
    except (ValueError, TypeError):
        time_label = time_str
    if status == "free":
        return f"Chấp nhận: Khung giờ {time_label} trống lịch"
    if status == "conflict":
        return f"Bận: {time_label} đã có lịch — nên đề xuất giờ khác"
    return "Kiểm tra lịch thất bại — xem lại thủ công"


def _build_reschedule_recommendation(calendar_result: dict) -> str:
    from datetime import datetime
    status = calendar_result.get("status")
    time_str = calendar_result.get("start", "")
    try:
        dt = datetime.fromisoformat(time_str)
        time_label = dt.strftime("%H:%M %d/%m/%Y")
    except (ValueError, TypeError):
        time_label = time_str
    if status == "free":
        return f"Chấp nhận dời lịch sang {time_label}"
    if status == "conflict":
        return f"Bận: {time_label} đã có lịch — nên đề xuất giờ khác"
    if status == "not_found":
        return "Không tìm thấy lịch cũ — kiểm tra thủ công"
    return "Kiểm tra dời lịch thất bại — xem lại thủ công"


def _store_email_insight(email, email_result: dict) -> int | None:
    """
    Store every incoming email as an email_insight record with AI enrichment.
    Returns the new row id on success, or None on failure.
    """
    gmail_message_id = email_result.get(
        "gmail_message_id") or getattr(email, "gmail_message_id", None)
    try:
        row_id = insert_email_insight(
            gmail_message_id=gmail_message_id,
            sender=getattr(email, "sender", ""),
            subject=getattr(email, "subject", ""),
            category=email_result.get("category", "Other"),
            summary=email_result.get("summary", ""),
            priority=email_result.get("priority", "Low"),
            action_required=email_result.get("action_required", False),
            important_note=email_result.get("important_note"),
            body=getattr(email, "body", None),
            thread_id=getattr(email, "thread_id", None),
            sentiment=email_result.get("sentiment"),
            detected_language=email_result.get("detected_language"),
            priority_score=priority_scoring_skill(email_result),
        )
        logger.info("[Orchestrator] Stored email_insight row_id=%d | sender=%s",
                    row_id, getattr(email, "sender", ""))
        return row_id
    except Exception as db_err:
        logger.error(
            "[Orchestrator] Failed to store email_insight: %s: %s | sender=%s",
            type(db_err).__name__, db_err, getattr(email, "sender", ""),
        )
        return None


# ── Node functions ─────────────────────────────────────────────────────────────

def node_classify_email(state: PipelineState) -> dict:
    email = state["email"]
    email_result = process_email(email)
    log_event(agent="email_agent", status=email_result.get("intent", "unknown"),
              payload=email_result)
    insight_id = _store_email_insight(email, email_result)
    return {
        "email_result": email_result,
        "insight_id": insight_id,
        "intent": email_result.get("intent", "other"),
        "confidence": float(email_result.get("confidence") or 0.0),
    }


def node_low_confidence(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]
    confidence = state["confidence"]
    insight_id = state["insight_id"]
    sender = getattr(email, "sender", "")
    subject = getattr(email, "subject", "")
    gmail_id = getattr(email, "gmail_message_id", None)
    thread_id = getattr(email, "thread_id", None)

    action_id = create_pending_action(
        action_type="unclear",
        sender=sender,
        subject=subject,
        summary=email_result.get("summary"),
        confidence=confidence,
        options={"available": ["ignore"]},
        gmail_message_id=gmail_id,
        email_insight_id=insight_id,
        thread_id=thread_id,
    )
    log_event(agent="orchestrator", status="low_confidence_pending",
              payload={"confidence": confidence, "action_id": action_id})
    logger.info("[Orchestrator] Low confidence %.2f – pending_action id=%d",
                confidence, action_id)
    return {
        "action_id": action_id,
        "output": {"type": "unclear_flow", "data": {"email": email_result, "action_id": action_id}},
    }


def node_schedule(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]
    confidence = state["confidence"]
    insight_id = state["insight_id"]
    sender = getattr(email, "sender", "")
    subject = getattr(email, "subject", "")
    gmail_id = getattr(email, "gmail_message_id", None)
    thread_id = getattr(email, "thread_id", None)

    calendar_result = check_calendar_availability(email_result)
    log_event(agent="calendar_agent", status=calendar_result.get("status"),
              payload=calendar_result)
    recommendation = _build_schedule_recommendation(calendar_result)
    action_id = create_pending_action(
        action_type="meeting_request",
        sender=sender,
        subject=subject,
        summary=email_result.get("summary"),
        recommendation=recommendation,
        confidence=confidence,
        options={"available": ["accept", "reject", "suggest_new_time"]},
        calendar_result=calendar_result,
        gmail_message_id=gmail_id,
        email_insight_id=insight_id,
        thread_id=thread_id,
    )
    log_event(agent="orchestrator", status="schedule_pending", payload={
        "calendar_status": calendar_result.get("status"),
        "action_id": action_id,
    })
    logger.info("[Orchestrator] meeting_request pending_action id=%d | calendar=%s",
                action_id, calendar_result.get("status"))
    return {
        "calendar_result": calendar_result,
        "action_id": action_id,
        "output": {"type": "schedule_flow", "data": {
            "email": email_result, "calendar": calendar_result, "action_id": action_id,
        }},
    }


def node_reschedule(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]
    confidence = state["confidence"]
    insight_id = state["insight_id"]
    sender = getattr(email, "sender", "")
    subject = getattr(email, "subject", "")
    gmail_id = getattr(email, "gmail_message_id", None)
    thread_id = getattr(email, "thread_id", None)

    calendar_result = check_reschedule_availability(email_result)
    log_event(agent="calendar_agent", status=calendar_result.get("status"),
              payload=calendar_result)
    recommendation = _build_reschedule_recommendation(calendar_result)
    action_id = create_pending_action(
        action_type="meeting_reschedule",
        sender=sender,
        subject=subject,
        summary=email_result.get("summary"),
        recommendation=recommendation,
        confidence=confidence,
        options={"available": ["accept", "reject", "suggest_new_time"]},
        calendar_result=calendar_result,
        gmail_message_id=gmail_id,
        email_insight_id=insight_id,
        thread_id=thread_id,
    )
    log_event(agent="orchestrator", status="reschedule_pending", payload={
        "calendar_status": calendar_result.get("status"),
        "action_id": action_id,
    })
    logger.info("[Orchestrator] meeting_reschedule pending_action id=%d | calendar=%s",
                action_id, calendar_result.get("status"))
    return {
        "calendar_result": calendar_result,
        "action_id": action_id,
        "output": {"type": "reschedule_flow", "data": {
            "email": email_result, "calendar": calendar_result, "action_id": action_id,
        }},
    }


def node_cancel(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]
    confidence = state["confidence"]
    insight_id = state["insight_id"]
    sender = getattr(email, "sender", "")
    subject = getattr(email, "subject", "")
    gmail_id = getattr(email, "gmail_message_id", None)
    thread_id = getattr(email, "thread_id", None)

    action_id = create_pending_action(
        action_type="meeting_cancel",
        sender=sender,
        subject=subject,
        summary=email_result.get("summary"),
        confidence=confidence,
        options={"available": ["accept", "reject"]},
        gmail_message_id=gmail_id,
        email_insight_id=insight_id,
        thread_id=thread_id,
    )
    log_event(agent="orchestrator", status="cancel_pending",
              payload={"action_id": action_id})
    logger.info("[Orchestrator] meeting_cancel pending_action id=%d", action_id)
    return {
        "action_id": action_id,
        "output": {"type": "cancel_flow", "data": {"email": email_result, "action_id": action_id}},
    }


def node_reply_required(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]
    intent = state["intent"]
    confidence = state["confidence"]
    insight_id = state["insight_id"]
    sender = getattr(email, "sender", "")
    subject = getattr(email, "subject", "")
    gmail_id = getattr(email, "gmail_message_id", None)
    thread_id = getattr(email, "thread_id", None)

    action_id = create_pending_action(
        action_type="reply_required",
        sender=sender,
        subject=subject,
        summary=email_result.get("summary"),
        confidence=confidence,
        options={"available": ["generate_reply"]},
        gmail_message_id=gmail_id,
        email_insight_id=insight_id,
        thread_id=thread_id,
    )
    log_event(agent="orchestrator", status="reply_required_pending",
              payload={"intent": intent, "action_id": action_id})
    logger.info("[Orchestrator] reply_required pending_action id=%d | intent=%s",
                action_id, intent)
    return {
        "action_id": action_id,
        "output": {"type": "reply_required_flow", "data": {"email": email_result, "action_id": action_id}},
    }


def node_other(state: PipelineState) -> dict:
    email = state["email"]
    email_result = state["email_result"]

    try:
        intelligence_result = classify_intelligence(email)
        log_event(agent="email_intelligence_agent",
                  status=intelligence_result.get("category", "other"),
                  payload=intelligence_result)

        extracted_data = intelligence_result.get("extracted_data", {})
        extracted_data_json_str = json.dumps(extracted_data, ensure_ascii=False)
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
            logger.info("[EmailIntelligence] Saved row_id=%d | category=%s", row_id, category)
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

        log_event(agent="orchestrator", status="other_analyzed", payload={
            "category": intelligence_result.get("category"),
            "importance_score": importance_score,
        })
        return {
            "output": {"type": "other_flow", "data": {
                "email": email_result,
                "intelligence": intelligence_result,
            }},
        }
    except Exception as e:
        log_event(agent="orchestrator", status="other_error", payload={"error": str(e)})
        return {
            "output": {"type": "other_flow", "data": {"email": email_result, "error": str(e)}},
        }


# ── Routing function ───────────────────────────────────────────────────────────

def route_by_intent(state: PipelineState) -> str:
    if state["confidence"] < LOW_CONFIDENCE_THRESHOLD:
        return "low_confidence"
    intent = state["intent"]
    if intent == "schedule":
        return "schedule"
    if intent == "reschedule":
        return "reschedule"
    if intent == "cancel":
        return "cancel"
    if intent in ("send_email", "reply_email", "inquiry"):
        return "reply_required"
    return "other"


# ── Graph construction ─────────────────────────────────────────────────────────

_graph = StateGraph(PipelineState)

_graph.add_node("classify_email", node_classify_email)
_graph.add_node("low_confidence", node_low_confidence)
_graph.add_node("schedule", node_schedule)
_graph.add_node("reschedule", node_reschedule)
_graph.add_node("cancel", node_cancel)
_graph.add_node("reply_required", node_reply_required)
_graph.add_node("other", node_other)

_graph.set_entry_point("classify_email")
_graph.add_conditional_edges(
    "classify_email",
    route_by_intent,
    {
        "low_confidence": "low_confidence",
        "schedule":       "schedule",
        "reschedule":     "reschedule",
        "cancel":         "cancel",
        "reply_required": "reply_required",
        "other":          "other",
    },
)
_graph.add_edge("low_confidence", END)
_graph.add_edge("schedule",       END)
_graph.add_edge("reschedule",     END)
_graph.add_edge("cancel",         END)
_graph.add_edge("reply_required", END)
_graph.add_edge("other",          END)

pipeline_graph = _graph.compile()


# ── Public entry point ─────────────────────────────────────────────────────────

async def run_pipeline(email) -> dict:
    """
    Classify the incoming email and route to the appropriate handler.

    HITL contract: this pipeline never creates calendar events, sends emails,
    or modifies any external state.  It only stores pending_actions rows so
    the user can review and confirm each action via the Dashboard.

    Every email is always stored in email_insights with AI enrichment.
    """
    initial_state: PipelineState = {
        "email":          email,
        "email_result":   {},
        "insight_id":     None,
        "intent":         "other",
        "confidence":     0.0,
        "calendar_result": {},
        "action_id":      None,
        "output":         {},
    }
    final_state = await pipeline_graph.ainvoke(initial_state)
    return final_state["output"]
