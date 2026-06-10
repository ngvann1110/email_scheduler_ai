"""
Unit tests for app/db/sqlite.py

Tests:
- get_connection()
- init_db()
- get_log_stats() — total, by_status, by_agent
- get_logs() — filtering, pagination, search, date range
"""

from app.db.sqlite import (
    insert_email_analysis,
    get_email_analysis,
    get_email_statistics,
    get_recent_emails,
)
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


# ── Email Intelligence CRUD tests ──────────────────────────────────────────────


class TestInsertEmailAnalysis:
    """Tests for insert_email_analysis()."""

    def test_insert_returns_int_id(self, db_connection):
        """insert_email_analysis should return an integer id."""
        new_id = insert_email_analysis(
            email_id="msg-001",
            sender="partner@example.com",
            subject="Hợp tác dự án AI",
            category="partnership",
            summary="- Cơ hội hợp tác phát triển AI\n- Dự kiến ký MOU tháng 7",
            extracted_data_json='{"project":"AI Platform"}',
            importance_score=85,
        )
        assert isinstance(new_id, int)
        assert new_id > 0

    def test_insert_minimal_fields(self, db_connection):
        """Insert with only required fields should succeed."""
        new_id = insert_email_analysis(
            email_id=None,
            sender="anon@test.com",
            subject=None,
            category="other",
            summary=None,
        )
        assert isinstance(new_id, int)
        assert new_id > 0

    def test_insert_default_values(self, db_connection):
        """Default summary/score should be stored correctly."""
        new_id = insert_email_analysis(
            email_id="msg-defaults",
            sender="test@test.com",
            subject="Test defaults",
            category="announcement",
            summary=None,
        )
        record = get_email_analysis(new_id)
        assert record is not None
        assert record["summary"] is None
        assert record["extracted_data_json"] == "{}"
        assert record["importance_score"] == 50


class TestGetEmailAnalysis:
    """Tests for get_email_analysis()."""

    def test_get_existing_record(self, db_connection):
        """Should return full record dict for a valid id."""
        new_id = insert_email_analysis(
            email_id="msg-002",
            sender="report@corp.com",
            subject="Báo cáo Q1",
            category="report",
            summary="- Doanh thu tăng 15%\n- Chi phí giảm 5%",
            extracted_data_json='{"deadline":"2026-04-15"}',
            importance_score=72,
        )
        record = get_email_analysis(new_id)
        assert record is not None
        assert record["email_id"] == "msg-002"
        assert record["sender"] == "report@corp.com"
        assert record["subject"] == "Báo cáo Q1"
        assert record["category"] == "report"
        assert record["summary"] == "- Doanh thu tăng 15%\n- Chi phí giảm 5%"
        assert record["extracted_data_json"] == '{"deadline":"2026-04-15"}'
        assert record["importance_score"] == 72
        assert record["processed_at"] is not None

    def test_get_nonexistent_record(self, db_connection):
        """Should return None for invalid id."""
        record = get_email_analysis(99999)
        assert record is None

    def test_all_keys_present(self, db_connection):
        """Returned dict should have all expected keys."""
        new_id = insert_email_analysis(
            email_id="msg-keys",
            sender="keys@test.com",
            subject="Key check",
            category="other",
            summary=None,
        )
        record = get_email_analysis(new_id)
        expected_keys = {
            "id", "email_id", "sender", "subject", "category",
            "summary", "extracted_data_json", "importance_score", "processed_at",
        }
        assert set(record.keys()) == expected_keys


class TestGetEmailStatistics:
    """Tests for get_email_statistics()."""

    def test_stats_empty_table(self, db_connection):
        """When table is empty (after clearing), all counts should be 0."""
        # Insert nothing additional; but we must account for other tests' inserts
        stats = get_email_statistics()
        # All keys should be present
        assert "total" in stats
        assert "meeting" in stats
        assert "report" in stats
        assert "partnership" in stats
        assert "support" in stats
        assert "announcement" in stats
        assert "other" in stats
        # total should equal sum of categories
        expected_total = (
            stats["meeting"]
            + stats["report"]
            + stats["partnership"]
            + stats["support"]
            + stats["announcement"]
            + stats["other"]
        )
        assert stats["total"] == expected_total

    def test_stats_after_inserts(self, db_connection):
        """Stats should reflect inserted records."""
        insert_email_analysis(
            email_id="stats-1", sender="s1@t.com", subject="S1",
            category="report", summary=None, importance_score=60,
        )
        insert_email_analysis(
            email_id="stats-2", sender="s2@t.com", subject="S2",
            category="report", summary=None, importance_score=70,
        )
        insert_email_analysis(
            email_id="stats-3", sender="s3@t.com", subject="S3",
            category="support", summary=None, importance_score=30,
        )
        stats = get_email_statistics()
        assert stats["report"] >= 2
        assert stats["support"] >= 1
        assert stats["total"] >= 3

    def test_stats_type(self, db_connection):
        """All stat values should be integers."""
        stats = get_email_statistics()
        for k, v in stats.items():
            assert isinstance(v, int), f"{k} should be int, got {type(v)}"


class TestGetRecentEmails:
    """Tests for get_recent_emails()."""

    def test_returns_dict_with_correct_keys(self, db_connection):
        """Should return dict with items, total, page, page_size."""
        result = get_recent_emails()
        assert "items" in result
        assert "total" in result
        assert "page" in result
        assert "page_size" in result
        assert isinstance(result["items"], list)
        assert isinstance(result["total"], int)
        assert result["page"] == 1
        assert result["page_size"] == 20

    def test_pagination_first_page(self, db_connection):
        """First page should have no more than page_size items."""
        result = get_recent_emails(page=1, page_size=3)
        assert len(result["items"]) <= 3

    def test_sort_by_importance(self, db_connection):
        """When sort_by='importance', items should be in descending importance order."""
        insert_email_analysis(
            email_id="sort-low", sender="low@t.com", subject="Low",
            category="other", summary=None, importance_score=10,
        )
        insert_email_analysis(
            email_id="sort-high", sender="high@t.com", subject="High",
            category="announcement", summary=None, importance_score=99,
        )
        result = get_recent_emails(sort_by="importance", page_size=5)
        items = result["items"]
        scores = [item["importance_score"] for item in items]
        assert scores == sorted(
            scores, reverse=True), f"Scores not sorted DESC: {scores}"

    def test_default_sort_is_processed_at(self, db_connection):
        """Default sort should be by processed_at DESC."""
        result = get_recent_emails(page_size=5)
        items = result["items"]
        if len(items) >= 2:
            # processed_at should be descending (newest first)
            timestamps = [item["processed_at"] for item in items]
            assert timestamps == sorted(timestamps, reverse=True), \
                f"Timestamps not sorted DESC: {timestamps}"

    def test_item_structure(self, db_connection):
        """Each item should have all expected fields."""
        insert_email_analysis(
            email_id="struct-test", sender="struct@t.com", subject="Structure",
            category="meeting",
            summary="- Test summary",
            extracted_data_json='{"meeting_date":"2026-07-01"}',
            importance_score=50,
        )
        result = get_recent_emails(page_size=1)
        item = result["items"][0]
        assert "id" in item
        assert "email_id" in item
        assert "sender" in item
        assert "subject" in item
        assert "category" in item
        assert "summary" in item
        assert "extracted_data_json" in item
        assert "importance_score" in item
        assert "processed_at" in item
