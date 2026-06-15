"""
Integration tests for app/core/gmail_poller.py

Tests:
- _parse_date_header() — RFC 2822 to ISO 8601 conversion
- _parse_message() — email parsing from raw Gmail API response
- _mark_as_read() — marking messages as read
- poll_gmail() — main polling loop (with mocked Gmail API)
"""

import asyncio
from datetime import datetime, timezone
from email import message_from_bytes
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.core.gmail_poller import _parse_date_header, _parse_message, _mark_as_read, poll_gmail


def _make_mail(date_value: str | None) -> object:
    """Build a minimal email.message.Message with an optional Date header."""
    from email.mime.text import MIMEText
    msg = MIMEText("test body")
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test"
    if date_value is not None:
        msg["Date"] = date_value
    return msg


class TestParseDateHeader:
    """Tests for _parse_date_header() — Gmail Date header → ISO 8601."""

    # ── Case 1: real-world Gmail RFC 2822 with +0700 offset ──────────────────
    def test_real_gmail_date_with_plus0700(self):
        """Thu, 11 Jun 2026 00:24:18 +0700 → ISO 8601 with correct offset."""
        mail = _make_mail("Thu, 11 Jun 2026 00:24:18 +0700")
        result = _parse_date_header(mail)
        # parsedate_to_datetime returns offset-aware datetime; isoformat preserves offset
        assert result == "2026-06-11T00:24:18+07:00"

    # ── Case 2: RFC 2822 with +0000 (UTC) ────────────────────────────────────
    def test_rfc2822_with_utc(self):
        """Mon, 01 Jan 2025 12:30:00 +0000 → ISO 8601 UTC."""
        mail = _make_mail("Mon, 01 Jan 2025 12:30:00 +0000")
        result = _parse_date_header(mail)
        assert result == "2025-01-01T12:30:00+00:00"

    # ── Case 3: missing Date header ─────────────────────────────────────────
    def test_missing_date_header(self):
        """Missing Date → fallback to current UTC time (non-empty ISO string)."""
        mail = _make_mail(None)
        # Remove the Date header entirely if present
        if "Date" in mail:
            del mail["Date"]
        result = _parse_date_header(mail)
        # Should be a valid ISO 8601 string
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None  # timezone-aware
        # Should be close to now (within 2 seconds)
        delta = abs((dt - datetime.now(timezone.utc)).total_seconds())
        assert delta < 2

    def test_empty_date_header(self):
        """Empty Date string → fallback to current UTC time."""
        mail = _make_mail("")
        result = _parse_date_header(mail)
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None
        delta = abs((dt - datetime.now(timezone.utc)).total_seconds())
        assert delta < 2

    # ── Case 4: invalid / unparseable Date header ────────────────────────────
    def test_invalid_date_header(self):
        """'invalid-date' → no crash, fallback ISO timestamp generated."""
        mail = _make_mail("invalid-date")
        result = _parse_date_header(mail)
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None
        delta = abs((dt - datetime.now(timezone.utc)).total_seconds())
        assert delta < 2

    def test_garbled_date_header(self):
        """'not-a-real-date!!' → no crash, fallback ISO timestamp generated."""
        mail = _make_mail("not-a-real-date!!")
        result = _parse_date_header(mail)
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    # ── Case 5: output must pass EmailSchema validation ──────────────────────
    def test_output_passes_email_schema(self):
        """ISO string from _parse_date_header must validate in EmailSchema."""
        from app.schemas.email import EmailSchema
        mail = _make_mail("Thu, 11 Jun 2026 00:24:18 +0700")
        iso = _parse_date_header(mail)
        email_obj = EmailSchema(
            sender="test@example.com",
            subject="Test",
            body="body",
            timestamp=iso,
        )
        assert email_obj.timestamp == "2026-06-11T00:24:18+07:00"


def _make_raw_email(from_header: str, subject: str = "Test", body: str = "body") -> str:
    """Build a base64url-encoded raw email with the given From header string."""
    import base64
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg["From"] = from_header
    msg["Subject"] = subject
    msg["Date"] = "Mon, 15 Jun 2026 10:00:00 +0700"
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _service_with_raw(raw: str):
    """Return a mock Gmail service whose messages().get().execute() yields ``raw``."""
    from unittest.mock import MagicMock
    service = MagicMock()
    service.users().messages().get().execute.return_value = {
        "id": "msg_x",
        "raw": raw,
    }
    return service


