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

    def test_stats_endpoint_returns_structure(self, test_client, db_connection):
        """Should return stats with total, by_status, by_agent."""
        response = test_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data
        assert "upcoming_events" in data
        assert "total" in data["stats"]
        assert "by_status" in data["stats"]
        assert "by_agent" in data["stats"]

    def test_stats_with_seeded_data(self, test_client, seed_logs):
        """Should reflect seeded log data in stats."""
        response = test_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total"] == 8
        assert data["stats"]["by_agent"]["email_agent"] == 2

    def test_stats_empty_db(self, test_client, db_connection):
        """Should return zeros when DB is empty."""
        response = test_client.get("/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total"] == 0
        assert data["stats"]["by_status"] == {}
        assert data["stats"]["by_agent"] == {}


class TestDashboardLogs:
    """Tests for GET /dashboard/logs."""

    def test_logs_endpoint_returns_structure(self, test_client, db_connection):
        """Should return items, total, page, page_size."""
        response = test_client.get("/dashboard/logs")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_logs_with_seeded_data(self, test_client, seed_logs):
        """Should return all seeded logs."""
        response = test_client.get("/dashboard/logs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 8
        assert len(data["items"]) == 8

    def test_logs_pagination(self, test_client, seed_logs):
        """Should respect page_size parameter."""
        response = test_client.get("/dashboard/logs?page_size=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 3
        assert data["page_size"] == 3
        assert data["total"] == 8

    def test_logs_page_2(self, test_client, seed_logs):
        """Should return correct page 2 results."""
        response = test_client.get("/dashboard/logs?page=2&page_size=3")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) == 3

    def test_logs_filter_by_agent(self, test_client, seed_logs):
        """Should filter logs by agent."""
        response = test_client.get("/dashboard/logs?agent=email_agent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["agent"] == "email_agent"

    def test_logs_filter_by_status(self, test_client, seed_logs):
        """Should filter logs by status."""
        response = test_client.get("/dashboard/logs?status=spam")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "spam"

    def test_logs_search(self, test_client, seed_logs):
        """Should search logs by payload content."""
        response = test_client.get("/dashboard/logs?search=newsletter")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_logs_combined_filters(self, test_client, seed_logs):
        """Should support combined agent + status filter."""
        response = test_client.get(
            "/dashboard/logs?agent=email_agent&status=schedule")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_logs_empty_result(self, test_client, db_connection):
        """Should return empty items when no logs match."""
        response = test_client.get("/dashboard/logs?agent=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_logs_date_filter(self, test_client, db_connection):
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

        response = test_client.get("/dashboard/logs?date_from=2026-06-01")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "new"
