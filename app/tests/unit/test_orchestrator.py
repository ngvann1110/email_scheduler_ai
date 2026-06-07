"""
Unit tests for app/orchestrator/orchestrator.py

Tests:
- run_pipeline() — all intent branches (schedule, cancel, reschedule, inquiry, other)
- Pipeline integration with mocked agents
"""

from unittest.mock import MagicMock, patch

import pytest

from app.orchestrator.orchestrator import run_pipeline


class TestRunPipeline:
    """Tests for run_pipeline()."""

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.send_notification")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow(self, mock_log, mock_notification, mock_schedule, mock_email):
        """Should execute schedule flow when intent is 'schedule'."""
        mock_email.return_value = {"intent": "schedule",
                                   "time": "2026-06-10T09:00:00", "summary": "Test"}
        mock_schedule.return_value = {
            "status": "created", "event_id": "evt_001"}
        mock_notification.return_value = {
            "status": "sent", "to": "user@example.com"}

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["email"]["intent"] == "schedule"
        assert result["data"]["calendar"]["status"] == "created"
        assert result["data"]["notification"]["status"] == "sent"
        mock_schedule.assert_called_once()
        mock_notification.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.find_alternatives")
    @patch("app.orchestrator.orchestrator.send_notification")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_with_conflict(self, mock_log, mock_notification, mock_conflict, mock_schedule, mock_email):
        """Should include conflict resolution when schedule has conflict."""
        mock_email.return_value = {
            "intent": "schedule", "time": "2026-06-10T09:00:00"}
        mock_schedule.return_value = {"status": "conflict", "busy_slots": []}
        mock_conflict.return_value = {"status": "found", "suggestions": [
            {"start": "...", "end": "...", "label": "Alternative"}]}
        mock_notification.return_value = {"status": "sent"}

        result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["calendar"]["status"] == "conflict"
        assert result["data"]["conflict"]["status"] == "found"
        mock_conflict.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_cancel")
    @patch("app.orchestrator.orchestrator.send_notification")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_cancel_flow(self, mock_log, mock_notification, mock_cancel, mock_email):
        """Should execute cancel flow when intent is 'cancel'."""
        mock_email.return_value = {
            "intent": "cancel", "time": "2026-06-10T09:00:00"}
        mock_cancel.return_value = {
            "status": "cancelled", "event_id": "evt_001"}
        mock_notification.return_value = {"status": "sent"}

        result = await run_pipeline(MagicMock())

        assert result["type"] == "cancel_flow"
        assert result["data"]["calendar"]["status"] == "cancelled"
        mock_cancel.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_reschedule")
    @patch("app.orchestrator.orchestrator.send_notification")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_reschedule_flow(self, mock_log, mock_notification, mock_reschedule, mock_email):
        """Should execute reschedule flow when intent is 'reschedule'."""
        mock_email.return_value = {
            "intent": "reschedule", "time": "2026-06-11T10:00:00", "old_time": "2026-06-10T09:00:00"}
        mock_reschedule.return_value = {
            "status": "rescheduled", "event_id": "evt_001"}
        mock_notification.return_value = {"status": "sent"}

        result = await run_pipeline(MagicMock())

        assert result["type"] == "reschedule_flow"
        assert result["data"]["calendar"]["status"] == "rescheduled"
        mock_reschedule.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.chat")
    @patch("app.orchestrator.orchestrator.send_reply")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow(self, mock_log, mock_send_reply, mock_chat, mock_email):
        """Should execute inquiry flow when intent is 'inquiry' — calls chat and sends reply."""
        mock_email.return_value = {"intent": "inquiry"}
        mock_chat.return_value = {
            "reply": "Đây là câu trả lời từ AI", "action": None}
        mock_send_reply.return_value = {"status": "sent"}

        email_obj = MagicMock()
        email_obj.body = "Khi nào có lịch trống?"
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        assert result["type"] == "inquiry_flow"
        assert result["data"]["reply"] == "Đây là câu trả lời từ AI"
        assert result["data"]["notification"]["status"] == "sent"
        mock_chat.assert_called_once()
        mock_send_reply.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.send_reply")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow(self, mock_log, mock_send_reply, mock_email):
        """Should execute other_flow when intent is 'other' — sends fixed fallback email."""
        mock_email.return_value = {"intent": "other"}
        mock_send_reply.return_value = {"status": "sent"}

        email_obj = MagicMock()
        email_obj.sender = "user@example.com"
        result = await run_pipeline(email_obj)

        assert result["type"] == "other_flow"
        assert result["data"]["notification"]["status"] == "sent"
        mock_send_reply.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.process_schedule")
    @patch("app.orchestrator.orchestrator.send_notification")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_schedule_flow_no_conflict(self, mock_log, mock_notification, mock_schedule, mock_email):
        """When schedule has no conflict, conflict_agent should not be called."""
        mock_email.return_value = {
            "intent": "schedule", "time": "2026-06-10T09:00:00"}
        mock_schedule.return_value = {
            "status": "created", "event_id": "evt_001"}
        mock_notification.return_value = {"status": "sent"}

        with patch("app.orchestrator.orchestrator.find_alternatives") as mock_conflict:
            result = await run_pipeline(MagicMock())

        assert result["type"] == "schedule_flow"
        assert result["data"]["conflict"] is None
        mock_conflict.assert_not_called()
