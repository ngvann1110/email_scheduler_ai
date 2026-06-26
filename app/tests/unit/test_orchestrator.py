"""
Unit tests for app/orchestrator/orchestrator.py

Tests:
- run_pipeline() — all intent branches (schedule, reschedule, inquiry, other)
- Pipeline integration with mocked agents
- Note: the orchestrator uses a HITL contract — no events are created, no emails
  sent. Each flow stores a pending_action row for user review via the Dashboard.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.orchestrator.orchestrator import run_pipeline


class TestRunPipeline:
    """Tests for run_pipeline()."""

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.check_calendar_availability")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow(self, mock_log, mock_action, mock_insight, mock_score, mock_cal, mock_email):
        """Should execute schedule flow when intent is 'schedule'."""
        mock_email.return_value = {
            "intent": "schedule",
            "confidence": 0.9,
            "time": "2026-06-10T09:00:00",
            "summary": "Test",
        }
        mock_cal.return_value = {"status": "free", "start": "2026-06-10T09:00:00"}
        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["email"]["intent"] == "schedule"
        assert result["data"]["calendar"]["status"] == "free"
        assert result["data"]["action_id"] == 42
        mock_cal.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.check_calendar_availability")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_with_conflict(
        self, mock_log, mock_action, mock_insight, mock_score, mock_cal, mock_email
    ):
        """Should include conflict calendar status when the requested slot is busy."""
        mock_email.return_value = {
            "intent": "schedule",
            "confidence": 0.9,
            "time": "2026-06-10T09:00:00",
        }
        mock_cal.return_value = {
            "status": "conflict",
            "busy_slots": [{"start": "2026-06-10T09:00:00Z", "end": "2026-06-10T10:00:00Z"}],
        }
        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "conflict"
        assert result["data"]["action_id"] == 42
        mock_cal.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.check_reschedule_availability")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_reschedule_flow(
        self, mock_log, mock_action, mock_insight, mock_score, mock_cal, mock_email
    ):
        """Should execute reschedule flow when intent is 'reschedule'."""
        mock_email.return_value = {
            "intent": "reschedule",
            "confidence": 0.9,
            "time": "2026-06-11T10:00:00",
            "old_time": "2026-06-10T09:00:00",
        }
        mock_cal.return_value = {"status": "free", "start": "2026-06-11T10:00:00"}
        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        result = await run_pipeline(MagicMock())

        assert result["type"] == "reschedule_flow"
        assert result["data"]["calendar"]["status"] == "free"
        assert result["data"]["action_id"] == 42
        mock_cal.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow(self, mock_log, mock_action, mock_insight, mock_score, mock_email):
        """inquiry intent routes to reply_required_flow and creates a pending action."""
        mock_email.return_value = {"intent": "inquiry", "confidence": 0.9}
        mock_insight.return_value = 1
        mock_score.return_value = 30
        mock_action.return_value = 10

        email_obj = MagicMock()
        email_obj.body = "Khi nào có lịch trống?"
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        assert result["type"] == "reply_required_flow"
        assert result["data"]["email"]["intent"] == "inquiry"
        assert result["data"]["action_id"] == 10

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.classify_intelligence")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.insert_email_analysis")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow(
        self, mock_log, mock_insert, mock_insight, mock_score, mock_intel, mock_email
    ):
        """Should execute other_flow when intent is 'other' — calls intelligence agent."""
        mock_email.return_value = {
            "intent": "other",
            "confidence": 0.9,
        }
        mock_intel.return_value = {
            "category": "report",
            "importance_score": 78,
            "summary": "- Doanh thu Q2 tăng 12%",
            "extracted_data": {"project": "Test"},
        }
        mock_insight.return_value = 1
        mock_score.return_value = 50

        email_obj = MagicMock()
        email_obj.sender = "user@example.com"
        email_obj.subject = "Test"
        result = await run_pipeline(email_obj)

        assert result["type"] == "other_flow"
        assert result["data"]["intelligence"]["category"] == "report"
        assert result["data"]["intelligence"]["importance_score"] == 78
        mock_intel.assert_called_once_with(email_obj)
        mock_insert.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.classify_intelligence")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow_intelligence_error_handled(
        self, mock_log, mock_insight, mock_score, mock_intel, mock_email
    ):
        """When intelligence agent fails, should still complete gracefully with error info."""
        mock_email.return_value = {"intent": "other", "confidence": 0.9}
        mock_intel.side_effect = Exception("AI failure")
        mock_insight.return_value = 1
        mock_score.return_value = 50

        email_obj = MagicMock()
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        assert result["type"] == "other_flow"
        assert result["data"]["email"]["intent"] == "other"
        assert result["data"]["error"] == "AI failure"

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.check_calendar_availability")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_no_conflict(
        self, mock_log, mock_action, mock_insight, mock_score, mock_cal, mock_email
    ):
        """When calendar is free, schedule_flow is returned; no conflict data in output."""
        mock_email.return_value = {
            "intent": "schedule",
            "confidence": 0.9,
            "time": "2026-06-10T09:00:00",
        }
        mock_cal.return_value = {"status": "free", "start": "2026-06-10T09:00:00"}
        mock_insight.return_value = 1
        mock_score.return_value = 50
        mock_action.return_value = 42

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "free"
        assert result["data"]["action_id"] == 42
        assert "conflict" not in result["data"]
