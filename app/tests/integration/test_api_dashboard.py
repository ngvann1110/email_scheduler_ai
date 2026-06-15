"""
Integration tests for Dashboard API endpoints.

Tests:
- GET /dashboard/stats — returns stats and upcoming events
- GET /dashboard/logs — returns paginated/filtered logs
- GET /dashboard/logs with filters (agent, status, search, pagination)
"""

import json
import sqlite3
from unittest.mock import patch

import pytest

from app.db.sqlite import get_log_stats, get_logs


# ── Pending Actions ───────────────────────────────────────────────────────────


class TestPendingActions:
    """Tests for GET /dashboard/pending-actions."""

    def test_pending_returns_structure(self, auth_client, db_connection):
        """Should return items, total, page, page_size."""
        response = auth_client.get("/dashboard/pending-actions")
        assert response.status_code == 200
        data = response.json()
        for key in ("items", "total", "page", "page_size"):
            assert key in data, f"Missing key: {key}"

    def test_pending_empty_returns_zero(self, auth_client, db_connection):
        """With no pending items, total should be 0."""
        response = auth_client.get("/dashboard/pending-actions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_pending_includes_email_action(self, auth_client, db_connection):
        """email_insights rows with action_required=1 and is_read=0 appear."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO email_insights "
            "(sender, subject, category, summary, priority, action_required, is_read) "
            "VALUES (?,?,?,?,?,?,?)",
            ("boss@corp.com", "Review Q2", "report", "Please review", "high", 1, 0),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions").json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["source"] == "email_action"
        assert item["sender"] == "boss@corp.com"
        assert item["priority"] == "high"

    def test_pending_excludes_read_email(self, auth_client, db_connection):
        """email_insights rows with is_read=1 must NOT appear."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO email_insights "
            "(sender, subject, category, summary, priority, action_required, is_read) "
            "VALUES (?,?,?,?,?,?,?)",
            ("old@corp.com", "Old task", "other", "Done", "low", 1, 1),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions").json()
        assert data["total"] == 0

    def test_pending_includes_invite(self, auth_client, db_connection):
        """pending_invites with status='pending' appear as meeting_invite items."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO pending_invites (token, action, status) VALUES (?,?,?)",
            ("tok-abc",
             json.dumps({"invitee_email": "guest@x.com", "summary": "Sprint Review"}),
             "pending"),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions").json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["source"] == "meeting_invite"
        assert item["sender"] == "guest@x.com"
        assert item["subject"] == "Sprint Review"
        assert item["action_type"] == "Xác nhận cuộc họp"

    def test_pending_excludes_confirmed_invite(self, auth_client, db_connection):
        """pending_invites with status='confirmed' must NOT appear."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO pending_invites (token, action, status) VALUES (?,?,?)",
            ("tok-done", json.dumps({"invitee_email": "x@x.com", "summary": "Done"}),
             "confirmed"),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions").json()
        assert data["total"] == 0

    def test_pending_combines_all_sources(self, auth_client, db_connection):
        """Items from all three sources are merged and returned together."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        # email action
        cur.execute(
            "INSERT INTO email_insights "
            "(sender, subject, category, summary, priority, action_required, is_read) "
            "VALUES (?,?,?,?,?,?,?)",
            ("a@x.com", "Task A", "other", "do it", "high", 1, 0),
        )
        # pending invite
        cur.execute(
            "INSERT INTO pending_invites (token, action, status) VALUES (?,?,?)",
            ("t1", json.dumps({"invitee_email": "b@x.com", "summary": "Meeting B"}),
             "pending"),
        )
        # pending reschedule
        cur.execute(
            "INSERT INTO pending_reschedules (token, action, status) VALUES (?,?,?)",
            ("t2", json.dumps({"invitee_email": "c@x.com", "event_title": "Move C"}),
             "pending"),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions").json()
        assert data["total"] == 3
        sources = {i["source"] for i in data["items"]}
        assert sources == {"email_action", "meeting_invite", "reschedule"}

    def test_pending_date_from_filters_email_actions(self, auth_client, db_connection):
        """date_from should exclude email_insights created before that date."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO email_insights "
            "(sender, subject, category, summary, priority, action_required, is_read, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("old@x.com", "Old", "other", "old", "low", 1, 0, "2026-01-01 00:00:00"),
        )
        cur.execute(
            "INSERT INTO email_insights "
            "(sender, subject, category, summary, priority, action_required, is_read, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("new@x.com", "New", "other", "new", "high", 1, 0, "2026-06-10 00:00:00"),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions?date_from=2026-06-01").json()
        # Only the new email action; pending_invites/reschedules are unaffected by date
        assert data["total"] == 1
        assert data["items"][0]["sender"] == "new@x.com"

    def test_pending_pagination(self, auth_client, db_connection):
        """Should respect page_size parameter."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        for i in range(5):
            cur.execute(
                "INSERT INTO email_insights "
                "(sender, subject, category, summary, priority, action_required, is_read) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"s{i}@x.com", f"Task {i}", "other", "do", "low", 1, 0),
            )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/pending-actions?page_size=2").json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page_size"] == 2

    def test_pending_requires_auth(self, test_client):
        """Unauthenticated request should be rejected."""
        assert test_client.get("/dashboard/pending-actions").status_code == 401


# ── Dashboard Summary ─────────────────────────────────────────────────────────


class TestDashboardSummary:
    """Tests for GET /dashboard/summary."""

    def test_summary_returns_required_keys(self, auth_client):
        """Should return all five KPI fields."""
        response = auth_client.get("/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        for key in ("emails_processed", "pending_actions", "meetings_scheduled",
                    "total_events", "errors"):
            assert key in data, f"Missing key: {key}"

    def test_summary_empty_db_all_zeros(self, auth_client, db_connection):
        """With an empty DB every counter should be 0."""
        response = auth_client.get("/dashboard/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["emails_processed"]   == 0
        assert data["pending_actions"]    == 0
        assert data["meetings_scheduled"] == 0
        assert data["total_events"]       == 0
        assert data["errors"]             == 0

    def test_summary_counts_emails_processed(self, auth_client, db_connection):
        """emails_processed counts rows in email_intelligence."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        for i in range(3):
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) VALUES (NULL,?,?,?,?,?,?)",
                (f"s{i}@x.com", f"S{i}", "meeting", "sum", "{}", 50),
            )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/summary").json()
        assert data["emails_processed"] == 3

    def test_summary_counts_meetings_scheduled(self, auth_client, db_connection):
        """meetings_scheduled counts system_logs WHERE status='meeting_accepted'."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute("INSERT INTO system_logs (agent, status, payload) VALUES (?,?,?)",
                    ("chat", "meeting_accepted", "{}"))
        cur.execute("INSERT INTO system_logs (agent, status, payload) VALUES (?,?,?)",
                    ("chat", "error", "{}"))
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/summary").json()
        assert data["meetings_scheduled"] == 1
        assert data["total_events"]       == 2
        assert data["errors"]             == 1

    def test_summary_date_from_filters_emails(self, auth_client, db_connection):
        """date_from parameter should exclude emails processed before that date."""
        conn = sqlite3.connect(db_connection)
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO email_intelligence "
            "(email_id, sender, subject, category, summary, "
            "extracted_data_json, importance_score, processed_at) VALUES (NULL,?,?,?,?,?,?,?)",
            ("old@x.com", "Old", "meeting", "old", "{}", 50, "2026-01-01 00:00:00"),
        )
        cur.execute(
            "INSERT INTO email_intelligence "
            "(email_id, sender, subject, category, summary, "
            "extracted_data_json, importance_score, processed_at) VALUES (NULL,?,?,?,?,?,?,?)",
            ("new@x.com", "New", "meeting", "new", "{}", 50, "2026-06-10 00:00:00"),
        )
        conn.commit()
        conn.close()

        data = auth_client.get("/dashboard/summary?date_from=2026-06-01").json()
        assert data["emails_processed"] == 1

    def test_summary_requires_auth(self, test_client):
        """Unauthenticated request should be rejected."""
        response = test_client.get("/dashboard/summary")
        assert response.status_code == 401


class TestDashboardStats:
    """Tests for GET /dashboard/stats."""

    def test_stats_endpoint_returns_structure(self, auth_client, db_connection):
        """Should return stats with total, by_status, by_agent."""
        response = auth_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data
        assert "upcoming_events" in data
        assert "total" in data["stats"]
        assert "by_status" in data["stats"]
        assert "by_agent" in data["stats"]

    def test_stats_with_seeded_data(self, auth_client, seed_logs):
        """Should reflect seeded log data in stats."""
        response = auth_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total"] == 8
        assert data["stats"]["by_agent"]["email_agent"] == 2

    def test_stats_empty_db(self, auth_client, db_connection):
        """Should return zeros when DB is empty."""
        response = auth_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total"] == 0
        assert data["stats"]["by_status"] == {}
        assert data["stats"]["by_agent"] == {}


class TestDashboardLogs:
    """Tests for GET /dashboard/logs."""

    def test_logs_endpoint_returns_structure(self, auth_client, db_connection):
        """Should return items, total, page, page_size."""
        response = auth_client.get("/dashboard/logs")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_logs_with_seeded_data(self, auth_client, seed_logs):
        """Should return all seeded logs."""
        response = auth_client.get("/dashboard/logs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 8
        assert len(data["items"]) == 8

    def test_logs_pagination(self, auth_client, seed_logs):
        """Should respect page_size parameter."""
        response = auth_client.get("/dashboard/logs?page_size=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        assert data["page_size"] == 3
        assert data["total"] == 8

    def test_logs_page_2(self, auth_client, seed_logs):
        """Should return correct page 2 results."""
        response = auth_client.get("/dashboard/logs?page=2&page_size=3")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 3

    def test_logs_filter_by_agent(self, auth_client, seed_logs):
        """Should filter logs by agent."""
        response = auth_client.get("/dashboard/logs?agent=email_agent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["agent"] == "email_agent"

    def test_logs_filter_by_status(self, auth_client, seed_logs):
        """Should filter logs by status."""
        response = auth_client.get("/dashboard/logs?status=spam")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "spam"

    def test_logs_search(self, auth_client, seed_logs):
        """Should search logs by payload content."""
        response = auth_client.get("/dashboard/logs?search=newsletter")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_logs_combined_filters(self, auth_client, seed_logs):
        """Should support combined agent + status filter."""
        response = auth_client.get(
            "/dashboard/logs?agent=email_agent&status=schedule")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_logs_empty_result(self, auth_client, db_connection):
        """Should return empty items when no logs match."""
        response = auth_client.get("/dashboard/logs?agent=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_logs_date_filter(self, auth_client, db_connection):
        """Should filter logs by date range."""
        # Insert logs with specific dates
        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("test", "old", "{}", "2026-01-01 00:00:00"),
        )
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("test", "new", "{}", "2026-06-06 00:00:00"),
        )
        conn.commit()
        conn.close()

        response = auth_client.get("/dashboard/logs?date_from=2026-06-01")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "new"


# ── Email Intelligence Dashboard ──────────────────────────────────────────────


class TestEmailStats:
    """Tests for GET /dashboard/email-stats."""

    def test_email_stats_returns_structure(self, auth_client):
        """Should return total + category breakdown."""
        response = auth_client.get("/dashboard/email-stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "meeting" in data
        assert "report" in data
        assert "partnership" in data
        assert "support" in data
        assert "announcement" in data
        assert "other" in data

    def test_email_stats_empty_db(self, auth_client):
        """Should return all zeros when no analyzed emails."""
        response = auth_client.get("/dashboard/email-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["meeting"] == 0
        assert data["report"] == 0

    def test_email_stats_with_data(self, auth_client, db_connection):
        """Should reflect seeded email_intelligence data."""
        from app.db.sqlite import get_connection, init_db

        # Ensure email_intelligence table exists
        init_db()
        conn = get_connection()
        cur = conn.cursor()
        samples = [
            ("sender1@test.com", "Subject 1", "report", "Summary 1", '{}', 70),
            ("sender2@test.com", "Subject 2", "meeting", "Summary 2", '{}', 80),
            ("sender3@test.com", "Subject 3", "report", "Summary 3", '{}', 60),
            ("sender4@test.com", "Subject 4", "support", "Summary 4", '{}', 50),
        ]
        for sender, subject, category, summary, extracted, score in samples:
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (sender, subject, category, summary, extracted, score),
            )
        conn.commit()
        conn.close()

        response = auth_client.get("/dashboard/email-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        assert data["report"] == 2
        assert data["meeting"] == 1
        assert data["support"] == 1
        assert data["partnership"] == 0


class TestRecentEmails:
    """Tests for GET /dashboard/recent-emails.

    The endpoint returns {"emails": [...], "count": N} — a lightweight
    non-paginated list of recent emails for AI summarisation.
    """

    def test_recent_emails_returns_structure(self, auth_client):
        """Should return emails list and count."""
        response = auth_client.get("/dashboard/recent-emails")
        assert response.status_code == 200
        data = response.json()
        assert "emails" in data
        assert "count" in data

    def test_recent_emails_empty_db(self, auth_client):
        """Should return empty list when no emails exist."""
        response = auth_client.get("/dashboard/recent-emails")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["emails"] == []

    def test_recent_emails_with_data(self, auth_client, db_connection):
        """Should return seeded email_intelligence data with correct fields."""
        from app.db.sqlite import get_connection, init_db

        init_db()
        conn = get_connection()
        cur = conn.cursor()
        samples = [
            ("sender_a@test.com", "Report Q2", "report", "Báo cáo Q2", '{}', 75),
            ("sender_b@test.com", "Meeting request", "meeting", "Họp dự án", '{}', 85),
            ("sender_c@test.com", "Hỗ trợ kỹ thuật", "support", "Cần hỗ trợ API", '{}', 45),
        ]
        for sender, subject, category, summary, extracted, score in samples:
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (sender, subject, category, summary, extracted, score),
            )
        conn.commit()
        conn.close()

        response = auth_client.get("/dashboard/recent-emails")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["emails"]) == 3
        for item in data["emails"]:
            assert "sender" in item
            assert "category" in item
            assert "summary" in item
            assert "processed_at" in item


# ── Email Insights ────────────────────────────────────────────────────────────


def _insert_insight(conn, sender, subject, category="report", priority="high",
                    action_required=0, is_read=0, confidence=None,
                    ai_recommendation=None, created_at=None):
    """Helper — insert one email_insights row."""
    cur = conn.cursor()
    sql = (
        "INSERT INTO email_insights "
        "(sender, subject, category, priority, action_required, is_read, "
        "confidence, ai_recommendation"
        + (", created_at" if created_at else "") + ") "
        "VALUES (?,?,?,?,?,?,?,?"
        + (",?" if created_at else "") + ")"
    )
    args = [sender, subject, category, priority, action_required, is_read,
            confidence, ai_recommendation]
    if created_at:
        args.append(created_at)
    cur.execute(sql, args)
    conn.commit()
    return cur.lastrowid


class TestEmailInsights:
    """Tests for GET /dashboard/email-insights."""

    def test_insights_returns_structure(self, auth_client, db_connection):
        """Should return items, total, page, page_size."""
        res = auth_client.get("/dashboard/email-insights")
        assert res.status_code == 200
        data = res.json()
        for key in ("items", "total", "page", "page_size"):
            assert key in data, f"Missing key: {key}"

    def test_insights_empty_returns_zero(self, auth_client, db_connection):
        """Empty DB → total == 0, items == []."""
        res = auth_client.get("/dashboard/email-insights")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_insights_includes_confidence_and_reco(self, auth_client, db_connection):
        """Rows with confidence / ai_recommendation appear in the response."""
        conn = sqlite3.connect(db_connection)
        _insert_insight(conn, "mgr@corp.com", "Q2 Review",
                        confidence=88, ai_recommendation="Lên lịch họp sớm")
        conn.close()

        res = auth_client.get("/dashboard/email-insights")
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["confidence"] == 88
        assert items[0]["ai_recommendation"] == "Lên lịch họp sớm"

    def test_insights_date_from_filter(self, auth_client, db_connection):
        """Rows before date_from are excluded; rows on or after are included."""
        conn = sqlite3.connect(db_connection)
        _insert_insight(conn, "old@corp.com", "Old Email",
                        created_at="2026-01-01 10:00:00")
        _insert_insight(conn, "new@corp.com", "New Email",
                        created_at="2026-06-10 10:00:00")
        conn.close()

        res = auth_client.get("/dashboard/email-insights?date_from=2026-06-01")
        assert res.status_code == 200
        data = res.json()
        senders = [i["sender"] for i in data["items"]]
        assert "new@corp.com" in senders
        assert "old@corp.com" not in senders

    def test_insights_sort_by_priority(self, auth_client, db_connection):
        """sort_by=priority puts urgent before low."""
        conn = sqlite3.connect(db_connection)
        _insert_insight(conn, "a@x.com", "Low Pri", priority="low")
        _insert_insight(conn, "b@x.com", "Urgent Pri", priority="urgent")
        conn.close()

        res = auth_client.get("/dashboard/email-insights?sort_by=priority")
        assert res.status_code == 200
        items = res.json()["items"]
        priorities = [i["priority"] for i in items]
        assert priorities.index("urgent") < priorities.index("low")

    def test_insights_category_filter(self, auth_client, db_connection):
        """category=meeting returns only meeting rows."""
        conn = sqlite3.connect(db_connection)
        _insert_insight(conn, "a@x.com", "Meeting X", category="meeting")
        _insert_insight(conn, "b@x.com", "Report Y",  category="report")
        conn.close()

        res = auth_client.get("/dashboard/email-insights?category=meeting")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["category"] == "meeting"

    def test_insights_is_read_filter(self, auth_client, db_connection):
        """is_read=0 excludes rows that are already marked read."""
        conn = sqlite3.connect(db_connection)
        _insert_insight(conn, "unread@x.com", "Unread",  is_read=0)
        _insert_insight(conn, "read@x.com",   "Already", is_read=1)
        conn.close()

        res = auth_client.get("/dashboard/email-insights?is_read=0")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["sender"] == "unread@x.com"

    def test_insights_requires_auth(self, test_client):
        """Without a JWT the endpoint returns 401."""
        res = test_client.get("/dashboard/email-insights")
        assert res.status_code == 401


# ── Sent Emails ───────────────────────────────────────────────────────────────


def _insert_sent(conn, to_addr, subject, triggered_by="system", sent_at=None):
    """Helper — insert one sent_emails row."""
    cur = conn.cursor()
    if sent_at:
        cur.execute(
            "INSERT INTO sent_emails (to_addr, subject, triggered_by, sent_at) VALUES (?,?,?,?)",
            (to_addr, subject, triggered_by, sent_at),
        )
    else:
        cur.execute(
            "INSERT INTO sent_emails (to_addr, subject, triggered_by) VALUES (?,?,?)",
            (to_addr, subject, triggered_by),
        )
    conn.commit()
    return cur.lastrowid


class TestSentEmails:
    """Tests for GET /dashboard/sent-emails."""

    def test_sent_emails_returns_structure(self, auth_client, db_connection):
        """Should return items, total, page, page_size."""
        res = auth_client.get("/dashboard/sent-emails")
        assert res.status_code == 200
        data = res.json()
        for key in ("items", "total", "page", "page_size"):
            assert key in data, f"Missing key: {key}"

    def test_sent_emails_empty_returns_zero(self, auth_client, db_connection):
        """Empty table → total == 0, items == []."""
        res = auth_client.get("/dashboard/sent-emails")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_sent_emails_row_appears(self, auth_client, db_connection):
        """Inserted rows appear with correct fields."""
        conn = sqlite3.connect(db_connection)
        _insert_sent(conn, "alice@corp.com", "Thu moi hop", triggered_by="meeting_invite")
        conn.close()

        res = auth_client.get("/dashboard/sent-emails")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["to_addr"] == "alice@corp.com"
        assert item["subject"] == "Thu moi hop"
        assert item["triggered_by"] == "meeting_invite"
        assert "sent_at" in item

    def test_sent_emails_date_from_filter(self, auth_client, db_connection):
        """Rows before date_from are excluded."""
        conn = sqlite3.connect(db_connection)
        _insert_sent(conn, "old@x.com", "Old", sent_at="2026-01-01 09:00:00")
        _insert_sent(conn, "new@x.com", "New", sent_at="2026-06-10 09:00:00")
        conn.close()

        res = auth_client.get("/dashboard/sent-emails?date_from=2026-06-01")
        assert res.status_code == 200
        data = res.json()
        recipients = [i["to_addr"] for i in data["items"]]
        assert "new@x.com" in recipients
        assert "old@x.com" not in recipients

    def test_sent_emails_date_to_filter(self, auth_client, db_connection):
        """Rows after date_to are excluded."""
        conn = sqlite3.connect(db_connection)
        _insert_sent(conn, "early@x.com", "Early", sent_at="2026-01-01 09:00:00")
        _insert_sent(conn, "late@x.com",  "Late",  sent_at="2026-12-31 09:00:00")
        conn.close()

        res = auth_client.get("/dashboard/sent-emails?date_to=2026-06-01")
        assert res.status_code == 200
        data = res.json()
        recipients = [i["to_addr"] for i in data["items"]]
        assert "early@x.com" in recipients
        assert "late@x.com" not in recipients

    def test_sent_emails_pagination(self, auth_client, db_connection):
        """page_size=2 returns only 2 items; total reflects full count."""
        conn = sqlite3.connect(db_connection)
        for i in range(5):
            _insert_sent(conn, f"user{i}@x.com", f"Email {i}")
        conn.close()

        res = auth_client.get("/dashboard/sent-emails?page=1&page_size=2")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_sent_emails_requires_auth(self, test_client):
        """Without a JWT the endpoint returns 401."""
        res = test_client.get("/dashboard/sent-emails")
        assert res.status_code == 401


# ── Upcoming Events ───────────────────────────────────────────────────────────

_MOCK_EVENTS = [
    {
        "summary":   "Q2 Planning",
        "start":     "2026-06-20T09:00:00+07:00",
        "end":       "2026-06-20T10:00:00+07:00",
        "location":  "Room A",
        "link":      "https://calendar.google.com/event?eid=1",
        "attendees": ["alice@corp.com", "bob@corp.com"],
    },
    {
        "summary":   "Team Sync",
        "start":     "2026-06-25T14:00:00+07:00",
        "end":       "2026-06-25T15:00:00+07:00",
        "location":  "",
        "link":      "https://calendar.google.com/event?eid=2",
        "attendees": [],
    },
]


class TestUpcomingEvents:
    """Tests for GET /dashboard/stats (upcoming events + log stats)."""

    def test_stats_returns_structure(self, auth_client, db_connection):
        """Should return upcoming_events list and stats dict."""
        with patch("app.agents.chat_agent._fetch_upcoming_events", return_value=_MOCK_EVENTS):
            res = auth_client.get("/dashboard/stats")
        assert res.status_code == 200
        data = res.json()
        assert "upcoming_events" in data
        assert "stats" in data

    def test_stats_upcoming_events_content(self, auth_client, db_connection):
        """upcoming_events should contain the mocked event fields."""
        with patch("app.agents.chat_agent._fetch_upcoming_events", return_value=_MOCK_EVENTS):
            res = auth_client.get("/dashboard/stats")
        assert res.status_code == 200
        events = res.json()["upcoming_events"]
        assert len(events) == 2
        first = events[0]
        assert first["summary"] == "Q2 Planning"
        assert first["location"] == "Room A"
        assert "attendees" in first
        assert len(first["attendees"]) == 2

    def test_stats_range_days_7(self, auth_client, db_connection):
        """range_days=7 is forwarded to _fetch_upcoming_events."""
        with patch("app.agents.chat_agent._fetch_upcoming_events", return_value=[]) as mock_fn:
            auth_client.get("/dashboard/stats?range_days=7")
        mock_fn.assert_called_once_with(range_days=7)

    def test_stats_range_days_30(self, auth_client, db_connection):
        """range_days=30 is forwarded to _fetch_upcoming_events."""
        with patch("app.agents.chat_agent._fetch_upcoming_events", return_value=[]) as mock_fn:
            auth_client.get("/dashboard/stats?range_days=30")
        mock_fn.assert_called_once_with(range_days=30)

    def test_stats_empty_events(self, auth_client, db_connection):
        """When no events, upcoming_events list is empty."""
        with patch("app.agents.chat_agent._fetch_upcoming_events", return_value=[]):
            res = auth_client.get("/dashboard/stats")
        assert res.status_code == 200
        assert res.json()["upcoming_events"] == []

    def test_stats_requires_auth(self, test_client):
        """Without a JWT the endpoint returns 401."""
        res = test_client.get("/dashboard/stats")
        assert res.status_code == 401


# ── AI Activity Log ────────────────────────────────────────────────────────────


def _insert_log(conn, agent, status, payload=None, timestamp=None):
    """Helper — insert one system_logs row."""
    cur = conn.cursor()
    payload_str = json.dumps(payload or {})
    if timestamp:
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload, timestamp) VALUES (?,?,?,?)",
            (agent, status, payload_str, timestamp),
        )
    else:
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload) VALUES (?,?,?)",
            (agent, status, payload_str),
        )
    conn.commit()
    return cur.lastrowid


class TestActivityLog:
    """Tests for GET /dashboard/logs as used by the AI Activity Log UI section."""

    def test_logs_returns_structure(self, auth_client, db_connection):
        """Should return items, total, page, page_size."""
        res = auth_client.get("/dashboard/logs")
        assert res.status_code == 200
        data = res.json()
        for key in ("items", "total", "page", "page_size"):
            assert key in data

    def test_logs_item_fields(self, auth_client, db_connection):
        """Each log item should have event_id, agent, status, payload, timestamp."""
        conn = sqlite3.connect(db_connection)
        _insert_log(conn, "orchestrator", "success", {"flow": "schedule_flow"})
        conn.close()

        res = auth_client.get("/dashboard/logs")
        assert res.status_code == 200
        item = res.json()["items"][0]
        for field in ("event_id", "agent", "status", "payload", "timestamp"):
            assert field in item, f"Missing field: {field}"
        assert item["agent"] == "orchestrator"
        assert item["status"] == "success"

    def test_logs_date_to_filter(self, auth_client, db_connection):
        """date_to bound excludes newer logs."""
        conn = sqlite3.connect(db_connection)
        _insert_log(conn, "email_agent", "old", timestamp="2026-01-15 10:00:00")
        _insert_log(conn, "email_agent", "new", timestamp="2026-07-01 10:00:00")
        conn.close()

        res = auth_client.get("/dashboard/logs?date_to=2026-06-01")
        assert res.status_code == 200
        data = res.json()
        statuses = [i["status"] for i in data["items"]]
        assert "old" in statuses
        assert "new" not in statuses

    def test_logs_agent_and_status_combined(self, auth_client, db_connection):
        """Combining agent + status filters correctly narrows results."""
        conn = sqlite3.connect(db_connection)
        _insert_log(conn, "orchestrator", "success")
        _insert_log(conn, "orchestrator", "error")
        _insert_log(conn, "email_agent",  "success")
        conn.close()

        res = auth_client.get("/dashboard/logs?agent=orchestrator&status=success")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["agent"] == "orchestrator"
        assert data["items"][0]["status"] == "success"

    def test_logs_payload_search(self, auth_client, db_connection):
        """Search term matches against payload content."""
        conn = sqlite3.connect(db_connection)
        _insert_log(conn, "spam_filter", "spam", {"reason": "newsletter detected"})
        _insert_log(conn, "email_agent", "success", {"intent": "schedule"})
        conn.close()

        res = auth_client.get("/dashboard/logs?search=newsletter")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["agent"] == "spam_filter"

    def test_logs_requires_auth(self, test_client):
        """Without a JWT the endpoint returns 401."""
        res = test_client.get("/dashboard/logs")
        assert res.status_code == 401
