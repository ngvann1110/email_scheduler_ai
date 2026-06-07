"""
Integration tests for app/core/gmail_poller.py

Tests:
- _parse_message() — email parsing from raw Gmail API response
- _mark_as_read() — marking messages as read
- poll_gmail() — main polling loop (with mocked Gmail API)
"""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.core.gmail_poller import _parse_message, _mark_as_read


class TestParseMessage:
    """Tests for _parse_message()."""

    def test_parse_success(self, mock_gmail_service):
        """Should parse a raw Gmail message into a dict."""
        # The mock returns base64 encoded content
        result = _parse_message(mock_gmail_service, "msg_001")
        assert result is not None
        assert "sender" in result
        assert "subject" in result
        assert "body" in result
        assert "timestamp" in result

    def test_parse_failure_returns_none(self, mock_gmail_service):
        """Should return None when parsing fails."""
        mock_gmail_service.users().messages().get(
        ).execute.side_effect = Exception("API error")
        result = _parse_message(mock_gmail_service, "msg_001")
        assert result is None


class TestMarkAsRead:
    """Tests for _mark_as_read()."""

    def test_mark_as_read_success(self, mock_gmail_service):
        """Should call modify with removeLabelIds UNREAD."""
        _mark_as_read(mock_gmail_service, "msg_001")
        mock_gmail_service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_001",
            body={"removeLabelIds": ["UNREAD"]},
        )

    def test_mark_as_read_error_does_not_raise(self, mock_gmail_service):
        """Should not raise when modify fails."""
        mock_gmail_service.users().messages().modify(
        ).execute.side_effect = Exception("API error")
        # Should not raise
        _mark_as_read(mock_gmail_service, "msg_001")
        assert True


class TestPollGmail:
    """Tests for poll_gmail()."""

    @pytest.mark.asyncio
    async def test_poll_gmail_processes_messages(self, mock_gmail_service):
        """Should process new messages and run pipeline."""
        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.is_spam", return_value=(False, "")):
                with patch("app.core.gmail_poller.evaluate_and_retry") as mock_eval:
                    mock_eval.return_value = {
                        "type": "inquiry_flow", "data": {}}

                    # Run one iteration (will hit asyncio.sleep and stop)
                    with patch("app.core.gmail_poller.asyncio.sleep", side_effect=asyncio.CancelledError):
                        with pytest.raises(asyncio.CancelledError):
                            from app.core.gmail_poller import poll_gmail
                            await poll_gmail()

                    # Should have called evaluate_and_retry for each message
                    assert mock_eval.call_count >= 1

    @pytest.mark.asyncio
    async def test_poll_gmail_skips_spam(self, mock_gmail_service):
        """Should skip spam messages."""
        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.is_spam", return_value=(True, "spam detected")):
                with patch("app.core.gmail_poller.evaluate_and_retry") as mock_eval:
                    with patch("app.core.gmail_poller.asyncio.sleep", side_effect=asyncio.CancelledError):
                        with pytest.raises(asyncio.CancelledError):
                            from app.core.gmail_poller import poll_gmail
                            await poll_gmail()

                    # Should NOT have called evaluate_and_retry for spam
                    mock_eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_gmail_no_messages(self, mock_gmail_service):
        """Should handle empty inbox gracefully."""
        mock_gmail_service.users().messages().list(
        ).execute.return_value = {"messages": []}
        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    from app.core.gmail_poller import poll_gmail
                    await poll_gmail()
                # Should not raise any exception
                assert True

    @pytest.mark.asyncio
    async def test_poll_gmail_auth_failure(self):
        """Should return early when Gmail auth fails."""
        with patch("app.core.gmail_poller.get_gmail_service", side_effect=Exception("Auth failed")):
            from app.core.gmail_poller import poll_gmail
            await poll_gmail()
            # Should return without entering the loop
            assert True
