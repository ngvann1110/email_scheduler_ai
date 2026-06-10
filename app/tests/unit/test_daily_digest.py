"""
Unit tests for daily_digest module.

Test plan
---------
* ``_build_digest_body`` – output when stats populated; empty top_emails fallback
* ``run_daily_digest`` behaviour:
  * skips when no emails exist (stats.total == 0)
  * sends digest when emails exist
  * handles Notification Agent error gracefully
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.daily_digest import _build_digest_body, run_daily_digest


# ── _build_digest_body ──────────────────────────────────────────────────────────

def test_build_digest_body_with_top_emails():
    """Digest body contains categories, top emails, and signature."""
    stats = {
        "total": 10,
        "report": 5,
        "partnership": 2,
        "support": 3,
        "announcement": 0,
        "meeting": 0,
        "other": 0,
    }
    top_emails = [
        {
            "sender": "alice@example.com",
            "subject": "Báo cáo tháng 6",
            "importance_score": 95,
            "summary": "Báo cáo doanh thu tháng 6 tăng 12%.",
            "category": "report",
        },
        {
            "sender": "bob@partner.com",
            "subject": "Hợp tác chiến lược",
            "importance_score": 88,
            "summary": "Đề xuất hợp tác dự án AI.",
            "category": "partnership",
        },
        {
            "sender": "carol@support.com",
            "subject": "Sự cố hệ thống",
            "importance_score": 80,
            "summary": "",
            "category": "support",
        },
    ]

    body = _build_digest_body(stats, top_emails, "10/06/2026")

    # Structural checks
    assert "Chào buổi sáng" in body
    assert "10/06/2026" in body
    assert "5 Báo cáo" in body
    assert "2 Đối tác" in body
    assert "3 Hỗ trợ" in body
    assert "0 Thông báo" in body

    # Top emails
    assert "alice@example.com" in body
    assert "Báo cáo tháng 6" in body
    assert "95/100" in body
    assert "Báo cáo doanh thu tháng 6 tăng 12%" in body
    assert "bob@partner.com" in body
    assert "88/100" in body
    assert "carol@support.com" in body
    assert "80/100" in body

    # No summary for third email (empty string)
    # Just verify it doesn't crash – the summary line is still present
    # but empty string means it still gets printed as "     Tóm tắt: "
    # That's acceptable; the important thing is no crash.

    assert "Trân trọng" in body
    assert "Email Scheduler AI" in body


def test_build_digest_body_empty_top_emails():
    """Fallback message when there are no emails analysed."""
    stats = {"total": 0, "report": 0, "partnership": 0,
             "support": 0, "announcement": 0, "meeting": 0, "other": 0}
    top_emails = []

    body = _build_digest_body(stats, top_emails, "10/06/2026")

    assert "Không có email nào được phân tích" in body
    assert "Chào buổi sáng" in body


def test_build_digest_body_missing_keys():
    """Stats dict with missing category keys should default to 0."""
    stats = {"total": 1}
    top_emails = []

    body = _build_digest_body(stats, top_emails, "01/01/2026")

    assert "0 Báo cáo" in body
    assert "0 Đối tác" in body
    assert "0 Hỗ trợ" in body
    assert "0 Thông báo" in body


# ── run_daily_digest ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_daily_digest_skips_when_no_emails():
    """When get_email_statistics returns total=0, no email should be sent."""
    with patch("app.core.daily_digest.settings") as mock_settings, \
            patch("app.core.daily_digest.get_email_statistics") as mock_stats, \
            patch("app.core.daily_digest.get_top_important_emails") as mock_top, \
            patch("app.core.daily_digest.send_reply") as mock_send, \
            patch("app.core.daily_digest.asyncio.sleep") as mock_sleep:

        mock_settings.DAILY_DIGEST_HOUR = 8
        mock_stats.return_value = {"total": 0}
        mock_top.return_value = []

        # First sleep: return immediately → digest runs
        # Second sleep: break the infinite loop
        mock_sleep.side_effect = [None, asyncio.CancelledError()]

        try:
            await run_daily_digest()
        except asyncio.CancelledError:
            pass

        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_run_daily_digest_sends_when_emails_exist():
    """When emails exist, digest should be built and sent."""
    with patch("app.core.daily_digest.settings") as mock_settings, \
            patch("app.core.daily_digest.get_email_statistics") as mock_stats, \
            patch("app.core.daily_digest.get_top_important_emails") as mock_top, \
            patch("app.core.daily_digest.send_reply") as mock_send, \
            patch("app.core.daily_digest.asyncio.sleep") as mock_sleep:

        mock_settings.DAILY_DIGEST_HOUR = 8
        mock_stats.return_value = {
            "total": 8, "report": 4, "partnership": 2,
            "support": 1, "announcement": 0, "meeting": 1, "other": 0,
        }
        mock_top.return_value = [
            {
                "sender": "test@test.com",
                "subject": "Test",
                "importance_score": 90,
                "summary": "Test summary",
                "category": "report",
            }
        ]
        mock_send.return_value = {"status": "sent", "to": "test@test.com"}

        # First sleep: return immediately → digest runs
        # Second sleep: break the infinite loop
        mock_sleep.side_effect = [None, asyncio.CancelledError()]

        try:
            await run_daily_digest()
        except asyncio.CancelledError:
            pass

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args.kwargs["to_email"] == mock_settings.ORGANIZER_EMAIL
        assert "Daily Digest" in call_args.kwargs["subject"]
        assert "4 Báo cáo" in call_args.kwargs["body_text"]


@pytest.mark.asyncio
async def test_run_daily_digest_handles_send_failure():
    """When Notification Agent returns error, digest logs but doesn't crash."""
    with patch("app.core.daily_digest.settings") as mock_settings, \
            patch("app.core.daily_digest.get_email_statistics") as mock_stats, \
            patch("app.core.daily_digest.get_top_important_emails") as mock_top, \
            patch("app.core.daily_digest.send_reply") as mock_send, \
            patch("app.core.daily_digest.asyncio.sleep") as mock_sleep:

        mock_settings.DAILY_DIGEST_HOUR = 8
        mock_stats.return_value = {"total": 5, "report": 5, "partnership": 0,
                                   "support": 0, "announcement": 0, "meeting": 0, "other": 0}
        mock_top.return_value = []
        mock_send.return_value = {"status": "error", "message": "SMTP down"}

        # First sleep: return immediately → digest runs
        # Second sleep: break the infinite loop
        mock_sleep.side_effect = [None, asyncio.CancelledError()]

        try:
            await run_daily_digest()
        except asyncio.CancelledError:
            pass

        # send_reply should still have been called – error is handled gracefully
        mock_send.assert_called_once()