class TestDecodeMimeHeader:
    """Unit tests for decode_mime_header() covering RFC 2047 sender variants."""

    def test_ascii_sender(self):
        """Plain ASCII sender is returned unchanged."""
        from app.core.gmail_poller import decode_mime_header
        assert decode_mime_header("alice@example.com") == "alice@example.com"

    def test_utf8_display_name(self):
        """RFC 2047 UTF-8 base64-encoded display name is decoded correctly."""
        import base64
        from app.core.gmail_poller import decode_mime_header
        name = "Nguyễn Văn An"
        b64 = base64.b64encode(name.encode("utf-8")).decode()
        encoded = f"=?UTF-8?B?{b64}?= <nguyen@example.com>"
        result = decode_mime_header(encoded)
        assert name in result
        assert isinstance(result, str)

    def test_vietnamese_quoted_printable(self):
        """RFC 2047 UTF-8 quoted-printable sender is decoded to Vietnamese text."""
        from app.core.gmail_poller import decode_mime_header
        # "Trần Thị B" encoded as QP
        encoded = "=?UTF-8?Q?Tr=E1=BA=A7n_Th=E1=BB=8B_B?= <tran@example.com>"
        result = decode_mime_header(encoded)
        assert "Trần" in result
        assert isinstance(result, str)

    def test_emoji_display_name(self):
        """Emoji in display name is decoded without errors."""
        from app.core.gmail_poller import decode_mime_header
        import base64
        emoji_bytes = "🎉 Team".encode("utf-8")
        b64 = base64.b64encode(emoji_bytes).decode()
        encoded = f"=?UTF-8?B?{b64}?= <team@example.com>"
        result = decode_mime_header(encoded)
        assert "🎉" in result
        assert isinstance(result, str)

    def test_header_object_coerced_to_str(self):
        """email.header.Header object is coerced to str, not leaked to caller."""
        from email.header import Header
        from app.core.gmail_poller import decode_mime_header
        header_obj = Header("Alice <alice@example.com>", "utf-8")
        result = decode_mime_header(header_obj)
        assert isinstance(result, str)
        assert result != ""

    def test_empty_value_returns_empty_string(self):
        """Falsy input always returns empty string, never None or Header."""
        from app.core.gmail_poller import decode_mime_header
        assert decode_mime_header("") == ""
        assert decode_mime_header(None) == ""


class TestParseMessageSenderTypes:
    """_parse_message() must always produce a plain str for 'sender'."""

    def test_ascii_sender_is_str(self):
        """ASCII From header → sender is a plain str."""
        raw = _make_raw_email("alice@example.com")
        result = _parse_message(_service_with_raw(raw), "msg_x")
        assert result is not None
        assert isinstance(result["sender"], str)
        assert result["sender"] == "alice@example.com"

    def test_utf8_mime_encoded_sender_is_str(self):
        """RFC 2047 encoded Vietnamese name → sender is a plain str (email address extracted)."""
        import base64
        encoded_name = base64.b64encode("Nguyễn Văn An".encode()).decode()
        raw = _make_raw_email(
            f"=?UTF-8?B?{encoded_name}?= <nguyen@example.com>"
        )
        result = _parse_message(_service_with_raw(raw), "msg_x")
        assert result is not None
        assert isinstance(result["sender"], str)
        assert result["sender"] == "nguyen@example.com"

    def test_emoji_sender_is_str(self):
        """Emoji display name → sender field is a plain str."""
        import base64
        encoded_name = base64.b64encode("🎉 Team".encode()).decode()
        raw = _make_raw_email(
            f"=?UTF-8?B?{encoded_name}?= <team@example.com>"
        )
        result = _parse_message(_service_with_raw(raw), "msg_x")
        assert result is not None
        assert isinstance(result["sender"], str)
        assert result["sender"] == "team@example.com"

    def test_header_object_injected_via_mock(self):
        """Even if mail.get() returns a Header object, sender must be a plain str."""
        from email.header import Header
        from unittest.mock import MagicMock, patch
        import base64
        from email.mime.text import MIMEText

        msg = MIMEText("body")
        msg["Date"] = "Mon, 15 Jun 2026 10:00:00 +0700"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        service = MagicMock()
        service.users().messages().get().execute.return_value = {
            "id": "msg_x", "raw": raw,
        }

        header_obj = Header("Alice <alice@example.com>", "utf-8")

        with patch("app.core.gmail_poller.message_from_bytes") as mock_parse:
            fake_mail = MagicMock()
            fake_mail.is_multipart.return_value = False
            fake_mail.get_payload.return_value = b"body"
            fake_mail.get.side_effect = lambda key, default="": (
                header_obj if key == "From"
                else "Test Subject" if key == "Subject"
                else "Mon, 15 Jun 2026 10:00:00 +0700" if key == "Date"
                else default
            )
            mock_parse.return_value = fake_mail

            result = _parse_message(service, "msg_x")

        assert result is not None
        assert isinstance(result["sender"], str), (
            f"sender must be str, got {type(result['sender'])}"
        )


