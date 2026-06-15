"""
Unit tests for app/orchestrator/orchestrator.py

Tests:
- run_pipeline() — all intent branches (schedule, reschedule, inquiry, other)
- Pipeline integration with mocked agents
- Note: send_notification and send_reply are no longer triggered by the orchestrator.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.orchestrator.orchestrator import run_pipeline


class TestRunPipeline:
    """Tests for run_pipeline()."""

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow(self, mock_log, mock_schedule, mock_email):
        """Should execute schedule flow when intent is 'schedule'."""
        mock_email.return_value = {
            "intent": "schedule",
            "time": "2026-06-10T09:00:00",
            "summary": "Test",
        }
        mock_schedule.return_value = {
            "status": "created",
            "event_id": "evt_001",
        }

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["email"]["intent"] == "schedule"
        assert result["data"]["calendar"]["status"] == "created"
        mock_schedule.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.find_alternatives")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_with_conflict(
        self, mock_log, mock_conflict, mock_schedule, mock_email
    ):
        """Should include conflict resolution when schedule has conflict."""
        mock_email.return_value = {
            "intent": "schedule",
            "time": "2026-06-10T09:00:00",
        }
        mock_schedule.return_value = {"status": "conflict", "busy_slots": []}
        mock_conflict.return_value = {
            "status": "found",
            "suggestions": [
                {"start": "...", "end": "...", "label": "Alternative"}
            ],
        }

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "conflict"
        assert result["data"]["conflict"]["status"] == "found"
        mock_conflict.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_reschedule")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_reschedule_flow(
        self, mock_log, mock_reschedule, mock_email
    ):
        """Should execute reschedule flow when intent is 'reschedule'."""
        mock_email.return_value = {
            "intent": "reschedule",
            "time": "2026-06-11T10:00:00",
            "old_time": "2026-06-10T09:00:00",
        }
        mock_reschedule.return_value = {
            "status": "rescheduled",
            "event_id": "evt_001",
        }

        result = await run_pipeline(MagicMock())

        assert result["type"] == "reschedule_flow"
        assert result["data"]["calendar"]["status"] == "rescheduled"
        mock_reschedule.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow(self, mock_log, mock_email):
        """Should execute inquiry flow when intent is 'inquiry' — logged, no auto-reply."""
        mock_email.return_value = {"intent": "inquiry"}

        email_obj = MagicMock()
        email_obj.body = "Khi nào có lịch trống?"
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        assert result["type"] == "inquiry_flow"
        assert result["data"]["email"]["intent"] == "inquiry"

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.classify_intelligence")
    @patch("app.orchestrator.orchestrator.insert_email_analysis")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow(
        self, mock_log, mock_insert, mock_intel, mock_email
    ):
        """Should execute other_flow when intent is 'other' — calls intelligence agent, no auto-reply."""
        mock_email.return_value = {"intent": "other"}
        mock_intel.return_value = {
            "category": "report",
            "importance_score": 78,
            "summary": "- Doanh thu Q2 tăng 12%",
            "extracted_data": {"project": "Test"},
        }

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
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow_intelligence_error_handled(
        self, mock_log, mock_intel, mock_email
    ):
        """When intelligence agent fails, should still complete gracefully with error info."""
        mock_email.return_value = {"intent": "other"}
        mock_intel.side_effect = Exception("AI failure")

        email_obj = MagicMock()
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        # Should still complete with error handling
        assert result["type"] == "other_flow"
        assert result["data"]["email"]["intent"] == "other"
        assert result["data"]["error"] == "AI failure"

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_no_conflict(
        self, mock_log, mock_schedule, mock_email
    ):
        """When schedule has no conflict, conflict_agent should not be called."""
        mock_email.return_value = {
            "intent": "schedule",
            "time": "2026-06-10T09:00:00",
        }
        mock_schedule.return_value = {
            "status": "created",
            "event_id": "evt_001",
        }

        with patch(
            "app.orchestrator.orchestrator.find_alternatives"
        ) as mock_conflict:
            result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["conflict"] is None
        mock_conflict.assert_not_called()
