"""
Unit tests for app/db/sqlite.py

Tests:
- get_connection()
- init_db()
- get_log_stats() — total, by_status, by_agent
- get_logs() — filtering, pagination, search, date range
"""

import json
import sqlite3

import pytest

from app.db.sqlite import get_connection, init_db, get_log_stats, get_logs


class TestDatabaseConnection:
    """Tests for basic DB connection and initialization."""

    def test_get_connection(self, temp_db_path):
        """get_connection should return a sqlite3.Connection."""
        import app.db.sqlite as sqlite_mod
        original = sqlite_mod.DB_NAME
        sqlite_mod.DB_NAME = temp_db_path
        try:
            conn = get_connection()
            assert isinstance(conn, sqlite3.Connection)
            conn.close()
        finally:
            sqlite_mod.DB_NAME = original

    def test_init_db_creates_table(self, temp_db_path):
        """init_db should create the system_logs table."""
        import app.db.sqlite as sqlite_mod
        original = sqlite_mod.DB_NAME
        sqlite_mod.DB_NAME = temp_db_path
        try:
            init_db()
            conn = sqlite3.connect(temp_db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='system_logs'"
            )
            assert cur.fetchone() is not None
            conn.close()
        finally:
            sqlite_mod.DB_NAME = original

    def test_init_db_idempotent(self, temp_db_path):
        """init_db should be safe to call multiple times."""
        import app.db.sqlite as sqlite_mod
        original = sqlite_mod.DB_NAME
        sqlite_mod.DB_NAME = temp_db_path
        try:
            init_db()
            init_db()  # second call should not raise
            conn = sqlite3.connect(temp_db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM system_logs")
            assert cur.fetchone()[0] == 0
            conn.close()
        finally:
            sqlite_mod.DB_NAME = original


class TestGetLogStats:
    """Tests for get_log_stats()."""

    def test_empty_db_returns_zeros(self, db_connection):
        """With no logs, stats should show total=0 and empty dicts."""
        stats = get_log_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_agent"] == {}

    def test_returns_correct_counts(self, seed_logs):
        """With seeded data, stats should reflect correct aggregates."""
        stats = get_log_stats()
        assert stats["total"] == 8
        assert stats["by_status"].get("schedule") == 2
        assert stats["by_status"].get("created") == 1
        assert stats["by_status"].get("sent") == 1
        assert stats["by_status"].get("spam") == 1
        assert stats["by_status"].get("success") == 2
        assert stats["by_status"].get("conflict") == 1
        assert stats["by_agent"].get("email_agent") == 2
        assert stats["by_agent"].get("calendar_agent") == 2
        assert stats["by_agent"].get("notification_agent") == 1
        assert stats["by_agent"].get("spam_filter") == 1
        assert stats["by_agent"].get("orchestrator") == 1
        assert stats["by_agent"].get("evaluation_agent") == 1


class TestGetLogs:
    """Tests for get_logs()."""

    def test_empty_db_returns_empty(self, db_connection):
        """With no logs, should return empty items list."""
        result = get_logs()
        assert result["items"] == []
        assert result["total"] == 0
        assert result["page"] == 1
        assert result["page_size"] == 20

    def test_returns_all_items(self, seed_logs):
        """Without filters, should return all items."""
        result = get_logs()
        assert result["total"] == 8
        assert len(result["items"]) == 8

    def test_pagination(self, seed_logs):
        """Should respect page and page_size parameters."""
        result = get_logs(page=1, page_size=3)
        assert len(result["items"]) == 3
        assert result["total"] == 8
        assert result["page"] == 1
        assert result["page_size"] == 3

        result_page2 = get_logs(page=2, page_size=3)
        assert len(result_page2["items"]) == 3

        result_page3 = get_logs(page=3, page_size=3)
        assert len(result_page3["items"]) == 2

    def test_filter_by_agent(self, seed_logs):
        """Should filter by agent name (exact match)."""
        result = get_logs(agent="email_agent")
        assert result["total"] == 2
        for item in result["items"]:
            assert item["agent"] == "email_agent"

    def test_filter_by_status(self, seed_logs):
        """Should filter by status (exact match)."""
        result = get_logs(status="spam")
        assert result["total"] == 1
        assert result["items"][0]["status"] == "spam"

    def test_filter_by_agent_and_status(self, seed_logs):
        """Should support combined agent + status filter."""
        result = get_logs(agent="email_agent", status="schedule")
        assert result["total"] == 2
        for item in result["items"]:
            assert item["agent"] == "email_agent"
            assert item["status"] == "schedule"

    def test_search_in_payload(self, seed_logs):
        """Should search within the payload column (LIKE)."""
        result = get_logs(search="newsletter")
        assert result["total"] == 1
        assert "newsletter" in result["items"][0]["payload"]

    def test_search_no_match(self, seed_logs):
        """Search with non-matching term should return empty."""
        result = get_logs(search="zzz_nonexistent_zzz")
        assert result["total"] == 0
        assert result["items"] == []

    def test_date_filter(self, db_connection):
        """Should filter by date range."""
        # Insert logs with specific timestamps
        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("test_agent", "old", "{}", "2026-01-01 00:00:00"),
        )
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("test_agent", "recent", "{}", "2026-06-06 00:00:00"),
        )
        conn.commit()
        conn.close()

        # Filter for recent only
        result = get_logs(date_from="2026-06-01")
        assert result["total"] == 1
        assert result["items"][0]["status"] == "recent"

        # Filter for old only
        result = get_logs(date_to="2026-01-02")
        assert result["total"] == 1
        assert result["items"][0]["status"] == "old"

    def test_items_have_correct_keys(self, seed_logs):
        """Each item should have event_id, agent, status, payload, timestamp."""
        result = get_logs(page_size=1)
        item = result["items"][0]
        assert "event_id" in item
        assert "agent" in item
        assert "status" in item
        assert "payload" in item
        assert "timestamp" in item

    def test_payload_is_json_string(self, seed_logs):
        """Payload should be a valid JSON string."""
        result = get_logs(page_size=1)
        payload = result["items"][0]["payload"]
        # Should be parseable as JSON
        parsed = json.loads(payload)
        assert isinstance(parsed, dict)
