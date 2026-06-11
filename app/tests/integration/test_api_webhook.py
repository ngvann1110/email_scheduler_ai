"""
Integration tests for Webhook API endpoint.

Tests:
- POST /webhook/gmail — receive email webhook
- Pipeline execution via webhook
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestWebhookEndpoint:
    """Tests for POST /webhook/gmail."""

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_returns_processed(self, mock_pipeline, test_client):
        """Should return processed status with flow type."""
        mock_pipeline.return_value = {
            "type": "schedule_flow",
            "data": {"email": {"intent": "schedule"}},
        }

        response = test_client.post(
            "/webhook/gmail",
            json={
                "sender": "user@example.com",
                "subject": "Meeting request",
                "body": "Let's meet at 9am tomorrow",
                "timestamp": "2026-06-06T10:00:00+07:00",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["flow"] == "schedule_flow"

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_inquiry_flow(self, mock_pipeline, test_client):
        """Should handle inquiry flow correctly."""
        mock_pipeline.return_value = {
            "type": "inquiry_flow",
            "data": {"email": {"intent": "inquiry"}},
        }

        response = test_client.post(
            "/webhook/gmail",
            json={
                "sender": "user@example.com",
                "subject": "My schedule",
                "body": "What's on my calendar?",
                "timestamp": "2026-06-06T10:00:00+07:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["flow"] == "inquiry_flow"

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_reschedule_flow(self, mock_pipeline, test_client):
        """Should handle reschedule flow correctly."""
        mock_pipeline.return_value = {
            "type": "reschedule_flow",
            "data": {
                "email": {"intent": "reschedule"},
                "calendar": {"status": "rescheduled"},
            },
        }

        response = test_client.post(
            "/webhook/gmail",
            json={
                "sender": "user@example.com",
                "subject": "Reschedule",
                "body": "Move my meeting to Tuesday",
                "timestamp": "2026-06-06T10:00:00+07:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["flow"] == "reschedule_flow"

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_missing_fields_422(self, mock_pipeline, test_client):
        """Should return 422 for missing required fields."""
        response = test_client.post(
            "/webhook/gmail",
            json={"sender": "user@example.com"},
        )
        assert response.status_code == 422

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_invalid_timestamp_422(self, mock_pipeline, test_client):
        """Should return 422 for invalid timestamp."""
        response = test_client.post(
            "/webhook/gmail",
            json={
                "sender": "user@example.com",
                "subject": "Test",
                "body": "Test body",
                "timestamp": "invalid",
            },
        )
        assert response.status_code == 422

    @patch("app.api.v1.webhook.run_pipeline", new_callable=AsyncMock)
    def test_webhook_empty_body_allowed(self, mock_pipeline, test_client):
        """Should accept empty body field."""
        mock_pipeline.return_value = {
            "type": "summary_flow",
            "data": {},
        }

        response = test_client.post(
            "/webhook/gmail",
            json={
                "sender": "user@example.com",
                "subject": "Test",
                "body": "",
                "timestamp": "2026-06-06T10:00:00+07:00",
            },
        )
        assert response.status_code == 200
