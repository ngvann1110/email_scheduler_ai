"""
Unit tests for app/agents/notification_agent.py

Tests:
- send_notification() — all status branches (created, conflict, rescheduled, etc.)
- _build_*_email() — each email builder function
- _decode_subject(), _format_datetime() — helper functions
- _send() — Gmail API call
"""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.notification_agent import (
    send_notification,
    _decode_subject,
    _format_datetime,
    _build_success_email,
    _build_conflict_email,
    _build_reschedule_email,
    _build_reschedule_not_found_email,
    _build_error_email,
    _send,
)


class TestDecodeSubject:
    """Tests for _decode_subject."""

    def test_plain_subject(self):
        assert _decode_subject("Meeting request") == "Meeting request"

    def test_encoded_subject(self):
        encoded = "=?UTF-8?B?VGhp4bqfdCBsw6BuZw==?="
        result = _decode_subject(encoded)
        assert len(result) > 0

    def test_empty_subject(self):
        assert _decode_subject("") == ""

    def test_none_subject(self):
        assert _decode_subject(None) == "None"  # str(None)


class TestFormatDatetime:
    """Tests for _format_datetime."""

    def test_valid_iso(self):
        result = _format_datetime("2026-06-10T09:00:00")
        assert "Thứ" in result
        assert "10/06/2026" in result.replace("10/06/2026", "10/06/2026")

    def test_invalid_iso(self):
        assert _format_datetime("invalid") == "invalid"

    def test_empty_string(self):
        assert _format_datetime("") == ""


class TestBuildSuccessEmail:
    """Tests for _build_success_email."""

    def _get_body(self, msg):
        """Extract plain text body from a MIME message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", errors="ignore")
        return msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    def test_returns_mime_message(self):
        msg = _build_success_email(
            to="user@example.com",
            subject="Meeting request",
            calendar_result={
                "location": "Room A",
                "event_link": "https://calendar.google.com/event?eid=123",
            },
            email_result={
                "time": "2026-06-10T09:00:00",
                "summary": "Project discussion",
            },
        )
        assert msg["To"] == "user@example.com"
        assert "xac nhan lich hop" in msg["Subject"].lower()
        body = self._get_body(msg)
        assert "Project discussion" in body


class TestBuildConflictEmail:
    """Tests for _build_conflict_email."""

    def _get_body(self, msg):
        """Extract plain text body from a MIME message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", errors="ignore")
        return msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    def test_returns_mime_message_with_suggestions(self):
        msg = _build_conflict_email(
            to="user@example.com",
            subject="Meeting request",
            calendar_result={"status": "conflict"},
            email_result={
                "time": "2026-06-10T09:00:00",
                "summary": "Project discussion",
            },
            conflict_result={
                "suggestions": [
                    {"label": "Thứ Hai 10/06/2026 lúc 10:00",
                        "start": "...", "end": "..."},
                    {"label": "Thứ Hai 10/06/2026 lúc 14:00",
                        "start": "...", "end": "..."},
                ]
            },
        )
        assert msg["To"] == "user@example.com"
        assert "xung dot" in msg["Subject"].lower()
        body = self._get_body(msg)
        assert "10:00" in body
        assert "14:00" in body

    def test_empty_suggestions(self):
        msg = _build_conflict_email(
            to="user@example.com",
            subject="Meeting",
            calendar_result={},
            email_result={"time": "2026-06-10T09:00:00", "summary": "Test"},
            conflict_result={"suggestions": []},
        )
        body = self._get_body(msg)
        assert "khong tim duoc" in body.lower()


class TestBuildRescheduleEmail:
    """Tests for _build_reschedule_email."""

    def test_returns_mime_message(self):
        msg = _build_reschedule_email(
            to="user@example.com",
            subject="Reschedule meeting",
            calendar_result={
                "event_title": "Team standup",
                "old_start": "2026-06-10T09:00:00",
                "new_start": "2026-06-11T10:00:00",
                "event_link": "https://calendar.google.com/event?eid=123",
            },
            email_result={},
        )
        assert "Da doi lich hop thanh cong" in msg["Subject"]


class TestBuildRescheduleNotFoundEmail:
    """Tests for _build_reschedule_not_found_email."""

    def test_returns_mime_message(self):
        msg = _build_reschedule_not_found_email(
            to="user@example.com",
            subject="Reschedule meeting",
            calendar_result={"status": "not_found"},
            email_result={"old_time": "2026-06-10T09:00:00"},
        )
        assert "Khong tim thay lich can doi" in msg["Subject"]


