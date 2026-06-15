"""Integration tests for the Chat API endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestChatEndpoint:
    """Tests for POST /chat."""

    def test_chat_returns_reply(self, auth_client, mock_openai_chat_client):
        """Should return a reply when given a valid message."""
        response = auth_client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert len(data["reply"]) > 0

    def test_chat_with_empty_message(self, auth_client):
        """Should handle empty message gracefully."""
        response = auth_client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": ""}]},
        )
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data

    def test_chat_with_schedule_action(self, auth_client):
        """Should return action when schedule is requested."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = type(
            "MockResponse", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": (
                            "Tôi sẽ tạo lịch.\n"
                            "<action>\n"
                            '{"type":"schedule","time":"2026-06-10T09:00:00","summary":"Project meeting","attendees":["guest@example.com"]}\n'
                            "</action>"
                        )
                    })()
                })]
            }
        )()
        with patch("app.agents.chat_agent.client", mock_client):
            response = auth_client.post(
                "/chat",
                json={
                    "messages": [{"role": "user", "content": "Schedule a meeting with guest@example.com at 9am tomorrow"}]},
            )
        assert response.status_code == 200
        data = response.json()
        assert data.get("action") is not None
        assert data["action"]["type"] == "schedule"


class TestSendEmailEndpoint:
    """Tests for POST /chat/send-email — verifies shared Gmail service is used."""

    def test_send_email_returns_ok(self, auth_client, mock_gmail_service):
        """Should return {status: ok} when Gmail API call succeeds."""
        with patch("app.api.v1.chat.get_gmail_service", return_value=mock_gmail_service):
            response = auth_client.post(
                "/chat/send-email",
                json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
            )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_send_email_calls_shared_auth_not_private_function(self, auth_client, mock_gmail_service):
        """_send_email must obtain Gmail service via app.api.v1.chat.get_gmail_service,
        not through a private duplicated OAuth function in chat.py."""
        with patch("app.api.v1.chat.get_gmail_service", return_value=mock_gmail_service) as mock_get:
            auth_client.post(
                "/chat/send-email",
                json={"to": "recipient@example.com", "subject": "Hi", "body": "Hello"},
            )
        mock_get.assert_called_once()

    def test_send_email_calls_gmail_send_api(self, auth_client, mock_gmail_service):
        """Gmail users().messages().send() must be called with the correct userId."""
        with patch("app.api.v1.chat.get_gmail_service", return_value=mock_gmail_service):
            auth_client.post(
                "/chat/send-email",
                json={"to": "recipient@example.com", "subject": "Test", "body": "Body text"},
            )
        mock_gmail_service.users().messages().send.assert_called_once()
        call_kwargs = mock_gmail_service.users().messages().send.call_args
        assert call_kwargs.kwargs.get("userId") == "me"

    def test_send_email_logs_to_sent_emails_table(self, auth_client, mock_gmail_service):
        """insert_sent_email must be called so the sent email appears in the dashboard."""
        with patch("app.api.v1.chat.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.api.v1.chat.insert_sent_email") as mock_insert:
                auth_client.post(
                    "/chat/send-email",
                    json={"to": "log@example.com", "subject": "Log test", "body": "body"},
                )
        mock_insert.assert_called_once()
        args = mock_insert.call_args.args
        assert args[0] == "log@example.com"
        assert args[1] == "Log test"

    def test_send_email_missing_field_returns_422(self, auth_client):
        """Pydantic must reject a request missing a required field."""
        response = auth_client.post(
            "/chat/send-email",
            json={"to": "recipient@example.com"},  # missing subject and body
        )
        assert response.status_code == 422


class TestConfirmInvite:
    """Tests for GET /chat/confirm/{token}."""

    def test_confirm_invalid_token(self, test_client):
        """Should return error for non-existent token."""
        response = test_client.get("/chat/confirm/nonexistent-token")
        assert response.status_code == 200
        # Endpoint returns HTMLResponse, not JSON
        assert "khong hop le" in response.text.lower() or "het han" in response.text.lower()


class TestDeclineInvite:
    """Tests for GET /chat/decline/{token}."""

    def test_decline_invalid_token(self, test_client):
        """Should return error for non-existent token."""
        response = test_client.get("/chat/decline/nonexistent-token")
        assert response.status_code == 200
        # Endpoint returns HTMLResponse, not JSON
        assert "tu choi" in response.text.lower() or "khong hop le" in response.text.lower()


class TestConfirmReschedule:
    """Tests for GET /chat/reschedule/confirm/{token}."""

    def test_confirm_reschedule_invalid_token(self, test_client):
        """Should return error for non-existent token."""
        response = test_client.get(
            "/chat/reschedule/confirm/nonexistent-token")
        assert response.status_code == 200
        # Endpoint returns HTMLResponse, not JSON
        assert "khong hop le" in response.text.lower() or "het han" in response.text.lower()


class TestDeclineReschedule:
    """Tests for GET /chat/reschedule/decline/{token}."""

    def test_decline_reschedule_invalid_token(self, test_client):
        """Should return error for non-existent token."""
        response = test_client.get(
            "/chat/reschedule/decline/nonexistent-token")
        assert response.status_code == 200
        # Endpoint returns HTMLResponse, not JSON
        assert "tu choi" in response.text.lower() or "khong hop le" in response.text.lower()