class TestParseMessage:
    """Tests for _parse_message()."""

    def test_parse_success(self, mock_gmail_service):
        """Should parse a raw Gmail message into a dict with ISO 8601 timestamp."""
        result = _parse_message(mock_gmail_service, "msg_001")
        assert result is not None
        assert "sender" in result
        assert "subject" in result
        assert "body" in result
        assert "timestamp" in result
        # Verify timestamp is now ISO 8601 (not raw RFC 2822)
        dt = datetime.fromisoformat(result["timestamp"])
        assert dt.tzinfo is not None

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
            await poll_gmail()
            # Should return without entering the loop
            assert True

    @pytest.mark.asyncio
    async def test_does_not_mark_read_when_pipeline_fails(self, mock_gmail_service):
        """Email must stay unread when the pipeline raises, so it is retried next poll."""
        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.is_spam", return_value=(False, "")):
                with patch("app.core.gmail_poller.evaluate_and_retry",
                           side_effect=Exception("pipeline crashed")):
                    with patch("app.core.gmail_poller._mark_as_read") as mock_mark:
                        with patch("app.core.gmail_poller.asyncio.sleep",
                                   side_effect=asyncio.CancelledError):
                            with pytest.raises(asyncio.CancelledError):
                                await poll_gmail()
        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_read_after_pipeline_success_not_before(self, mock_gmail_service):
        """_mark_as_read must fire AFTER evaluate_and_retry returns, not before."""
        call_order = []

        async def fake_pipeline(*args, **kwargs):
            call_order.append("pipeline")
            return {"type": "schedule_flow", "data": {}}

        def fake_mark_read(service, msg_id):
            call_order.append("mark_read")

        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.is_spam", return_value=(False, "")):
                with patch("app.core.gmail_poller.evaluate_and_retry", new=fake_pipeline):
                    with patch("app.core.gmail_poller._mark_as_read",
                               side_effect=fake_mark_read):
                        with patch("app.core.gmail_poller.asyncio.sleep",
                                   side_effect=asyncio.CancelledError):
                            with pytest.raises(asyncio.CancelledError):
                                await poll_gmail()

        assert "pipeline" in call_order, "_mark_as_read fired before pipeline ran"
        assert "mark_read" in call_order, "_mark_as_read was never called on success"
        assert call_order.index("pipeline") < call_order.index("mark_read"), (
            "_mark_as_read fired before evaluate_and_retry completed"
        )

    @pytest.mark.asyncio
    async def test_marks_spam_as_read_without_running_pipeline(self, mock_gmail_service):
        """Spam emails must be marked as read (so they don't recur) but pipeline must not run."""
        with patch("app.core.gmail_poller.get_gmail_service", return_value=mock_gmail_service):
            with patch("app.core.gmail_poller.is_spam", return_value=(True, "spam_keyword")):
                with patch("app.core.gmail_poller.evaluate_and_retry") as mock_eval:
                    with patch("app.core.gmail_poller._mark_as_read") as mock_mark:
                        with patch("app.core.gmail_poller.asyncio.sleep",
                                   side_effect=asyncio.CancelledError):
                            with pytest.raises(asyncio.CancelledError):
                                await poll_gmail()
        mock_mark.assert_called()
        mock_eval.assert_not_called()
