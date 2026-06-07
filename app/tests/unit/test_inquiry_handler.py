"""
Unit tests for inquiry and other intent handling in the orchestrator.

Tests:
- test_inquiry_flow_sends_reply
- test_other_flow_sends_fallback
- test_inquiry_flow_notification_failure
"""

from unittest.mock import MagicMock, patch

import pytest

from app.orchestrator.orchestrator import run_pipeline


class TestInquiryHandler:
    """Tests for inquiry/other intent auto-reply functionality."""

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.chat")
    @patch("app.orchestrator.orchestrator.send_reply")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow_sends_reply(self, mock_log, mock_send_reply, mock_chat, mock_email):
        """Inquiry flow should call chat_agent and send a reply to the sender."""
        mock_email.return_value = {"intent": "inquiry"}
        mock_chat.return_value = {
            "reply": "Chào bạn, hiện tại lịch của tôi còn trống vào thứ Ba tuần sau.",
            "action": None,
        }
        mock_send_reply.return_value = {
            "status": "sent", "to": "sender@example.com"}

        email_obj = MagicMock()
        email_obj.sender = "sender@example.com"
        email_obj.body = "Tuần sau có lịch trống không?"
        email_obj.subject = "Hỏi về lịch"

        result = await run_pipeline(email_obj)

        assert result["type"] == "inquiry_flow"
        assert "reply" in result["data"]
        assert len(result["data"]["reply"]) > 0
        assert result["data"]["notification"]["status"] == "sent"
        # Verify notification was called with the sender's email
        mock_send_reply.assert_called_once()
        args, kwargs = mock_send_reply.call_args
        assert kwargs["to_email"] == "sender@example.com" or args[0] is not None

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.send_reply")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow_sends_fallback(self, mock_log, mock_send_reply, mock_email):
        """Other flow should send fixed fallback email, NOT call chat_agent."""
        mock_email.return_value = {"intent": "other"}
        mock_send_reply.return_value = {
            "status": "sent", "to": "unknown@example.com"}

        email_obj = MagicMock()
        email_obj.sender = "unknown@example.com"
        email_obj.body = "Blah blah blah"
        email_obj.subject = "Random topic"

        with patch("app.orchestrator.orchestrator.chat") as mock_chat:
            result = await run_pipeline(email_obj)

        assert result["type"] == "other_flow"
        assert result["data"]["notification"]["status"] == "sent"
        # chat_agent MUST NOT be called (no token waste)
        mock_chat.assert_not_called()
        # notification must be called
        mock_send_reply.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.chat")
    @patch("app.orchestrator.orchestrator.send_reply")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow_notification_failure(
        self, mock_log, mock_send_reply, mock_chat, mock_email
    ):
        """Pipeline should not crash when notification fails, should return error status."""
        mock_email.return_value = {"intent": "inquiry"}
        mock_chat.return_value = {
            "reply": "Đây là câu trả lời.",
            "action": None,
        }
        mock_send_reply.side_effect = Exception("SMTP connection failed")

        email_obj = MagicMock()
        email_obj.sender = "sender@example.com"
        email_obj.body = "Hỏi về lịch"
        email_obj.subject = "Inquiry"

        # The orchestrator wraps the inquiry block in try/except,
        # so even if send_reply raises, the pipeline should return gracefully
        result = await run_pipeline(email_obj)

        assert result["type"] == "inquiry_flow"
        assert result["data"]["notification"]["status"] == "error"
        assert "message" in result["data"]["notification"]
        # Should still have the email result
        assert result["data"]["email"]["intent"] == "inquiry"
