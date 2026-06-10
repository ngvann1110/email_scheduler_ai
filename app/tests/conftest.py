"""
Shared pytest fixtures for the entire test suite.

Provides:
- Temporary SQLite database (in-memory) for DB tests
- Mocked Google API services (Calendar, Gmail)
- Mocked OpenAI client
- Test EmailSchema factory
- FastAPI test client
"""

from fastapi.testclient import TestClient
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta, timezone
import os
import sys
import json
import sqlite3

# ── Ensure critical env vars are set BEFORE any app module import ────────────
# Required by app.core.config.Settings (validated at import time)
os.environ.setdefault("OPENAI_API_KEY", "sk-test-mock-key-for-testing")
os.environ.setdefault("ORGANIZER_EMAIL", "test-organizer@example.com")
os.environ.setdefault("GOOGLE_CREDENTIALS_PATH", "")
os.environ.setdefault("GOOGLE_TOKEN_PATH", "")
os.environ.setdefault("DATABASE_PATH", ":memory:")


# ── Ensure the project root is on sys.path ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # app/
APP_ROOT = PROJECT_ROOT  # app/
sys.path.insert(0, str(PROJECT_ROOT.parent))  # email_scheduler_ai/

# ── Database fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_path(tmp_path) -> str:
    """Create a temporary SQLite database file for testing."""
    db_path = tmp_path / "test_logs.db"
    return str(db_path)


@pytest.fixture
def db_connection(temp_db_path: str):
    """Provide a connection to a temporary SQLite DB with the system_logs table."""
    # Patch DB_NAME before importing sqlite module
    import app.db.sqlite as sqlite_mod
    original_db_name = sqlite_mod.DB_NAME
    sqlite_mod.DB_NAME = temp_db_path

    # Init the system_logs table
    conn = sqlite3.connect(temp_db_path)
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
    conn.commit()
    conn.close()

    # Also init the users table + test user so auth-protected dashboard
    # endpoints can resolve the test user via get_current_user.
    import app.db.sqlite as _sql_mod
    _orig = _sql_mod.DB_NAME
    _sql_mod.DB_NAME = temp_db_path
    from app.db.sqlite import init_db as _init_db
    _init_db()
    _conn = _sql_mod.get_connection()
    _conn.execute(
        "INSERT OR IGNORE INTO users (google_id, email, name) VALUES (?, ?, ?)",
        ("test-google-id", "test@example.com", "Test User"),
    )
    _conn.commit()
    _conn.close()
    _sql_mod.DB_NAME = _orig

    yield temp_db_path

    # Restore original
    sqlite_mod.DB_NAME = original_db_name


@pytest.fixture
def seed_logs(db_connection: str):
    """Insert sample log entries into the temp DB for query tests."""
    conn = sqlite3.connect(db_connection)
    cur = conn.cursor()
    sample_logs = [
        ("email_agent", "schedule", json.dumps(
            {"intent": "schedule", "confidence": 0.95})),
        ("calendar_agent", "created", json.dumps(
            {"status": "created", "event_id": "evt_001"})),
        ("notification_agent", "sent", json.dumps(
            {"status": "sent", "to": "test@example.com"})),
        ("spam_filter", "spam", json.dumps(
            {"reason": "sender contains 'newsletter'"})),
        ("orchestrator", "success", json.dumps({"flow": "schedule_flow"})),
        ("email_agent", "schedule", json.dumps(
            {"intent": "schedule", "confidence": 0.88})),
        ("calendar_agent", "conflict", json.dumps(
            {"status": "conflict", "busy_slots": []})),
        ("evaluation_agent", "success", json.dumps(
            {"attempt": 1, "is_success": True})),
    ]
    for agent, status, payload in sample_logs:
        cur.execute(
            "INSERT INTO system_logs (agent, status, payload) VALUES (?, ?, ?)",
            (agent, status, payload),
        )
    conn.commit()
    conn.close()
    return db_connection


# ── Mock Google API services ─────────────────────────────────────────────────