class TestBuildErrorEmail:
    """Tests for _build_error_email."""

    def _get_body(self, msg):
        """Extract plain text body from a MIME message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", errors="ignore")
        return msg.get_payload(decode=True).decode("utf-8", errors="ignore")

    def test_returns_mime_message(self):
        msg = _build_error_email(
            to="user@example.com",
            subject="Meeting request",
            error_msg="Could not parse time",
        )
        assert "Khong xu ly duoc yeu cau" in msg["Subject"]
        body = self._get_body(msg)
        assert "Could not parse time" in body


class TestSend:
    """Tests for _send()."""

    def test_send_success(self, mock_gmail_service):
        """Should return True when Gmail API succeeds."""
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg["To"] = "test@example.com"
        msg["Subject"] = "Test"
        msg.attach(__import__("email.mime.text", fromlist=[
                   "MIMEText"]).MIMEText("Body", "plain", "utf-8"))
        result = _send(mock_gmail_service, msg)
        assert result is True

    def test_send_failure(self, mock_gmail_service):
        """Should return False when Gmail API returns no ID."""
        mock_gmail_service.users().messages().send().execute.return_value = {}
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg["To"] = "test@example.com"
        msg["Subject"] = "Test"
        msg.attach(__import__("email.mime.text", fromlist=[
                   "MIMEText"]).MIMEText("Body", "plain", "utf-8"))
        result = _send(mock_gmail_service, msg)
        assert result is False


class TestSendNotification:
    """Tests for send_notification()."""

    @patch("app.core.logger.log_event")
    @patch("app.agents.notification_agent._get_gmail_service")
    def test_send_created_notification(self, mock_get_service, mock_log, mock_gmail_service):
        """Should send success email when status is 'created'."""
        mock_get_service.return_value = mock_gmail_service
        result = send_notification(
            email_obj=MagicMock(sender="user@example.com", subject="Meeting"),
            email_result={"time": "2026-06-10T09:00:00", "summary": "Test"},
            calendar_result={"status": "created",
                             "location": "Room A", "event_link": "http://link"},
        )
        assert result["status"] == "sent"

    @patch("app.core.logger.log_event")
    @patch("app.agents.notification_agent._get_gmail_service")
    def test_send_conflict_notification(self, mock_get_service, mock_log, mock_gmail_service):
        """Should send conflict email when status is 'conflict'."""
        mock_get_service.return_value = mock_gmail_service
        result = send_notification(
            email_obj=MagicMock(sender="user@example.com", subject="Meeting"),
            email_result={"time": "2026-06-10T09:00:00", "summary": "Test"},
            calendar_result={"status": "conflict"},
            conflict_result={"suggestions": [
                {"label": "Tomorrow 10am", "start": "...", "end": "..."}]},
        )
        assert result["status"] == "sent"

    @patch("app.core.logger.log_event")
    @patch("app.agents.notification_agent._get_gmail_service")
    def test_send_rescheduled_notification(self, mock_get_service, mock_log, mock_gmail_service):
        """Should send reschedule email when status is 'rescheduled'."""
        mock_get_service.return_value = mock_gmail_service
        result = send_notification(
            email_obj=MagicMock(sender="user@example.com",
                                subject="Reschedule"),
            email_result={"time": "2026-06-11T10:00:00"},
            calendar_result={
                "status": "rescheduled",
                "event_title": "Meeting",
                "old_start": "2026-06-10T09:00:00",
                "new_start": "2026-06-11T10:00:00",
                "event_link": "http://link",
            },
        )
        assert result["status"] == "sent"

    @patch("app.core.logger.log_event")
    @patch("app.agents.notification_agent._get_gmail_service")
    def test_send_error_notification(self, mock_get_service, mock_log, mock_gmail_service):
        """Should send error email for unknown status."""
        mock_get_service.return_value = mock_gmail_service
        result = send_notification(
            email_obj=MagicMock(sender="user@example.com", subject="Test"),
            email_result={},
            calendar_result={"status": "unknown",
                             "message": "Something went wrong"},
        )
        assert result["status"] == "sent"

    @patch("app.core.logger.log_event")
    @patch("app.agents.notification_agent._get_gmail_service")
    def test_send_failure_returns_error(self, mock_get_service, mock_log, mock_gmail_service):
        """Should return error status when Gmail API fails."""
        mock_gmail_service.users().messages().send(
        ).execute.side_effect = Exception("Gmail API error")
        mock_get_service.return_value = mock_gmail_service
        result = send_notification(
            email_obj=MagicMock(sender="user@example.com", subject="Test"),
            email_result={},
            calendar_result={"status": "created",
                             "location": "", "event_link": ""},
        )
        assert result["status"] == "error"
