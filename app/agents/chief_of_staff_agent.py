"""
ChiefOfStaffAgent — reasoning and planning only.

Design constraints:
  - NEVER sends emails
  - NEVER creates or modifies calendar events
  - NEVER writes to the database
  - Only reads from existing DB query functions and Google Calendar

Public surface:
  gather_context(last_view)              → context dict consumed by all skills
  executive_briefing_skill(context)      → {"bullets": [...], "generated_at": str, "meta": {...}}
  risk_detection_skill(context)          → {"risks": [...], "generated_at": str}
  priority_recommendation_skill(context) → {"priorities": [...], "generated_at": str}
  waiting_response_skill(context)        → {"waiting": [...], "summary": {...}, "generated_at": str}
  deadline_intelligence_skill(context)   → {"deadlines": [...], "overdue": [...], "generated_at": str}
  productivity_insight_skill(context)    → {"metrics": {...}, "insights": [...], "generated_at": str}
"""

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))


# ── Private helpers ───────────────────────────────────────────────────────────


def _parse_iso_safe(dt_str: str) -> datetime | None:
    """Parse an ISO-8601 string to a timezone-aware datetime. Returns None on failure."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(ICT)
    except Exception:
        return None


def _days_since(created_at_str: str) -> int:
    """Return calendar days elapsed since an ISO-8601 datetime string. Returns 0 on failure."""
    dt = _parse_iso_safe(created_at_str)
    if dt is None:
        return 0
    return max(0, (datetime.now(ICT).date() - dt.date()).days)


def _urgency_from_days(days_remaining: int) -> str:
    """Map days_remaining to an urgency label."""
    if days_remaining < 0:
        return "overdue"
    if days_remaining == 0:
        return "critical"
    if days_remaining <= 2:
        return "high"
    if days_remaining <= 7:
        return "medium"
    return "low"


def _llm_call(prompt: str, skill_name: str, max_tokens: int = 500) -> dict:
    """Shared OpenAI call. Returns parsed JSON dict, or {} on failure."""
    from openai import OpenAI
    from app.core.config import settings

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.error("[ChiefOfStaff] %s LLM call failed: %s", skill_name, exc)
        return {}


# ── Context gathering ─────────────────────────────────────────────────────────


def gather_context(last_view: str | None) -> dict:
    """
    Collect all system-state data needed by ChiefOfStaff skills.

    Phase 2 fix: `waiting_for_reply` now correctly fetches from the
    `pending_actions` table via list_pending_actions(status="waiting_external_reply").
    The old approach filtered get_pending_actions() items for that status,
    which always returned an empty list because that table doesn't carry it.

    Args:
        last_view: ISO-8601 timestamp of the user's last dashboard view, or None.
    """
    from app.db.sqlite import (
        get_pending_actions,
        get_top_important_emails,
        get_email_statistics_since,
        list_pending_actions,
        get_sent_emails,
        get_log_stats,
        get_dashboard_summary,
    )
    from app.agents.chat_agent import _fetch_upcoming_events

    since_fallback = last_view or "2000-01-01T00:00:00"

    # Combined queue: email_insights (action_required) + pending_invites + pending_reschedules
    pending_data = get_pending_actions(page=1, page_size=100)
    items = pending_data.get("items", [])

    # Email stats and top emails since last view
    email_stats = get_email_statistics_since(since=last_view)
    top_emails = get_top_important_emails(since=since_fallback, top_n=5)

    # Calendar
    upcoming = _fetch_upcoming_events(range_days=7) or []

    # FIXED: actual waiting_external_reply rows from pending_actions table
    waiting_data = list_pending_actions(status="waiting_external_reply", page=1, page_size=100)
    waiting_for_reply = waiting_data.get("items", [])

    # Active pending_actions rows that need user action
    active_data = list_pending_actions(page=1, page_size=100)
    active_actions = [
        a for a in active_data.get("items", [])
        if a["status"] in ("pending", "draft_ready", "waiting_send_confirmation")
    ]

    # Productivity data
    sent_stats = get_sent_emails(page=1, page_size=1)
    log_stats = get_log_stats()
    kpis = get_dashboard_summary()

    # Legacy alias so risk_detection_skill (Phase 1) keeps working
    action_required = [a for a in items if a.get("source") != "pending_actions"]

    return {
        # ── existing keys ──────────────────────────────────────────────────
        "last_view":      last_view,
        "items":          items,
        "action_required": action_required,
        "email_stats":    email_stats,
        "top_emails":     top_emails,
        "upcoming":       upcoming,
        # ── fixed / new ────────────────────────────────────────────────────
        "waiting_for_reply": waiting_for_reply,
        "waiting_reply":     waiting_for_reply,   # backward-compat alias
        "active_actions":    active_actions,
        "sent_total":        sent_stats.get("total", 0),
        "log_stats":         log_stats,
        "kpis":              kpis,
    }


# ── Skills ────────────────────────────────────────────────────────────────────


def executive_briefing_skill(context: dict) -> dict:
    """
    Generate an AI executive briefing in Vietnamese (3–7 actionable bullet points).
    Uses GPT-4o-mini.
    """
    # Use active_actions (pending_actions table) — same source as the Pending Actions widget.
    # context["action_required"] reads email_insights (legacy table) and becomes stale
    # after HITL actions complete without marking email_insights.is_read.
    action_required = context["active_actions"]
    waiting_reply   = context["waiting_for_reply"]
    email_stats     = context["email_stats"]
    top_emails      = context["top_emails"]
    upcoming        = context["upcoming"]

    generated_at = datetime.now(ICT).isoformat()
    logger.info(
        "[ExecutiveBriefing] pending_action_count=%d waiting_reply_count=%d generated_at=%s",
        len(action_required),
        len(waiting_reply),
        generated_at,
    )

    top_email_text = "\n".join(
        f'  - [{e["category"]}] {e["sender"]}: {e["subject"]} — {e["summary"]}'
        for e in top_emails
    ) or "  (không có)"

    upcoming_text = "\n".join(
        f'  - {ev.get("summary", "Không tiêu đề")} lúc {ev.get("start", "")}'
        for ev in upcoming[:5]
    ) or "  (không có sự kiện)"

    prompt = (
        "Bạn là trợ lý AI của ứng dụng quản lý lịch email. "
        "Hãy tóm tắt tình hình hiện tại bằng tiếng Việt, dưới dạng 3-7 gạch đầu dòng ngắn gọn. "
        "Tập trung vào thông tin có thể hành động, giải thích ý nghĩa thay vì chỉ đếm số liệu.\n\n"
        "Dữ liệu hiện tại:\n"
        f"- Email cần xử lý ngay: {len(action_required)}\n"
        f"- Đang chờ phản hồi từ người khác: {len(waiting_reply)}\n"
        f"- Email mới từ lần xem gần nhất: {email_stats.get('total', 0)} "
        f"(họp: {email_stats.get('meeting', 0)}, báo cáo: {email_stats.get('report', 0)}, "
        f"hợp tác: {email_stats.get('partnership', 0)}, hỗ trợ: {email_stats.get('support', 0)}, "
        f"thông báo: {email_stats.get('announcement', 0)})\n"
        f"- Email quan trọng nhất:\n{top_email_text}\n"
        f"- Sự kiện sắp tới (7 ngày tới):\n{upcoming_text}\n\n"
        "Yêu cầu:\n"
        "- Giải thích ý nghĩa, không chỉ đếm số\n"
        "- Đề cập deadline, cuộc họp sắp tới nếu có\n"
        "- Giọng điệu thân thiện, chuyên nghiệp\n"
        '- Trả về JSON: {"bullets": ["nội dung 1", "nội dung 2", ...]}'
    )

    result = _llm_call(prompt, "executive_briefing_skill", max_tokens=600)
    logger.info(
        "[ExecutiveBriefing] summary generated pending_count=%d bullets=%d",
        len(action_required),
        len(result.get("bullets", [])),
    )
    return {
        "bullets": result.get("bullets", []),
        "generated_at": generated_at,
        "meta": {
            "action_required": len(action_required),
            "waiting_reply":   len(waiting_reply),
            "upcoming_count":  len(upcoming),
        },
    }


def risk_detection_skill(context: dict) -> dict:
    """
    Rule-based risk detection — no LLM required.

    Detects:
      - Overdue external replies (>= 5 days → warning; >= 7 → critical)
      - Stale unresolved actions (>= 3 days → warning)
    """
    now = datetime.now(ICT)
    risks = []

    for action in context["waiting_for_reply"]:
        created_str = action.get("created_at") or ""
        dt = _parse_iso_safe(created_str)
        if dt is None:
            continue
        waiting_days = (now.date() - dt.date()).days
        if waiting_days >= 5:
            risks.append({
                "type":        "overdue_reply",
                "description": (
                    f"{action.get('sender', '?')} chưa phản hồi sau {waiting_days} ngày"
                    f" (chủ đề: {action.get('subject', '?')})"
                ),
                "severity": "critical" if waiting_days >= 7 else "warning",
            })

    for action in context["action_required"]:
        created_str = action.get("created_at") or ""
        dt = _parse_iso_safe(created_str)
        if dt is None:
            continue
        pending_days = (now.date() - dt.date()).days
        if pending_days >= 3:
            risks.append({
                "type":        "stale_pending",
                "description": (
                    f"Email từ {action.get('sender', '?')} đang chờ xử lý {pending_days} ngày"
                ),
                "severity": "warning",
            })

    return {
        "risks":        risks,
        "generated_at": datetime.now(ICT).isoformat(),
    }


def priority_recommendation_skill(context: dict) -> dict:
    """
    Answer "What should I do next?" — ranked priorities with AI-generated reasons.

    Pre-sorts candidates by action type and age, then sends top 5 to GPT-4o-mini
    to produce natural-language title + reason for each item.
    """
    now = datetime.now(ICT)

    # Build candidate pool from all action sources
    candidates: list[dict] = []

    # Source 1: active pending_actions (needs user's decision)
    for action in context["active_actions"]:
        age_days = _days_since(action.get("created_at", ""))
        atype = action.get("action_type", "")
        base_score = 100 if atype in ("meeting_request", "meeting_cancel") else 60
        candidates.append({
            "source":      "pending_action",
            "title_hint":  f"{action.get('subject', '?')} (từ {action.get('sender', '?')})",
            "action_type": atype,
            "age_days":    age_days,
            "score":       base_score + age_days * 3,
        })

    # Source 2: combined queue items (email_insights + invites + reschedules)
    for item in context["items"]:
        age_days = _days_since(item.get("created_at", ""))
        priority_text = (item.get("priority") or "").lower()
        base_score = 80 if priority_text == "high" else 50
        candidates.append({
            "source":      item.get("source", "email"),
            "title_hint":  f"{item.get('subject', '?')} (từ {item.get('sender', '?')})",
            "action_type": item.get("action_type", "reply_required"),
            "age_days":    age_days,
            "score":       base_score + age_days * 2,
        })

    # Source 3: waiting replies stale >= 5 days → add as "follow-up" candidates
    for action in context["waiting_for_reply"]:
        age_days = _days_since(action.get("created_at", ""))
        if age_days >= 5:
            candidates.append({
                "source":      "waiting_reply",
                "title_hint":  f"Nhắc lại với {action.get('sender', '?')} về: {action.get('subject', '?')}",
                "action_type": "follow_up",
                "age_days":    age_days,
                "score":       70 + age_days * 4,
            })

    # Source 4: meetings today or tomorrow
    for ev in context["upcoming"]:
        start_str = ev.get("start") or ""
        ev_dt = _parse_iso_safe(start_str)
        if ev_dt and (ev_dt.date() - now.date()).days <= 1:
            candidates.append({
                "source":      "calendar",
                "title_hint":  f"Cuộc họp: {ev.get('summary', 'Không tiêu đề')} lúc {start_str[:16]}",
                "action_type": "meeting_today",
                "age_days":    0,
                "score":       120,
            })

    # Deduplicate by title_hint and take top 5 by score
    seen: set[str] = set()
    top: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        key = c["title_hint"]
        if key not in seen:
            seen.add(key)
            top.append(c)
        if len(top) >= 5:
            break

    if not top:
        return {"priorities": [], "generated_at": datetime.now(ICT).isoformat()}

    prompt = (
        "Bạn là ChiefOfStaff AI. Tạo danh sách ưu tiên công việc hôm nay bằng tiếng Việt.\n\n"
        "Dữ liệu đầu vào:\n"
        f"{json.dumps(top, ensure_ascii=False, indent=2)}\n\n"
        "Với mỗi mục, tạo:\n"
        "- title: tên công việc ngắn gọn (dưới 10 từ)\n"
        "- reason: lý do tại sao cần làm ngay (1 câu, cụ thể)\n"
        "- priority: \"high\" | \"medium\" | \"low\"\n\n"
        "Trả về JSON: {\"priorities\": [{\"title\": str, \"reason\": str, \"priority\": str, \"action_type\": str}]}"
    )

    result = _llm_call(prompt, "priority_recommendation_skill", max_tokens=500)
    priorities = result.get("priorities", [])

    # Fallback: if LLM failed, build plain items from candidates
    if not priorities:
        priorities = [
            {
                "title":       c["title_hint"],
                "reason":      f"Chờ xử lý {c['age_days']} ngày.",
                "priority":    "high" if c["score"] >= 100 else "medium",
                "action_type": c["action_type"],
            }
            for c in top
        ]

    return {
        "priorities":   priorities,
        "generated_at": datetime.now(ICT).isoformat(),
    }


def waiting_response_skill(context: dict) -> dict:
    """
    Analyze waiting_external_reply items — no LLM required.

    Annotates each item with waiting_days, status_label, and follow_up_recommended.
    """
    now = datetime.now(ICT)
    annotated: list[dict] = []
    counts = {"critical": 0, "warning": 0, "normal": 0}

    for action in context["waiting_for_reply"]:
        dt = _parse_iso_safe(action.get("created_at") or "")
        waiting_days = (now.date() - dt.date()).days if dt else 0

        if waiting_days >= 7:
            label = "critical"
            follow_up = True
        elif waiting_days >= 5:
            label = "warning"
            follow_up = True
        else:
            label = "normal"
            follow_up = False

        counts[label] += 1
        annotated.append({
            "id":                   action.get("id"),
            "sender":               action.get("sender", ""),
            "subject":              action.get("subject", ""),
            "waiting_days":         waiting_days,
            "status_label":         label,
            "follow_up_recommended": follow_up,
            "created_at":           action.get("created_at", ""),
        })

    annotated.sort(key=lambda x: x["waiting_days"], reverse=True)

    return {
        "waiting": annotated,
        "summary": {
            "total":    len(annotated),
            "critical": counts["critical"],
            "warning":  counts["warning"],
            "normal":   counts["normal"],
        },
        "generated_at": datetime.now(ICT).isoformat(),
    }


def deadline_intelligence_skill(context: dict) -> dict:
    """
    Aggregate detectable deadlines from emails and calendar — no LLM required.

    Sources:
      1. email_intelligence.extracted_data_json.deadline (from top_emails)
      2. Google Calendar events happening today or tomorrow (imminent)
    """
    now = datetime.now(ICT)
    deadlines: list[dict] = []

    # Source 1: email_intelligence extracted deadlines
    for email in context["top_emails"]:
        extracted_raw = email.get("extracted_data_json") or "{}"
        try:
            extracted = json.loads(extracted_raw) if isinstance(extracted_raw, str) else extracted_raw
            deadline_str = extracted.get("deadline")
            if not deadline_str:
                continue
            dl_dt = _parse_iso_safe(deadline_str)
            if dl_dt is None:
                continue
            days_remaining = (dl_dt.date() - now.date()).days
            deadlines.append({
                "source":         "email",
                "description":    f"{email.get('sender', '?')}: {email.get('subject', '?')}",
                "deadline_date":  deadline_str[:10],
                "days_remaining": days_remaining,
                "urgency":        _urgency_from_days(days_remaining),
            })
        except Exception:
            pass

    # Source 2: calendar events today or tomorrow → treat as imminent deadlines
    for ev in context["upcoming"]:
        start_str = ev.get("start") or ""
        ev_dt = _parse_iso_safe(start_str)
        if ev_dt is None:
            continue
        days_remaining = (ev_dt.date() - now.date()).days
        if days_remaining <= 1:
            deadlines.append({
                "source":         "calendar",
                "description":    ev.get("summary", "Cuộc họp"),
                "deadline_date":  start_str[:10],
                "days_remaining": days_remaining,
                "urgency":        "critical" if days_remaining <= 0 else "high",
            })

    deadlines.sort(key=lambda x: x["days_remaining"])

    overdue      = [d for d in deadlines if d["days_remaining"] < 0]
    upcoming_today = [d for d in deadlines if d["days_remaining"] == 0]

    return {
        "deadlines":      deadlines,
        "overdue":        overdue,
        "upcoming_today": upcoming_today,
        "generated_at":   datetime.now(ICT).isoformat(),
    }


# ── Executive intent classifier ───────────────────────────────────────────────


def classify_executive_intent(message: str) -> str | None:
    """
    Keyword-based classification for executive questions. Deterministic, zero LLM cost.

    Returns one of:
      "work_priorities"  — what should I do today?
      "waiting_response" — who hasn't replied yet?
      "deadlines"        — what deadlines are coming?
      "email_summary"    — summarise important emails
      "pending_meetings" — meetings needing confirmation
      "executive_overview" — general today overview

    Returns None if the message is not an executive question.
    """
    msg = message.lower()
    if any(kw in msg for kw in ["cần làm gì", "việc gì", "ưu tiên", "làm gì hôm nay"]):
        return "work_priorities"
    if any(kw in msg for kw in ["chờ phản hồi", "đang chờ", "ai chưa trả lời", "chưa phản hồi", "ai chưa reply"]):
        return "waiting_response"
    if any(kw in msg for kw in ["deadline", "hạn chót", "sắp đến hạn", "hết hạn", "sắp tới không"]):
        return "deadlines"
    if any(kw in msg for kw in ["tóm tắt email", "email quan trọng", "email nào quan trọng"]):
        return "email_summary"
    if any(kw in msg for kw in ["cuộc họp cần xác nhận", "họp chờ", "xác nhận không", "cần xác nhận"]):
        return "pending_meetings"
    if any(kw in msg for kw in ["tổng quan", "hôm nay có gì", "tình hình", "tóm tắt hôm nay"]):
        return "executive_overview"
    return None


# ── Answer formatters (no LLM — pure string composition) ─────────────────────


def _format_priorities(priorities_result: dict, risks_result: dict) -> str:
    priorities = priorities_result.get("priorities", [])
    risks = risks_result.get("risks", [])
    if not priorities and not risks:
        return "✅ Không có việc gì cần ưu tiên ngay lúc này."
    icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = ["📋 **Ưu tiên hôm nay:**\n"]
    for i, p in enumerate(priorities, 1):
        icon = icons.get(p.get("priority", "low"), "🟢")
        lines.append(f"{i}. {icon} **{p.get('title', '?')}** — {p.get('reason', '')}")
    if risks:
        lines.append("\n⚠️ **Rủi ro phát hiện:**")
        sev_icons = {"critical": "🔴", "warning": "🟡"}
        for r in risks[:3]:
            icon = sev_icons.get(r.get("severity", "warning"), "🟡")
            lines.append(f"  {icon} {r.get('description', '')}")
    return "\n".join(lines)


def _format_waiting(waiting_result: dict) -> str:
    waiting = waiting_result.get("waiting", [])
    summary = waiting_result.get("summary", {})
    if not waiting:
        return "✅ Không có email nào đang chờ phản hồi."
    status_icons = {"critical": "🔴", "warning": "🟡", "normal": "🟢"}
    lines = [f"⏳ **Đang chờ phản hồi từ {summary.get('total', len(waiting))} người:**\n"]
    for item in waiting[:5]:
        icon = status_icons.get(item.get("status_label", "normal"), "🟢")
        days = item.get("waiting_days", 0)
        note = " *(nên nhắc lại)*" if item.get("follow_up_recommended") else ""
        lines.append(
            f"  {icon} **{item.get('sender', '?')}** — \"{item.get('subject', '?')}\" — {days} ngày{note}"
        )
    if len(waiting) > 5:
        lines.append(f"\n  _...và {len(waiting) - 5} người khác_")
    return "\n".join(lines)


def _format_deadlines(deadline_result: dict) -> str:
    deadlines = deadline_result.get("deadlines", [])
    if not deadlines:
        return "✅ Không có deadline nào sắp tới."
    urgency_icons  = {"overdue": "🔴", "critical": "🔴", "high": "🟡", "medium": "🟠", "low": "🟢"}
    urgency_labels = {"overdue": "QUÁ HẠN", "critical": "HÔM NAY", "high": "≤2 ngày", "medium": "tuần này", "low": "sắp tới"}
    lines = ["⚠️ **Deadline sắp tới:**\n"]
    for d in deadlines[:7]:
        urg = d.get("urgency", "low")
        icon  = urgency_icons.get(urg, "🟢")
        label = urgency_labels.get(urg, "")
        src   = d.get("source", "?")
        lines.append(f"  {icon} [{label}] {d.get('description', '?')} _({src})_")
    return "\n".join(lines)


def _format_briefing(briefing_result: dict) -> str:
    bullets = briefing_result.get("bullets", [])
    if not bullets:
        return "🧠 Không có đủ dữ liệu để tạo tóm tắt."
    lines = ["🧠 **Tóm tắt tình hình:**\n"]
    for b in bullets:
        lines.append(f"  • {b}")
    return "\n".join(lines)


# ── Executive answer entry point ──────────────────────────────────────────────


def answer_executive_question(question: str, last_view: str | None = None) -> dict:
    """
    Route an executive-level question to the appropriate skill(s) and return
    a formatted chat-ready answer.

    Args:
        question: the user's natural language question
        last_view: ISO-8601 timestamp of last dashboard view (for email stats context)

    Returns:
        {"answer": str, "skills_used": list[str], "intent": str}
    """
    intent = classify_executive_intent(question) or "executive_overview"
    context = gather_context(last_view)

    answer = ""
    skills_used: list[str] = []

    if intent == "work_priorities":
        p = priority_recommendation_skill(context)
        r = risk_detection_skill(context)
        skills_used = ["priority_recommendation_skill", "risk_detection_skill"]
        answer = _format_priorities(p, r)

    elif intent == "waiting_response":
        w = waiting_response_skill(context)
        skills_used = ["waiting_response_skill"]
        answer = _format_waiting(w)

    elif intent == "deadlines":
        d = deadline_intelligence_skill(context)
        skills_used = ["deadline_intelligence_skill"]
        answer = _format_deadlines(d)

    elif intent == "email_summary":
        b = executive_briefing_skill(context)
        p = priority_recommendation_skill(context)
        skills_used = ["executive_briefing_skill", "priority_recommendation_skill"]
        parts = [_format_briefing(b)]
        priorities = p.get("priorities", [])
        if priorities:
            icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            parts.append("\n📋 **Việc cần xử lý:**\n")
            for i, pr in enumerate(priorities[:3], 1):
                icon = icons.get(pr.get("priority", "low"), "🟢")
                parts.append(f"  {i}. {icon} **{pr.get('title', '?')}** — {pr.get('reason', '')}")
        answer = "\n".join(parts)

    elif intent == "pending_meetings":
        p = priority_recommendation_skill(context)
        skills_used = ["priority_recommendation_skill"]
        meeting_types = {"meeting_request", "meeting_today", "meeting_cancel"}
        meeting_items = [
            pr for pr in p.get("priorities", [])
            if pr.get("action_type") in meeting_types
        ]
        if not meeting_items:
            answer = "✅ Không có cuộc họp nào cần xác nhận ngay lúc này."
        else:
            icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            lines = ["📅 **Cuộc họp cần xử lý:**\n"]
            for i, pr in enumerate(meeting_items, 1):
                icon = icons.get(pr.get("priority", "low"), "🟢")
                lines.append(f"  {i}. {icon} **{pr.get('title', '?')}** — {pr.get('reason', '')}")
            answer = "\n".join(lines)

    else:  # executive_overview (default)
        b = executive_briefing_skill(context)
        skills_used = ["executive_briefing_skill"]
        answer = _format_briefing(b)

    logger.info(
        "[ChiefOfStaff] answer_executive_question | intent=%s | skills=%s",
        intent, skills_used,
    )
    return {"answer": answer, "skills_used": skills_used, "intent": intent}


def productivity_insight_skill(context: dict) -> dict:
    """
    Generate workload metrics + AI narrative observations.
    Uses GPT-4o-mini once.
    """
    kpis    = context["kpis"]
    metrics = {
        "emails_processed":   kpis.get("emails_processed", 0),
        "emails_sent":        context["sent_total"],
        "meetings_scheduled": kpis.get("meetings_scheduled", 0),
        "pending_workload":   kpis.get("pending_actions", 0),
        "errors":             kpis.get("errors", 0),
        "meetings_this_week": len(context["upcoming"]),
        "waiting_replies":    len(context["waiting_for_reply"]),
        "pipeline_events":    context["log_stats"].get("total", 0),
    }

    prompt = (
        "Bạn là ChiefOfStaff AI. Phân tích hiệu suất làm việc và tạo 2-4 nhận xét ngắn gọn bằng tiếng Việt.\n\n"
        f"Chỉ số:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        "Yêu cầu:\n"
        "- Nhận xét về xu hướng, không chỉ liệt kê số liệu\n"
        "- Đề xuất cải thiện nếu có vấn đề (workload cao, nhiều lỗi, chờ phản hồi lâu...)\n"
        "- Tông điệu: khách quan, chuyên nghiệp\n"
        '- Trả về JSON: {"insights": [{"observation": str, "type": "positive"|"neutral"|"warning"}]}'
    )

    result = _llm_call(prompt, "productivity_insight_skill", max_tokens=400)
    insights = result.get("insights", [])

    # Fallback: rule-based observations if LLM failed
    if not insights:
        if metrics["pending_workload"] > 5:
            insights.append({"observation": f"Còn {metrics['pending_workload']} công việc đang chờ xử lý.", "type": "warning"})
        if metrics["waiting_replies"] > 0:
            insights.append({"observation": f"Đang chờ phản hồi từ {metrics['waiting_replies']} người.", "type": "neutral"})
        if not insights:
            insights.append({"observation": "Không có dữ liệu đủ để phân tích hiệu suất.", "type": "neutral"})

    return {
        "metrics":      metrics,
        "insights":     insights,
        "generated_at": datetime.now(ICT).isoformat(),
    }