@pytest.fixture
def mock_calendar_service():
    """Create a mock Google Calendar service with controlled responses."""
    service = MagicMock()

    # Mock events().list().execute()
    mock_events_list = MagicMock()
    mock_events_list.execute.return_value = {
        "items": [
            {
                "id": "evt_001",
                "summary": "Test Meeting",
                "start": {"dateTime": "2026-06-10T09:00:00+07:00"},
                "end": {"dateTime": "2026-06-10T10:00:00+07:00"},
                "location": "Room A",
                "htmlLink": "https://calendar.google.com/event?eid=evt_001",
                "attendees": [
                    {"email": "attendee@example.com"},
                    {"email": "nhokstupid2811@gmail.com"},
                ],
            }
        ]
    }
    service.events().list.return_value = mock_events_list

    # Mock events().insert().execute()
    mock_events_insert = MagicMock()
    mock_events_insert.execute.return_value = {
        "id": "evt_new_001",
        "htmlLink": "https://calendar.google.com/event?eid=evt_new_001",
        "summary": "New Meeting",
    }
    service.events().insert.return_value = mock_events_insert

    # Mock events().delete().execute()
    mock_events_delete = MagicMock()
    mock_events_delete.execute.return_value = {}
    service.events().delete.return_value = mock_events_delete

    # Mock events().update().execute()
    mock_events_update = MagicMock()
    mock_events_update.execute.return_value = {
        "id": "evt_001",
        "htmlLink": "https://calendar.google.com/event?eid=evt_001_updated",
        "summary": "Test Meeting",
    }
    service.events().update.return_value = mock_events_update

    # Mock freebusy().query().execute()
    mock_freebusy = MagicMock()
    mock_freebusy.execute.return_value = {
        "calendars": {
            "primary": {"busy": []}
        }
    }
    service.freebusy().query.return_value = mock_freebusy

    return service


@pytest.fixture
def mock_calendar_service_with_conflict():
    """Mock Calendar service that reports a conflict."""
    service = MagicMock()

    mock_freebusy = MagicMock()
    mock_freebusy.execute.return_value = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-06-10T09:00:00Z",
                        "end": "2026-06-10T10:00:00Z"}
                ]
            }
        }
    }
    service.freebusy().query.return_value = mock_freebusy

    mock_events_list = MagicMock()
    mock_events_list.execute.return_value = {"items": []}
    service.events().list.return_value = mock_events_list

    return service


@pytest.fixture
def mock_gmail_service():
    """Create a mock Gmail service for notification tests."""
    import base64
    from email.mime.text import MIMEText

    service = MagicMock()

    # Mock users().messages().send().execute()
    mock_send = MagicMock()
    mock_send.execute.return_value = {"id": "msg_001"}
    service.users().messages().send.return_value = mock_send

    # Mock users().messages().list().execute()
    mock_list = MagicMock()
    mock_list.execute.return_value = {
        "messages": [{"id": "msg_001"}, {"id": "msg_002"}]
    }
    service.users().messages().list.return_value = mock_list

    # Mock users().messages().get().execute() with a valid email format
    msg = MIMEText("Hello, this is a test email body.")
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test Subject"
    msg["Date"] = "Mon, 06 Jun 2026 10:00:00 +0700"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    mock_get = MagicMock()
    mock_get.execute.return_value = {
        "id": "msg_001",
        "raw": raw,
    }
    service.users().messages().get.return_value = mock_get

    # Mock users().messages().modify().execute()
    mock_modify = MagicMock()
    mock_modify.execute.return_value = {}
    service.users().messages().modify.return_value = mock_modify

    return service


# ── Mock OpenAI client ───────────────────────────────────────────────────────

class MockChatCompletion:
    """Simulate an OpenAI chat completion response."""

    class Choice:
        class Message:
            def __init__(self, content: str):
                self.content = content

        def __init__(self, content: str):
            self.message = self.Message(content)

    def __init__(self, content: str):
        self.choices = [self.Choice(content)]


