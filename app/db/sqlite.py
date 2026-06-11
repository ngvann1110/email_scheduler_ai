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
