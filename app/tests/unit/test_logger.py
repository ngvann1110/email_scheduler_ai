"""
Unit tests for app/core/logger.py

Tests:
- log_event() inserts a row into system_logs
- log_event() handles exceptions gracefully
- log_event() serializes payload to JSON
"""

import json
import sqlite3

import pytest

from app.core.logger import log_event


class TestLogEvent:
    """Tests for log_event()."""

    def test_log_event_inserts_row(self, db_connection):
        """log_event should insert a row into system_logs."""
        log_event("test_agent", "test_status", {"key": "value"})

        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM system_logs")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 1

    def test_log_event_stores_correct_data(self, db_connection):
        """log_event should store agent, status, and serialized payload."""
        payload = {"intent": "schedule", "confidence": 0.95}
        log_event("email_agent", "schedule", payload)

        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute(
            "SELECT agent, status, payload FROM system_logs ORDER BY event_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "email_agent"
        assert row[1] == "schedule"
        assert json.loads(row[2]) == payload

    def test_log_event_multiple_entries(self, db_connection):
        """Multiple calls to log_event should insert multiple rows."""
        log_event("agent_a", "status_a", {"a": 1})
        log_event("agent_b", "status_b", {"b": 2})
        log_event("agent_c", "status_c", {"c": 3})

        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM system_logs")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 3

    def test_log_event_with_unicode(self, db_connection):
        """log_event should handle Unicode characters in payload."""
        payload = {"message": "Xin chào thế giới 🤖"}
        log_event("test_agent", "test", payload)

        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute(
            "SELECT payload FROM system_logs ORDER BY event_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()

        parsed = json.loads(row[0])
        assert parsed["message"] == "Xin chào thế giới 🤖"

    def test_log_event_empty_payload(self, db_connection):
        """log_event should handle empty dict payload."""
        log_event("test_agent", "test", {})

        conn = sqlite3.connect(db_connection)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM system_logs")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 1

    def test_log_event_does_not_raise_on_db_error(self, monkeypatch):
        """If DB fails, log_event should catch the exception and not raise."""
        def broken_connection():
            raise Exception("DB connection failed")

        import app.core.logger as logger_mod
        monkeypatch.setattr(logger_mod, "get_connection", broken_connection)

        # Should not raise
        log_event("test", "test", {"data": "value"})
        # If we get here, the test passes
        assert True