@pytest.fixture
def mock_openai_client():
    """Patch the OpenAI client to return controlled responses."""
    with patch("app.agents.email_agent.client") as mock_client:
        mock_client.chat.completions.create.return_value = MockChatCompletion(
            json.dumps({
                "intent": "schedule",
                "summary": "Test meeting request",
                "time": "2026-06-10T09:00:00",
                "location": "Room A",
                "attendees": ["attendee@example.com"],
                "confidence": 0.95,
                "raw_time_text": "9am Monday",
            })
        )
        yield mock_client


@pytest.fixture
def mock_openai_chat_client():
    """Patch the OpenAI client for chat_agent with action response."""
    with patch("app.agents.chat_agent.client") as mock_client:
        mock_client.chat.completions.create.return_value = MockChatCompletion(
            "Tôi có thể giúp gì cho bạn?\n"
            "<action>\n"
            '{"type":"query_calendar","range_days":7}\n'
            "</action>"
        )
        yield mock_client


# ── Test EmailSchema factory ─────────────────────────────────────────────────

@pytest.fixture
def sample_email_dict():
    """Return a dict representing a typical incoming email."""
    return {
        "sender": "user@example.com",
        "subject": "Meeting request",
        "body": "Let's meet on Monday at 9am to discuss the project.",
        "timestamp": "2026-06-06T10:00:00+07:00",
    }


@pytest.fixture
def sample_email(sample_email_dict):
    """Return an EmailSchema instance for testing."""
    from app.schemas.email import EmailSchema
    return EmailSchema(**sample_email_dict)


# ── FastAPI test client ──────────────────────────────────────────────────────

@pytest.fixture
def test_client():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.sessions import SessionMiddleware
    from app.api.v1.chat import router as chat_router
    from app.api.v1.webhook import router as webhook_router
    from app.api.v1.auth import router as auth_router

    app = FastAPI(title="Email Scheduler AI - Test")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Required for auth.py request.session
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-key-for-sessions",
        same_site="lax",
        https_only=False,
    )

    import tempfile
    import app.db.sqlite as sqlite_mod
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    sqlite_mod.DB_NAME = _tmp.name
    from app.db.sqlite import init_db
    init_db()

    # Create a test user + JWT so authenticated tests can use the auth_client fixture
    import app.db.sqlite as _sql_mod
    _conn = _sql_mod.get_connection()
    _conn.execute(
        "INSERT OR IGNORE INTO users (google_id, email, name) VALUES (?, ?, ?)",
        ("test-google-id", "test@example.com", "Test User"),
    )
    _conn.commit()
    _conn.close()

    app.include_router(auth_router)
    app.include_router(webhook_router)
    app.include_router(chat_router)

    @app.get("/ui")
    def ui():
        return {"status": "ui_available"}

    return TestClient(app)


@pytest.fixture
def auth_client(test_client):
    """TestClient that automatically includes a valid JWT auth header."""
    from app.core.jwt_auth import create_access_token
    from app.db.sqlite import get_user_by_google_id

    test_user = get_user_by_google_id("test-google-id")
    test_token = create_access_token(test_user["id"], test_user["email"])
    test_client.headers["Authorization"] = f"Bearer {test_token}"
    return test_client


# ── Mock credentials / token files ───────────────────────────────────────────

@pytest.fixture
def mock_google_auth(monkeypatch, tmp_path):
    """Mock Google OAuth so no real credentials file is needed."""
    # Create dummy credentials/token files
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(json.dumps({
        "installed": {
            "client_id": "test-client-id",
            "project_id": "test-project",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "test-secret",
            "redirect_uris": ["http://localhost"]
        }
    }))

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "token": "test-token",
        "refresh_token": "test-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client-id",
        "client_secret": "test-secret",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "expiry": "2027-01-01T00:00:00Z"
    }))

    # Patch the file paths in all modules that use them
    for module_path in [
        "app.agents.calendar_agent",
        "app.agents.conflict_agent",
        "app.agents.notification_agent",
        "app.agents.chat_agent",
        "app.core.gmail_poller",
    ]:
        monkeypatch.setattr(f"{module_path}.CREDENTIALS_FILE", creds_file)
        monkeypatch.setattr(f"{module_path}.TOKEN_FILE", token_file)

    return creds_file, token_file
