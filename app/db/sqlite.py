import json
import sqlite3
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings

# ── Resolve database path to an absolute, deterministic location ─────────────
# Always resolve relative to the project root (parent of app/) so the same
# database file is used regardless of the current working directory.
# Special SQLite paths (":memory:", "") are left unchanged.
_db_raw = settings.DATABASE_PATH
if _db_raw == ":memory:" or _db_raw == "":
    DB_NAME = _db_raw
else:
    _db_path = Path(_db_raw)
    if not _db_path.is_absolute():
        # app/db/sqlite.py → parent.parent.parent = project root
        _project_root = Path(__file__).resolve().parent.parent.parent
        _db_path = (_project_root / _db_path).resolve()
    DB_NAME = str(_db_path)

_logger = logging.getLogger(__name__)


def get_connection():
    return sqlite3.connect(DB_NAME)


def _verify_tables():
    """Log the absolute database path and list all user tables."""
    _logger.info("Database Path: %s", DB_NAME)
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]
        _logger.info("Tables: %s", ", ".join(tables) if tables else "(none)")
        conn.close()
    except Exception as exc:
        _logger.warning("Could not list tables: %s", exc)


def _require_table(table_name: str, create_sql: str):
    """Ensure *table_name* exists; create it via *create_sql* if missing."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cur.fetchone() is None:
            _logger.warning("Table '%s' is missing – creating now", table_name)
            cur.execute(create_sql)
            conn.commit()
        conn.close()
    except Exception as exc:
        _logger.error(
            "Failed to verify/create table '%s': %s", table_name, exc
        )


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_logs (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent TEXT,
        status TEXT,
        payload TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_invites (
        token TEXT PRIMARY KEY,
        action TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_reschedules (
        token TEXT PRIMARY KEY,
        action TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_id TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        name TEXT,
        picture_url TEXT,
        google_access_token TEXT,
        google_refresh_token TEXT,
        token_expiry TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_dashboard_view_at TEXT NULL
    )
    """)

    # Migration: add user_id column to system_logs if it doesn't exist
    cur.execute("PRAGMA table_info(system_logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "user_id" not in columns:
        cur.execute("ALTER TABLE system_logs ADD COLUMN user_id INTEGER")

    # Migration: add last_dashboard_view_at column to users if it doesn't exist
    cur.execute("PRAGMA table_info(users)")
    user_columns = [row[1] for row in cur.fetchall()]
    if "last_dashboard_view_at" not in user_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN last_dashboard_view_at TEXT NULL")

    # ── Email Intelligence table ───────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_id TEXT,
        sender TEXT NOT NULL,
        subject TEXT,
        category TEXT NOT NULL,
        summary TEXT,
        extracted_data_json TEXT DEFAULT '{}',
        importance_score INTEGER DEFAULT 50,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── Email Insight table ─────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmail_message_id TEXT,
        sender TEXT NOT NULL,
        subject TEXT,
        summary TEXT,
        category TEXT NOT NULL,
        priority TEXT,
        action_required INTEGER DEFAULT 0,
        important_note TEXT,
        is_read INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Migration: add columns to email_insights if they don't exist
    cur.execute("PRAGMA table_info(email_insights)")
    insight_cols = [row[1] for row in cur.fetchall()]
    if "is_read" not in insight_cols:
        cur.execute(
            "ALTER TABLE email_insights ADD COLUMN is_read INTEGER DEFAULT 0")
    if "action_type" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN action_type TEXT")
    if "confidence" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN confidence INTEGER")
    if "ai_recommendation" not in insight_cols:
        cur.execute(
            "ALTER TABLE email_insights ADD COLUMN ai_recommendation TEXT")
    if "body" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN body TEXT")
    if "thread_id" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN thread_id TEXT")
    if "sentiment" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN sentiment TEXT")
    if "detected_language" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN detected_language TEXT")
    if "priority_score" not in insight_cols:
        cur.execute("ALTER TABLE email_insights ADD COLUMN priority_score INTEGER")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sent_emails (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        to_addr     TEXT NOT NULL,
        subject     TEXT,
        body        TEXT,
        sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
        triggered_by TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_actions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        gmail_message_id TEXT,
        email_insight_id INTEGER,
        action_type      TEXT NOT NULL,
        sender           TEXT NOT NULL,
        subject          TEXT,
        summary          TEXT,
        recommendation   TEXT,
        confidence       REAL,
        draft_response   TEXT,
        options          TEXT DEFAULT '{}',
        calendar_result  TEXT,
        status           TEXT DEFAULT 'pending',
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Migration: add thread_id to pending_actions if missing
    cur.execute("PRAGMA table_info(pending_actions)")
    pa_cols = [row[1] for row in cur.fetchall()]
    if "thread_id" not in pa_cols:
        cur.execute("ALTER TABLE pending_actions ADD COLUMN thread_id TEXT")

    # ── Calendar Events table (direct chat-created events) ────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS calendar_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        google_event_id   TEXT,
        calendar_provider TEXT DEFAULT 'google',
        event_type        TEXT NOT NULL DEFAULT 'other',
        title             TEXT,
        description       TEXT,
        start_time        TEXT,
        end_time          TEXT,
        location          TEXT,
        attendees         TEXT DEFAULT '[]',
        sync_status       TEXT DEFAULT 'created',
        calendar_link     TEXT,
        created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

    # ── Diagnostics ────────────────────────────────────────────────────────────
    _verify_tables()
    _require_table("system_logs", """
        CREATE TABLE system_logs (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT,
            status TEXT,
            payload TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _require_table("email_intelligence", """
        CREATE TABLE email_intelligence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT,
            sender TEXT NOT NULL,
            subject TEXT,
            category TEXT NOT NULL,
            summary TEXT,
            extracted_data_json TEXT DEFAULT '{}',
            importance_score INTEGER DEFAULT 50,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _require_table("email_insights", """
        CREATE TABLE email_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT,
            sender TEXT NOT NULL,
            subject TEXT,
            summary TEXT,
            category TEXT NOT NULL,
            priority TEXT,
            action_required INTEGER DEFAULT 0,
            important_note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _require_table("pending_actions", """
        CREATE TABLE pending_actions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT,
            email_insight_id INTEGER,
            action_type      TEXT NOT NULL,
            sender           TEXT NOT NULL,
            subject          TEXT,
            summary          TEXT,
            recommendation   TEXT,
            confidence       REAL,
            draft_response   TEXT,
            options          TEXT DEFAULT '[]',
            calendar_result  TEXT,
            status           TEXT DEFAULT 'pending',
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _require_table("calendar_events", """
        CREATE TABLE calendar_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            google_event_id   TEXT,
            calendar_provider TEXT DEFAULT 'google',
            event_type        TEXT NOT NULL DEFAULT 'other',
            title             TEXT,
            description       TEXT,
            start_time        TEXT,
            end_time          TEXT,
            location          TEXT,
            attendees         TEXT DEFAULT '[]',
            sync_status       TEXT DEFAULT 'created',
            calendar_link     TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


def get_log_stats() -> dict:
    """
    Return aggregate statistics from system_logs table.

    Returns:
        dict with:
          - total: total number of log entries
          - by_status: dict of {status: count}
          - by_agent: dict of {agent: count}
    """
    conn = get_connection()
    cur = conn.cursor()

    # Total count
    cur.execute("SELECT COUNT(*) FROM system_logs")
    total = cur.fetchone()[0]

    # Count by status
    cur.execute("SELECT status, COUNT(*) FROM system_logs GROUP BY status")
    by_status = dict(cur.fetchall())

    # Count by agent
    cur.execute("SELECT agent, COUNT(*) FROM system_logs GROUP BY agent")
    by_agent = dict(cur.fetchall())

    conn.close()
    return {
        "total": total,
        "by_status": by_status,
        "by_agent": by_agent,
    }


def get_logs(
    agent: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Query system_logs with optional filters and pagination.

    Args:
        agent   : filter by agent name (exact match)
        status  : filter by status (exact match)
        search  : full-text search in payload column
        page    : page number (1-based)
        page_size: items per page
        date_from: ISO date string (>=)
        date_to  : ISO date string (<=)

    Returns:
        dict with {items: list[dict], total: int, page: int, page_size: int}
    """
    conn = get_connection()
    cur = conn.cursor()

    conditions = []
    params = []

    if agent:
        conditions.append("agent = ?")
        params.append(agent)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("payload LIKE ?")
        params.append(f"%{search}%")
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Get total count
    cur.execute(f"SELECT COUNT(*) FROM system_logs {where_clause}", params)
    total = cur.fetchone()[0]

    # Get paginated items
    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT event_id, agent, status, payload, timestamp "
        f"FROM system_logs {where_clause} "
        f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )

    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append({
            "event_id": row[0],
            "agent": row[1],
            "status": row[2],
            "payload": row[3],
            "timestamp": row[4],
        })

    conn.close()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── Email Insight CRUD ────────────────────────────────────────────────────────

def insert_email_insight(
    gmail_message_id: str | None,
    sender: str,
    subject: str | None,
    category: str,
    summary: str | None = None,
    priority: str | None = None,
    action_required: bool = False,
    important_note: str | None = None,
    body: str | None = None,
    thread_id: str | None = None,
    sentiment: str | None = None,
    detected_language: str | None = None,
    priority_score: int | None = None,
) -> int:
    """
    Insert one email_insight record.

    Returns the new row id.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO email_insights "
        "(gmail_message_id, sender, subject, category, summary, priority, "
        "action_required, important_note, body, thread_id, "
        "sentiment, detected_language, priority_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            gmail_message_id,
            sender,
            subject,
            category,
            summary,
            priority,
            1 if action_required else 0,
            important_note,
            body,
            thread_id,
            sentiment,
            detected_language,
            priority_score,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_email_insight(insight_id: int) -> dict | None:
    """
    Return a single email_insight record by id, or None.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, gmail_message_id, sender, subject, summary, category, "
        "priority, action_required, important_note, is_read, created_at, "
        "confidence, ai_recommendation "
        "FROM email_insights WHERE id = ?",
        (insight_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return _insight_row_to_dict(row)


def get_email_insight_language(insight_id: int) -> dict:
    """Return detected_language and sentiment for a single email_insight row."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT detected_language, sentiment FROM email_insights WHERE id = ?",
        (insight_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {"detected_language": None, "sentiment": None}
    return {"detected_language": row[0], "sentiment": row[1]}


def get_email_insights(
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "created_at",
    category: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_read: Optional[int] = None,
) -> dict:
    """
    Return paginated list of email_insight records.

    Args:
        page      : page number (1-based)
        page_size : items per page
        sort_by   : "priority" or "created_at" (default)
        category  : optional category filter
        priority  : optional priority filter
        search    : optional search term (matches sender, subject, summary)

    Returns:
        dict with {items: list[dict], total: int, page: int, page_size: int}
    """
    conn = get_connection()
    cur = conn.cursor()

    conditions = []
    params = []

    if category:
        conditions.append("category = ?")
        params.append(category)
    if priority:
        conditions.append("priority = ?")
        params.append(priority)
    if search:
        conditions.append(
            "(sender LIKE ? OR subject LIKE ? OR summary LIKE ?)")
        like_term = f"%{search}%"
        params.extend([like_term, like_term, like_term])
    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to)
    if is_read is not None:
        conditions.append("is_read = ?")
        params.append(is_read)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Validate sort column to prevent SQL injection
    if sort_by == "priority":
        order_clause = "CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END, created_at DESC"
    else:
        order_clause = "created_at DESC"

    cur.execute(f"SELECT COUNT(*) FROM email_insights {where_clause}", params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT id, gmail_message_id, sender, subject, summary, category, "
        f"priority, action_required, important_note, is_read, created_at, "
        f"confidence, ai_recommendation "
        f"FROM email_insights {where_clause} "
        f"ORDER BY {order_clause} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )

    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append(_insight_row_to_dict(row))

    conn.close()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_insights_by_message_id(gmail_message_id: str) -> dict | None:
    """
    Return the most recent email_insight for a given Gmail message id.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, gmail_message_id, sender, subject, summary, category, "
        "priority, action_required, important_note, is_read, created_at, "
        "confidence, ai_recommendation, body, thread_id "
        "FROM email_insights WHERE gmail_message_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (gmail_message_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return _insight_row_to_dict(row)


def _insight_row_to_dict(row) -> dict:
    """Convert a raw email_insights db row (tuple) to a dict."""
    return {
        "id":               row[0],
        "gmail_message_id": row[1],
        "sender":           row[2],
        "subject":          row[3],
        "summary":          row[4],
        "category":         row[5],
        "priority":         row[6],
        "action_required":  bool(row[7]),
        "important_note":   row[8],
        "is_read":          bool(row[9]),
        "created_at":       row[10],
        "confidence":       row[11],
        "ai_recommendation": row[12],
        "body":             row[13] if len(row) > 13 else None,
        "thread_id":        row[14] if len(row) > 14 else None,
    }


def mark_email_read(insight_id: int) -> bool:
    """
    Mark an email_insight record as read.

    Returns True if a row was updated, False otherwise.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE email_insights SET is_read = 1 WHERE id = ?",
        (insight_id,),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def count_unread_emails() -> int:
    """
    Return the count of unread email_insight records.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM email_insights WHERE is_read = 0")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_dashboard_summary(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Return KPI counts for the Dashboard Summary section.

    emails_processed, meetings_scheduled, total_events, and errors are
    filtered by the optional date window.  pending_actions is always
    current (not date-filtered — it reflects the live action queue).
    """
    conn = get_connection()
    cur = conn.cursor()

    ei_conds: list = []
    ei_params: list = []
    sl_conds: list = []
    sl_params: list = []

    if date_from:
        ei_conds.append("processed_at >= ?")
        ei_params.append(date_from)
        sl_conds.append("timestamp >= ?")
        sl_params.append(date_from)
    if date_to:
        ei_conds.append("processed_at <= ?")
        ei_params.append(date_to)
        sl_conds.append("timestamp <= ?")
        sl_params.append(date_to)

    ei_where = ("WHERE " + " AND ".join(ei_conds)) if ei_conds else ""
    sl_where = ("WHERE " + " AND ".join(sl_conds)) if sl_conds else ""

    cur.execute(
        f"SELECT COUNT(*) FROM email_intelligence {ei_where}", ei_params)
    emails_processed = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM pending_actions "
        "WHERE status IN ('pending', 'draft_ready', 'waiting_send_confirmation')"
    )
    pending_actions = cur.fetchone()[0]

    mtg_conds = sl_conds + ["status = 'meeting_accepted'"]
    mtg_where = "WHERE " + " AND ".join(mtg_conds)
    cur.execute(
        f"SELECT COUNT(*) FROM system_logs {mtg_where}", sl_params)
    meetings_scheduled = cur.fetchone()[0]

    cur.execute(f"SELECT COUNT(*) FROM system_logs {sl_where}", sl_params)
    total_events = cur.fetchone()[0]

    err_conds = sl_conds + ["status = 'error'"]
    err_where = "WHERE " + " AND ".join(err_conds)
    cur.execute(
        f"SELECT COUNT(*) FROM system_logs {err_where}", sl_params)
    errors = cur.fetchone()[0]

    conn.close()
    return {
        "emails_processed": emails_processed,
        "pending_actions": pending_actions,
        "meetings_scheduled": meetings_scheduled,
        "total_events": total_events,
        "errors": errors,
    }


def get_pending_actions(
    page: int = 1,
    page_size: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Return combined pending actions from three sources, sorted by created_at DESC:
      1. email_insights  WHERE action_required=1 AND is_read=0
      2. pending_invites WHERE status='pending'
      3. pending_reschedules WHERE status='pending'

    date_from/date_to apply only to email_insights rows (the other two tables
    surface all pending items regardless of age).
    """
    conn = get_connection()
    cur = conn.cursor()
    items: list = []

    # ── email_insights ────────────────────────────────────────────────────────
    ei_conds = ["action_required = 1", "is_read = 0"]
    ei_params: list = []
    if date_from:
        ei_conds.append("created_at >= ?")
        ei_params.append(date_from)
    if date_to:
        ei_conds.append("created_at <= ?")
        ei_params.append(date_to)
    ei_where = "WHERE " + " AND ".join(ei_conds)

    cur.execute(
        f"SELECT id, sender, subject, action_type, priority, important_note, created_at "
        f"FROM email_insights {ei_where} ORDER BY created_at DESC",
        ei_params,
    )
    for row in cur.fetchall():
        items.append({
            "source": "email_action",
            "id": row[0],
            "token": None,
            "sender": row[1],
            "subject": row[2],
            "action_type": row[3] or "Cần xử lý",
            "priority": row[4],
            "note": row[5],
            "created_at": row[6],
        })

    # ── pending_invites ───────────────────────────────────────────────────────
    cur.execute(
        "SELECT token, action, created_at FROM pending_invites "
        "WHERE status = 'pending' ORDER BY created_at DESC"
    )
    for row in cur.fetchall():
        try:
            action_data = json.loads(row[1]) if row[1] else {}
        except (json.JSONDecodeError, TypeError):
            action_data = {}
        items.append({
            "source": "meeting_invite",
            "id": None,
            "token": row[0],
            "sender": action_data.get("invitee_email", ""),
            "subject": action_data.get("summary", "Cuộc họp"),
            "action_type": "Xác nhận cuộc họp",
            "priority": "high",
            "note": None,
            "created_at": row[2],
        })

    # ── pending_reschedules ───────────────────────────────────────────────────
    cur.execute(
        "SELECT token, action, created_at FROM pending_reschedules "
        "WHERE status = 'pending' ORDER BY created_at DESC"
    )
    for row in cur.fetchall():
        try:
            action_data = json.loads(row[1]) if row[1] else {}
        except (json.JSONDecodeError, TypeError):
            action_data = {}
        items.append({
            "source": "reschedule",
            "id": None,
            "token": row[0],
            "sender": action_data.get("invitee_email", ""),
            "subject": action_data.get("event_title", "Dời lịch"),
            "action_type": "Xác nhận dời lịch",
            "priority": "medium",
            "note": None,
            "created_at": row[2],
        })

    conn.close()

    # Merge-sort across the three sources (all already DESC within each source)
    items.sort(key=lambda x: x["created_at"] or "", reverse=True)

    total = len(items)
    offset = (page - 1) * page_size
    return {
        "items": items[offset : offset + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── Sent Emails CRUD ─────────────────────────────────────────────────────────

def insert_sent_email(
    to_addr: str,
    subject: str | None,
    body: str | None,
    triggered_by: str = "system",
) -> int:
    """Record one outgoing email in the sent_emails log."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sent_emails (to_addr, subject, body, triggered_by) "
        "VALUES (?, ?, ?, ?)",
        (to_addr, subject, body, triggered_by),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_sent_emails(
    page: int = 1,
    page_size: int = 20,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Return paginated list of sent emails.

    Returns:
        dict with {items: list[dict], total: int, page: int, page_size: int}
    """
    conn = get_connection()
    cur = conn.cursor()

    conds: list = []
    params: list = []
    if date_from:
        conds.append("sent_at >= ?")
        params.append(date_from)
    if date_to:
        conds.append("sent_at <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    cur.execute(f"SELECT COUNT(*) FROM sent_emails {where}", params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT id, to_addr, subject, body, sent_at, triggered_by "
        f"FROM sent_emails {where} ORDER BY sent_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    items = [
        {
            "id": row[0],
            "to_addr": row[1],
            "subject": row[2],
            "body": row[3],
            "sent_at": row[4],
            "triggered_by": row[5],
        }
        for row in rows
    ]
    conn.close()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Pending Actions CRUD ─────────────────────────────────────────────────────

_PENDING_ACTION_UPDATABLE_FIELDS = frozenset({
    "status", "recommendation", "draft_response", "calendar_result", "options",
})


def _pending_action_row_to_dict(row) -> dict:
    raw_options = json.loads(row[10]) if row[10] else {}
    # Normalise: old rows stored options as a list of capability strings.
    # New rows store a dict with {"available": [...], "step": ..., "event_id": ...}.
    if isinstance(raw_options, list):
        options = {"available": raw_options}
    else:
        options = raw_options if raw_options else {"available": []}

    return {
        "id":               row[0],
        "gmail_message_id": row[1],
        "email_insight_id": row[2],
        "action_type":      row[3],
        "sender":           row[4],
        "subject":          row[5],
        "summary":          row[6],
        "recommendation":   row[7],
        "confidence":       row[8],
        "draft_response":   row[9],
        "options":          options,
        "calendar_result":  json.loads(row[11]) if row[11] else None,
        "status":           row[12],
        "created_at":       row[13],
        "updated_at":       row[14],
        "thread_id":        row[15] if len(row) > 15 else None,
    }


def create_pending_action(
    action_type: str,
    sender: str,
    subject: str | None = None,
    summary: str | None = None,
    recommendation: str | None = None,
    confidence: float | None = None,
    draft_response: str | None = None,
    options: list | dict | None = None,
    calendar_result: dict | None = None,
    gmail_message_id: str | None = None,
    email_insight_id: int | None = None,
    thread_id: str | None = None,
) -> int:
    """Insert a new pending_action and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    opts = options if options is not None else {}
    cur.execute(
        "INSERT INTO pending_actions "
        "(gmail_message_id, email_insight_id, action_type, sender, subject, "
        "summary, recommendation, confidence, draft_response, options, "
        "calendar_result, thread_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            gmail_message_id,
            email_insight_id,
            action_type,
            sender,
            subject,
            summary,
            recommendation,
            confidence,
            draft_response,
            json.dumps(opts),
            json.dumps(calendar_result) if calendar_result is not None else None,
            thread_id,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_pending_action(action_id: int) -> dict | None:
    """Return a single pending_action by id, or None."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, gmail_message_id, email_insight_id, action_type, sender, "
        "subject, summary, recommendation, confidence, draft_response, options, "
        "calendar_result, status, created_at, updated_at, thread_id "
        "FROM pending_actions WHERE id = ?",
        (action_id,),
    )
    row = cur.fetchone()
    conn.close()
    return _pending_action_row_to_dict(row) if row else None


def get_pending_action_by_message_id(gmail_message_id: str) -> dict | None:
    """Return the most recent active pending_action for a Gmail message id, or None."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, gmail_message_id, email_insight_id, action_type, sender, "
        "subject, summary, recommendation, confidence, draft_response, options, "
        "calendar_result, status, created_at, updated_at, thread_id "
        "FROM pending_actions "
        "WHERE gmail_message_id = ? "
        "AND status NOT IN ('completed', 'cancelled') "
        "ORDER BY created_at DESC LIMIT 1",
        (gmail_message_id,),
    )
    row = cur.fetchone()
    conn.close()
    return _pending_action_row_to_dict(row) if row else None


def list_pending_actions(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    action_type: str | None = None,
) -> dict:
    """Return paginated pending_actions, newest first."""
    conn = get_connection()
    cur = conn.cursor()

    conditions: list = []
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur.execute(f"SELECT COUNT(*) FROM pending_actions {where}", params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT id, gmail_message_id, email_insight_id, action_type, sender, "
        f"subject, summary, recommendation, confidence, draft_response, options, "
        f"calendar_result, status, created_at, updated_at "
        f"FROM pending_actions {where} "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    conn.close()
    return {
        "items": [_pending_action_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def update_pending_action_status(action_id: int, status: str) -> bool:
    """Update status and updated_at for a pending_action. Returns True if a row was changed."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_actions SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, action_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_pending_action_draft(action_id: int, draft_response: str) -> bool:
    """Update draft_response and updated_at for a pending_action."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_actions SET draft_response = ?, updated_at = datetime('now') WHERE id = ?",
        (draft_response, action_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_pending_action_fields(action_id: int, **fields) -> bool:
    """
    Update any combination of allowed fields on a pending_action.

    Allowed keys: status, recommendation, draft_response, calendar_result, options.
    calendar_result and options values are JSON-serialized automatically if passed as
    dict/list. updated_at is always refreshed.
    Raises ValueError on unknown field names.
    """
    invalid = set(fields) - _PENDING_ACTION_UPDATABLE_FIELDS
    if invalid:
        raise ValueError(f"Unknown pending_action fields: {invalid}")
    if not fields:
        return False

    serialized: dict = {}
    for k, v in fields.items():
        if k in ("calendar_result", "options") and not isinstance(v, str):
            serialized[k] = json.dumps(v) if v is not None else None
        else:
            serialized[k] = v

    set_clauses = ", ".join(f"{k} = ?" for k in serialized)
    params = list(serialized.values()) + [action_id]

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE pending_actions SET {set_clauses}, updated_at = datetime('now') "
        "WHERE id = ?",
        params,
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def claim_action_status(
    action_id: int,
    from_statuses: str | list[str],
    to_status: str,
) -> bool:
    """
    Atomically transition a pending_action status only if it is currently one
    of from_statuses.  Returns True if the row was updated (i.e., the claim
    succeeded), False if someone else already changed the status (race
    condition detected).  Use this before doing any external work (calendar
    API, Gmail send) so that two concurrent requests cannot both proceed.
    """
    if isinstance(from_statuses, str):
        from_statuses = [from_statuses]
    placeholders = ",".join("?" for _ in from_statuses)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE pending_actions SET status = ?, updated_at = datetime('now') "
        f"WHERE id = ? AND status IN ({placeholders})",
        [to_status, action_id] + list(from_statuses),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# ── Calendar Events CRUD ─────────────────────────────────────────────────────

def insert_calendar_event(
    event_type: str,
    title: str | None = None,
    description: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    location: str | None = None,
    attendees: list | None = None,
    google_event_id: str | None = None,
    calendar_provider: str = "google",
    sync_status: str = "created",
    calendar_link: str | None = None,
) -> int:
    """Insert one calendar_event record and return its id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO calendar_events "
        "(google_event_id, calendar_provider, event_type, title, description, "
        "start_time, end_time, location, attendees, sync_status, calendar_link) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            google_event_id,
            calendar_provider,
            event_type,
            title,
            description,
            start_time,
            end_time,
            location,
            json.dumps(attendees or [], ensure_ascii=False),
            sync_status,
            calendar_link,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


# ── User helpers ─────────────────────────────────────────────────────────────

def get_user_by_google_id(google_id: str) -> dict | None:
    """Return a user dict by Google ID, or None if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, google_id, email, name, picture_url, "
        "google_access_token, google_refresh_token, token_expiry, "
        "created_at, last_login, last_dashboard_view_at "
        "FROM users WHERE google_id = ?",
        (google_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "google_id": row[1],
        "email": row[2],
        "name": row[3],
        "picture_url": row[4],
        "google_access_token": row[5],
        "google_refresh_token": row[6],
        "token_expiry": row[7],
        "created_at": row[8],
        "last_login": row[9],
        "last_dashboard_view_at": row[10],
    }


def get_user_by_id(user_id: int) -> dict | None:
    """Return a user dict by internal ID, or None if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, google_id, email, name, picture_url, "
        "google_access_token, google_refresh_token, token_expiry, "
        "created_at, last_login, last_dashboard_view_at "
        "FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "google_id": row[1],
        "email": row[2],
        "name": row[3],
        "picture_url": row[4],
        "google_access_token": row[5],
        "google_refresh_token": row[6],
        "token_expiry": row[7],
        "created_at": row[8],
        "last_login": row[9],
        "last_dashboard_view_at": row[10],
    }


def create_or_update_user(
    google_id: str,
    email: str,
    name: str | None = None,
    picture_url: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expiry: str | None = None,
) -> dict:
    """
    Insert a new user or update an existing user's tokens and last_login on re-login.

    Returns the user dict.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE google_id = ?", (google_id,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            "UPDATE users SET email = ?, name = ?, picture_url = ?, "
            "google_access_token = ?, google_refresh_token = ?, token_expiry = ?, "
            "last_login = datetime('now') "
            "WHERE google_id = ?",
            (email, name, picture_url, access_token,
             refresh_token, token_expiry, google_id),
        )
    else:
        cursor.execute(
            "INSERT INTO users (google_id, email, name, picture_url, "
            "google_access_token, google_refresh_token, token_expiry) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (google_id, email, name, picture_url,
             access_token, refresh_token, token_expiry),
        )
    conn.commit()
    conn.close()
    return get_user_by_google_id(google_id)


# ── Email Intelligence CRUD ────────────────────────────────────────────────────

def insert_email_analysis(
    email_id: str | None,
    sender: str,
    subject: str | None,
    category: str,
    summary: str | None,
    extracted_data_json: str = "{}",
    importance_score: int = 50,
) -> int:
    """
    Insert one email intelligence analysis record.

    Returns the new row id.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO email_intelligence "
        "(email_id, sender, subject, category, summary, extracted_data_json, importance_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email_id, sender, subject, category, summary,
         extracted_data_json, importance_score),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_email_analysis(analysis_id: int) -> dict | None:
    """
    Return a single email_intelligence record by id, or None.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, email_id, sender, subject, category, summary, "
        "extracted_data_json, importance_score, processed_at "
        "FROM email_intelligence WHERE id = ?",
        (analysis_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "email_id": row[1],
        "sender": row[2],
        "subject": row[3],
        "category": row[4],
        "summary": row[5],
        "extracted_data_json": row[6],
        "importance_score": row[7],
        "processed_at": row[8],
    }


def get_email_statistics() -> dict:
    """
    Return category distribution from email_intelligence (all time).

    Returns:
        dict: {total, meeting, report, partnership, support, announcement, other}
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM email_intelligence")
    total = cur.fetchone()[0]

    cur.execute(
        "SELECT category, COUNT(*) FROM email_intelligence GROUP BY category")
    by_category = dict(cur.fetchall())

    conn.close()
    return {
        "total": total,
        "meeting": by_category.get("meeting", 0),
        "report": by_category.get("report", 0),
        "partnership": by_category.get("partnership", 0),
        "support": by_category.get("support", 0),
        "announcement": by_category.get("announcement", 0),
        "other": by_category.get("other", 0),
    }


def get_email_statistics_since(since: str | None) -> dict:
    """
    Return category distribution from email_intelligence filtered by a
    processed_at threshold.

    When *since* is None (first Dashboard visit), returns ALL emails.
    Otherwise only counts emails where processed_at >= since.

    Args:
        since: ISO-8601 datetime string, or None to count all emails

    Returns:
        dict: {total, meeting, report, partnership, support, announcement, other}
    """
    conn = get_connection()
    cur = conn.cursor()

    if since is None:
        cur.execute("SELECT COUNT(*) FROM email_intelligence")
        total = cur.fetchone()[0]

        cur.execute(
            "SELECT category, COUNT(*) FROM email_intelligence GROUP BY category"
        )
        by_category = dict(cur.fetchall())
    else:
        cur.execute(
            "SELECT COUNT(*) FROM email_intelligence WHERE processed_at >= ?",
            (since,),
        )
        total = cur.fetchone()[0]

        cur.execute(
            "SELECT category, COUNT(*) FROM email_intelligence "
            "WHERE processed_at >= ? GROUP BY category",
            (since,),
        )
        by_category = dict(cur.fetchall())

    conn.close()
    return {
        "total": total,
        "meeting": by_category.get("meeting", 0),
        "report": by_category.get("report", 0),
        "partnership": by_category.get("partnership", 0),
        "support": by_category.get("support", 0),
        "announcement": by_category.get("announcement", 0),
        "other": by_category.get("other", 0),
    }


def get_email_statistics_range(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Return category distribution from email_intelligence filtered by an
    explicit date range (both bounds optional).
    """
    conn = get_connection()
    cur = conn.cursor()

    conds: list = []
    params: list = []
    if date_from:
        conds.append("processed_at >= ?")
        params.append(date_from)
    if date_to:
        conds.append("processed_at <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    cur.execute(f"SELECT COUNT(*) FROM email_intelligence {where}", params)
    total = cur.fetchone()[0]
    cur.execute(
        f"SELECT category, COUNT(*) FROM email_intelligence {where} GROUP BY category",
        params,
    )
    by_category = dict(cur.fetchall())
    conn.close()
    return {
        "total": total,
        "meeting": by_category.get("meeting", 0),
        "report": by_category.get("report", 0),
        "partnership": by_category.get("partnership", 0),
        "support": by_category.get("support", 0),
        "announcement": by_category.get("announcement", 0),
        "other": by_category.get("other", 0),
    }


def update_last_dashboard_view(user_id: int):
    """
    Set the last_dashboard_view_at timestamp to now() for the given user.
    Call this AFTER analytics have been successfully returned.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET last_dashboard_view_at = datetime('now') "
        "WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def get_emails_since(since: str, limit: int = 100) -> list[dict]:
    """
    Return email_intelligence records processed since a given timestamp.

    Args:
        since: ISO-8601 datetime string (e.g. '2026-06-10T00:00:00')
        limit: max number of rows to return

    Returns:
        list of email intelligence dicts
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, email_id, sender, subject, category, summary, "
        "extracted_data_json, importance_score, processed_at "
        "FROM email_intelligence "
        "WHERE processed_at >= ? "
        "ORDER BY importance_score DESC LIMIT ?",
        (since, limit),
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append({
            "id": row[0],
            "email_id": row[1],
            "sender": row[2],
            "subject": row[3],
            "category": row[4],
            "summary": row[5],
            "extracted_data_json": row[6],
            "importance_score": row[7],
            "processed_at": row[8],
        })
    conn.close()
    return items


def get_top_important_emails(since: str, top_n: int = 3) -> list[dict]:
    """
    Return top N most important emails processed since a timestamp.

    Args:
        since: ISO-8601 datetime string
        top_n: number of top emails to return (default 3)

    Returns:
        list of email intelligence dicts sorted by importance_score DESC
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, email_id, sender, subject, category, summary, "
        "extracted_data_json, importance_score, processed_at "
        "FROM email_intelligence "
        "WHERE processed_at >= ? "
        "ORDER BY importance_score DESC LIMIT ?",
        (since, top_n),
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append({
            "id": row[0],
            "email_id": row[1],
            "sender": row[2],
            "subject": row[3],
            "category": row[4],
            "summary": row[5],
            "extracted_data_json": row[6],
            "importance_score": row[7],
            "processed_at": row[8],
        })
    conn.close()
    return items


def get_recent_emails_for_summary(since: str | None, limit: int = 20) -> list[dict]:
    """
    Return emails processed since a timestamp for AI summarization.

    If *since* is None (never viewed Dashboard), returns the latest *limit* emails.
    Otherwise returns emails where processed_at > since, ordered by processed_at DESC.

    Args:
        since: ISO-8601 datetime string, or None
        limit: max number of rows (default 20)

    Returns:
        list of dicts with keys: sender, subject, category, summary, processed_at
    """
    conn = get_connection()
    cur = conn.cursor()

    if since is None:
        cur.execute(
            "SELECT sender, subject, category, summary, processed_at "
            "FROM email_intelligence "
            "ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        )
    else:
        cur.execute(
            "SELECT sender, subject, category, summary, processed_at "
            "FROM email_intelligence "
            "WHERE processed_at > ? "
            "ORDER BY processed_at DESC LIMIT ?",
            (since, limit),
        )

    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append({
            "sender": row[0],
            "subject": row[1],
            "category": row[2],
            "summary": row[3],
            "processed_at": row[4],
        })

    conn.close()
    return items


def get_recent_emails(
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "processed_at",
) -> dict:
    """
    Return paginated list of analyzed emails.

    Args:
        page     : page number (1-based)
        page_size: items per page
        sort_by  : "importance" sorts by importance_score DESC,
                   otherwise sorts by processed_at DESC

    Returns:
        dict with {items: list[dict], total: int, page: int, page_size: int}
    """
    conn = get_connection()
    cur = conn.cursor()

    # Validate sort column to prevent SQL injection
    if sort_by == "importance":
        order_clause = "importance_score DESC, processed_at DESC"
    else:
        order_clause = "processed_at DESC"

    cur.execute("SELECT COUNT(*) FROM email_intelligence")
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(
        f"SELECT id, email_id, sender, subject, category, summary, "
        f"extracted_data_json, importance_score, processed_at "
        f"FROM email_intelligence "
        f"ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (page_size, offset),
    )

    rows = cur.fetchall()
    items = []
    for row in rows:
        items.append({
            "id": row[0],
            "email_id": row[1],
            "sender": row[2],
            "subject": row[3],
            "category": row[4],
            "summary": row[5],
            "extracted_data_json": row[6],
            "importance_score": row[7],
            "processed_at": row[8],
        })

    conn.close()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
