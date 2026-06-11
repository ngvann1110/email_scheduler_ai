"""
End-to-end tests for the full email processing pipeline.

These tests verify the complete flow:
1. Email is received via webhook
2. Email is processed by email_agent (intent parsing)
3. Calendar event is created/rescheduled
4. Notification is sent back to the sender

All external APIs are mocked. These tests verify the integration
between all components working together.

Prerequisites:
    pip install pytest pytest-asyncio httpx

Run with:
    pytest app/tests/e2e/ -v -m e2e
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Mark all tests as e2e (slow)
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class MockChatCompletion:
    """Simulate OpenAI chat completion."""

    class Choice:
        class Message:
            def __init__(self, content: str):
                self.content = content

        def __init__(self, content: str):
            self.message = self.Message(content)

    def __init__(self, content: str):
        self.choices = [self.Choice(content)]


class TestFullSchedulePipeline:
    """End-to-end test: email → schedule → notification."""

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.agents.notification_agent._get_gmail_service")
    @pytest.mark.asyncio
    async def test_full_schedule_flow(self, mock_noti_service, mock_cal_service, mock_email_client):
        """Complete schedule flow should create event and send notification."""
        # Setup mocks
        mock_email_client.chat.completions.create.return_value = MockChatCompletion(
            json.dumps({
                "intent": "schedule",
                "summary": "Project meeting",
                "time": "2026-06-10T09:00:00",
                "location": "Room A",
                "attendees": ["guest@example.com"],
                "confidence": 0.95,
                "raw_time_text": "9am Monday",
            })
        )

        # _get_service() returns mock_cal_service, so the actual service object
        # is mock_cal_service.return_value
        svc = mock_cal_service.return_value

        # Mock calendar service - no conflict
        svc.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }

        mock_insert = MagicMock()
        mock_insert.execute.return_value = {
            "id": "evt_e2e_001",
            "htmlLink": "https://calendar.google.com/event?eid=evt_e2e_001",
            "summary": "Project meeting",
        }
        svc.events.return_value.insert.return_value = mock_insert

        # Mock notification service
        mock_send = MagicMock()
        mock_send.execute.return_value = {"id": "msg_e2e_001"}
        mock_noti_service.users.return_value.messages.return_value.send.return_value = mock_send

        # Execute pipeline
        from app.orchestrator.orchestrator import run_pipeline
        from app.schemas.email import EmailSchema

        email = EmailSchema(
            sender="user@example.com",
            subject="Meeting request",
            body="Let's meet at 9am on Monday in Room A",
            timestamp="2026-06-06T10:00:00+07:00",
        )

        result = await run_pipeline(email)

        # Verify
        assert result["type"] == "schedule_flow"
        assert result["data"]["email"]["intent"] == "schedule"
        assert result["data"]["calendar"]["status"] == "created"
        assert result["data"]["notification"]["status"] == "sent"

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.agents.notification_agent._get_gmail_service")
    @pytest.mark.asyncio
    async def test_full_schedule_with_conflict(self, mock_noti_service, mock_cal_service, mock_email_client):
        """Schedule flow with conflict should find alternatives and notify."""
        mock_email_client.chat.completions.create.return_value = MockChatCompletion(
            json.dumps({
                "intent": "schedule",
                "summary": "Team standup",
                "time": "2026-06-10T09:00:00",
                "location": "Room B",
                "attendees": ["team@example.com"],
                "confidence": 0.95,
                "raw_time_text": "9am Monday",
            })
        )

        svc = mock_cal_service.return_value

        # Mock calendar - conflict
        svc.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-06-10T09:00:00Z", "end": "2026-06-10T10:00:00Z"}]
                }
            }
        }

        # Mock notification
        mock_send = MagicMock()
        mock_send.execute.return_value = {"id": "msg_e2e_002"}
        mock_noti_service.users.return_value.messages.return_value.send.return_value = mock_send

        from app.orchestrator.orchestrator import run_pipeline
        from app.schemas.email import EmailSchema

        email = EmailSchema(
            sender="user@example.com",
            subject="Team standup",
            body="Let's have standup at 9am Monday",
            timestamp="2026-06-06T10:00:00+07:00",
        )

        result = await run_pipeline(email)

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "conflict"
        assert result["data"]["notification"]["status"] == "sent"


class TestFullPipelineWithEvaluation:
    """End-to-end test: pipeline with evaluation and retry."""

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.agents.notification_agent._get_gmail_service")
    async def test_pipeline_with_retry(self, mock_noti_service, mock_cal_service, mock_email_client):
        """Pipeline should retry on failure and eventually succeed."""
        mock_email_client.chat.completions.create.return_value = MockChatCompletion(
            json.dumps({
                "intent": "schedule",
                "summary": "Important meeting",
                "time": "2026-06-10T09:00:00",
                "location": "Conference Room",
                "attendees": ["guest@example.com"],
                "confidence": 0.95,
                "raw_time_text": "9am Monday",
            })
        )

        svc = mock_cal_service.return_value

        svc.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }

        # Calendar fails first time, succeeds second time
        call_count = [0]

        def mock_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary API error")
            return {"id": "evt_retry_001", "htmlLink": "http://link"}

        mock_insert = MagicMock()
        mock_insert.execute.side_effect = mock_execute
        svc.events.return_value.insert.return_value = mock_insert

        mock_send = MagicMock()
        mock_send.execute.return_value = {"id": "msg_e2e_004"}
        mock_noti_service.users.return_value.messages.return_value.send.return_value = mock_send

        from app.agents.evaluation_agent import evaluate_and_retry
        from app.orchestrator.orchestrator import run_pipeline
        from app.schemas.email import EmailSchema

        email = EmailSchema(
            sender="user@example.com",
            subject="Important meeting",
            body="Let's meet at 9am Monday in Conference Room",
            timestamp="2026-06-06T10:00:00+07:00",
        )

        # Lần 1: calendar lỗi → evaluate trả False → retry
        # Lần 2: calendar thành công → evaluate trả True → done
        eval_call_count = [0]

        def mock_evaluate(r):
            eval_call_count[0] += 1
            if eval_call_count[0] == 1:
                return {"acceptable": False, "reason": "calendar error, retrying"}
            return {"acceptable": True, "reason": "ok"}

        with patch("app.agents.evaluation_agent.asyncio.sleep"), \
                patch("app.agents.evaluation_agent.evaluate_email", side_effect=mock_evaluate):
            result = await evaluate_and_retry(run_pipeline, email)

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "created"
