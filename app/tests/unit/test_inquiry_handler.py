"""
Unit tests for inquiry and other intent handling in the orchestrator.

Tests:
- test_inquiry_flow_sends_reply    — inquiry → reply_required_flow + pending action
- test_other_flow_sends_fallback   — other → classify_intelligence → other_flow
- test_inquiry_flow_notification_failure — insight storage failure is handled gracefully
"""

from unittest.mock import MagicMock, patch

import pytest

from app.orchestrator.orchestrator import run_pipeline


class TestInquiryHandler:
    """Tests for inquiry/other intent routing in the LangGraph orchestrator."""

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow_sends_reply(self, mock_log, mock_action, mock_insight, mock_score, mock_email):
        """inquiry intent creates a reply_required pending action and returns reply_required_flow."""
        mock_email.return_value = {
            "intent": "inquiry",
            "confidence": 0.9,
            "summary": "Asking about schedule availability",
        }
        mock_insight.return_value = 1
        mock_score.return_value = 30
        mock_action.return_value = 10

        email_obj = MagicMock()
        email_obj.sender = "sender@example.com"
        email_obj.body = "Tuần sau có lịch trống không?"
        email_obj.subject = "Hỏi về lịch"

        result = await run_pipeline(email_obj)

        assert result["type"] == "reply_required_flow"
        assert result["data"]["email"]["intent"] == "inquiry"
        assert result["data"]["action_id"] == 10
        mock_action.assert_called_once()

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.classify_intelligence")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.insert_email_analysis")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_other_flow_sends_fallback(
        self, mock_log, mock_insert, mock_insight, mock_score, mock_intel, mock_email
    ):
        """other intent calls classify_intelligence and returns other_flow; no reply sent."""
        mock_email.return_value = {"intent": "other", "confidence": 0.9}
        mock_intel.return_value = {
            "category": "general",
            "importance_score": 40,
            "summary": "- General inquiry",
            "extracted_data": {},
        }
        mock_insight.return_value = 1
        mock_score.return_value = 30

        email_obj = MagicMock()
        email_obj.sender = "unknown@example.com"
        email_obj.body = "Blah blah blah"
        email_obj.subject = "Random topic"

        result = await run_pipeline(email_obj)

        assert result["type"] == "other_flow"
        assert result["data"]["intelligence"]["category"] == "general"
        mock_intel.assert_called_once_with(email_obj)

    @patch("app.orchestrator.orchestrator.process_email")
    @patch("app.orchestrator.orchestrator.priority_scoring_skill")
    @patch("app.orchestrator.orchestrator.insert_email_insight")
    @patch("app.orchestrator.orchestrator.create_pending_action")
    @patch("app.orchestrator.orchestrator.log_event")
    @pytest.mark.asyncio
    async def test_inquiry_flow_notification_failure(
        self, mock_log, mock_action, mock_insight, mock_score, mock_email
    ):
        """inquiry flow succeeds even when email insight storage raises (handled internally)."""
        mock_email.return_value = {"intent": "inquiry", "confidence": 0.9}
        mock_insight.side_effect = Exception("DB failure")
        mock_score.return_value = 30
        mock_action.return_value = 10

        email_obj = MagicMock()
        email_obj.sender = "sender@example.com"
        email_obj.body = "Hỏi về lịch"
        email_obj.subject = "Inquiry"

        result = await run_pipeline(email_obj)

        assert result["type"] == "reply_required_flow"
        assert result["data"]["email"]["intent"] == "inquiry"
        assert result["data"]["action_id"] == 10
