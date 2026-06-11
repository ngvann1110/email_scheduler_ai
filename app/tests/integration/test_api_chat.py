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
