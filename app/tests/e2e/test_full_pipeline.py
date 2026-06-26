"""
End-to-end tests for the full email processing pipeline.

These tests verify the complete flow with mocked external APIs (OpenAI, Google
Calendar). The orchestrator follows a HITL contract — no events are created and
no emails are sent; instead, a pending_action row is stored for user review.

All external APIs and the DB layer are mocked. These tests verify the integration
between all pipeline components working together.

Prerequisites:
    pip install pytest pytest-asyncio httpx

Run with:
    pytest app/tests/e2e/ -v -m e2e
"""

import json
from unittest.mock import patch, MagicMock

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
    """End-to-end test: email → calendar check → pending action."""

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @pytest.mark.asyncio
    async def test_full_schedule_flow(
        self, mock_action, mock_insight, mock_score, mock_cal_service, mock_email_client
    ):
        """Complete schedule flow checks availability and creates a pending action."""
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

        svc = mock_cal_service.return_value
        svc.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }

        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        from app.orchestrator.orchestrator import run_pipeline
        from app.schemas.email import EmailSchema

        email = EmailSchema(
            sender="user@example.com",
            subject="Meeting request",
            body="Let's meet at 9am on Monday in Room A",
            timestamp="2026-06-06T10:00:00+07:00",
        )

        result = await run_pipeline(email)

        assert result["type"] == "schedule_flow"
        assert result["data"]["email"]["intent"] == "schedule"
        assert result["data"]["calendar"]["status"] == "free"
        assert result["data"]["action_id"] == 42

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @pytest.mark.asyncio
    async def test_full_schedule_with_conflict(
        self, mock_action, mock_insight, mock_score, mock_cal_service, mock_email_client
    ):
        """Schedule flow with conflict stores a pending action with conflict calendar data."""
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
        svc.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-06-10T09:00:00Z", "end": "2026-06-10T10:00:00Z"}]
                }
            }
        }

        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

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
        assert result["data"]["action_id"] == 42


class TestFullPipelineWithEvaluation:
    """End-to-end test: pipeline with evaluation and retry."""

    @patch("app.agents.email_agent.client")
    @patch("app.agents.calendar_agent._get_service")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    async def test_pipeline_with_retry(
        self, mock_action, mock_insight, mock_score, mock_cal_service, mock_email_client
    ):
        """Pipeline should retry on unacceptable evaluation and return the accepted result."""
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

        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        from app.agents.evaluation_agent import evaluate_and_retry
        from app.orchestrator.orchestrator import run_pipeline
        from app.schemas.email import EmailSchema

        email = EmailSchema(
            sender="user@example.com",
            subject="Important meeting",
            body="Let's meet at 9am Monday in Conference Room",
            timestamp="2026-06-06T10:00:00+07:00",
        )

        eval_call_count = [0]

        def mock_evaluate(r):
            eval_call_count[0] += 1
            if eval_call_count[0] == 1:
                return {"acceptable": False, "reason": "low confidence, retrying"}
            return {"acceptable": True, "reason": "ok"}

        with patch("app.agents.evaluation_agent.asyncio.sleep"), \
                patch("app.agents.evaluation_agent.evaluate_email", side_effect=mock_evaluate):
            result = await evaluate_and_retry(run_pipeline, email)

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "free"
        assert result["data"]["action_id"] == 42
