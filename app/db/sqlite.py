import sqlite3
from typing import Optional

from app.core.config import settings

DB_NAME = settings.DATABASE_PATH


def get_connection():
    return sqlite3.connect(DB_NAME)


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
    CREATE TABLE IF NOT EXISTS pending_cancels (
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

    conn.commit()
    conn.close()


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
