"""
Integration tests for Dashboard API endpoints.

Tests:
- GET /dashboard/stats — returns stats and upcoming events
- GET /dashboard/logs — returns paginated/filtered logs
- GET /dashboard/logs with filters (agent, status, search, pagination)
"""

import json
import sqlite3

import pytest

from app.db.sqlite import get_log_stats, get_logs


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
    """Tests for GET /dashboard/recent-emails."""

    def test_recent_emails_returns_structure(self, auth_client):
        """Should return items, total, page, page_size."""
        response = auth_client.get("/dashboard/recent-emails")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_recent_emails_empty_db(self, auth_client):
        """Should return empty items when no analyzed emails."""
        response = auth_client.get("/dashboard/recent-emails")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_recent_emails_with_data(self, auth_client, db_connection):
        """Should return seeded email_intelligence data."""
        from app.db.sqlite import get_connection, init_db

        init_db()
        conn = get_connection()
        cur = conn.cursor()
        samples = [
            ("sender_a@test.com", "Report Q2", "report",
             "Báo cáo Q2", '{}', 75),
            ("sender_b@test.com", "Meeting request", "meeting",
             "Họp dự án", '{}', 85),
            ("sender_c@test.com", "Hỗ trợ kỹ thuật", "support",
             "Cần hỗ trợ API", '{}', 45),
            ("sender_d@test.com", "Partnership offer", "partnership",
             "Đề xuất hợp tác", '{}', 90),
            ("sender_e@test.com", "Thông báo", "announcement",
             "Thông báo bảo trì", '{}', 30),
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
        assert data["total"] == 5
        assert len(data["items"]) == 5
        # Each item should have required fields
        for item in data["items"]:
            assert "sender" in item
            assert "category" in item
            assert "summary" in item
            assert "importance_score" in item
            assert "processed_at" in item

    def test_recent_emails_pagination(self, auth_client, db_connection):
        """Should respect page_size parameter."""
        from app.db.sqlite import get_connection, init_db

        init_db()
        conn = get_connection()
        cur = conn.cursor()
        for i in range(10):
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (f"sender{i}@test.com", f"Subject {i}", "other",
                 f"Summary {i}", '{}', 50 - i),
            )
        conn.commit()
        conn.close()

        response = auth_client.get("/dashboard/recent-emails?page_size=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        assert data["page_size"] == 3
        assert data["total"] == 10

    def test_recent_emails_page_2(self, auth_client, db_connection):
        """Should return correct page 2 results."""
        from app.db.sqlite import get_connection, init_db

        init_db()
        conn = get_connection()
        cur = conn.cursor()
        for i in range(5):
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (f"sender{i}@test.com", f"Subject {i}", "other",
                 f"Summary {i}", '{}', 50 - i),
            )
        conn.commit()
        conn.close()

        response = auth_client.get(
            "/dashboard/recent-emails?page=2&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 2

    def test_recent_emails_sort_by_importance(self, auth_client, db_connection):
        """Should sort by importance_score DESC when sort_by=importance."""
        from app.db.sqlite import get_connection, init_db

        init_db()
        conn = get_connection()
        cur = conn.cursor()
        # Insert with varying importance scores
        scores = [30, 90, 50, 80, 40]
        for i, score in enumerate(scores):
            cur.execute(
                "INSERT INTO email_intelligence "
                "(email_id, sender, subject, category, summary, "
                "extracted_data_json, importance_score) "
                "VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (f"sender{i}@test.com", f"Subject {i}", "report",
                 f"Summary {i}", '{}', score),
            )
        conn.commit()
        conn.close()

        response = auth_client.get(
            "/dashboard/recent-emails?sort_by=importance")
        assert response.status_code == 200
        data = response.json()
        items = data["items"]
        # First item should have highest importance
        assert items[0]["importance_score"] == 90
        # Second should be 80
        assert items[1]["importance_score"] == 80
        assert items[2]["importance_score"] == 50
