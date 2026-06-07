"""
Unit tests for app/agents/spam_filter.py

Tests:
- is_spam() with various sender patterns
- is_spam() with various subject patterns
- is_spam() with various body patterns
- is_spam() with clean (non-spam) emails
- Edge cases: empty fields, None values
"""

import pytest

from app.agents.spam_filter import (
    is_spam,
    _normalize,
    _check_sender,
    _check_subject,
    _check_body,
    SPAM_SENDER_KEYWORDS,
    SPAM_SUBJECT_KEYWORDS,
    SPAM_BODY_KEYWORDS,
)


class MockEmail:
    """Minimal email-like object for testing is_spam()."""

    def __init__(self, sender="", subject="", body=""):
        self.sender = sender
        self.subject = subject
        self.body = body


class TestNormalize:
    """Tests for _normalize helper."""

    def test_lowercases(self):
        assert _normalize("HELLO") == "hello"

    def test_strips_whitespace(self):
        assert _normalize("  hello  ") == "hello"

    def test_combined(self):
        assert _normalize("  HeLLo  ") == "hello"


class TestCheckSender:
    """Tests for _check_sender."""

    def test_spam_sender_noreply(self):
        assert _check_sender("noreply@company.com") == (True,
                                                        "sender contains 'noreply'")

    def test_spam_sender_newsletter(self):
        assert _check_sender("newsletter@example.com") == (True,
                                                           "sender contains 'newsletter'")

    def test_spam_sender_marketing(self):
        assert _check_sender("marketing@store.com") == (True,
                                                        "sender contains 'marketing@'")

    def test_clean_sender(self):
        assert _check_sender("john.doe@company.com") == (False, "")

    def test_empty_sender(self):
        assert _check_sender("") == (False, "")

    def test_case_insensitive(self):
        assert _check_sender("NoReply@example.com") == (True,
                                                        "sender contains 'noreply'")


class TestCheckSubject:
    """Tests for _check_subject."""

    def test_spam_subject_unsubscribe(self):
        assert _check_subject("Click to unsubscribe") == (
            True, "subject contains 'unsubscribe'")

    def test_spam_subject_discount(self):
        assert _check_subject("Big discount today!") == (
            True, "subject contains 'discount'")

    def test_spam_subject_khuyen_mai(self):
        assert _check_subject("Khuyến mãi lớn") == (
            True, "subject contains 'khuyến mãi'")

    def test_clean_subject(self):
        assert _check_subject("Meeting tomorrow at 10am") == (False, "")

    def test_empty_subject(self):
        assert _check_subject("") == (False, "")


class TestCheckBody:
    """Tests for _check_body."""

    def test_spam_body_unsubscribe_link(self):
        assert _check_body("click here to unsubscribe") == (
            True, "body contains 'click here to unsubscribe'")

    def test_spam_body_automated_message(self):
        assert _check_body("this is an automated message") == (
            True, "body contains 'this is an automated message'")

    def test_spam_body_copyright(self):
        assert _check_body("© 2026 All rights reserved") == (
            True, "body contains '©'")

    def test_clean_body(self):
        assert _check_body("Let's meet at the office tomorrow.") == (False, "")

    def test_empty_body(self):
        assert _check_body("") == (False, "")


class TestIsSpam:
    """Tests for the main is_spam() function."""

    def test_spam_by_sender(self):
        email = MockEmail(sender="newsletter@example.com",
                          subject="Hello", body="Hello")
        spam, reason = is_spam(email)
        assert spam is True
        assert "sender" in reason

    def test_spam_by_subject(self):
        email = MockEmail(sender="friend@example.com",
                          subject="Big discount 50% off", body="Check this out")
        spam, reason = is_spam(email)
        assert spam is True
        assert "subject" in reason

    def test_spam_by_body(self):
        email = MockEmail(sender="friend@example.com",
                          subject="Hello", body="click here to unsubscribe")
        spam, reason = is_spam(email)
        assert spam is True
        assert "body" in reason

    def test_clean_email(self):
        email = MockEmail(
            sender="colleague@company.com",
            subject="Project update meeting",
            body="Hi team, let's meet tomorrow at 10am to discuss the project.",
        )
        spam, reason = is_spam(email)
        assert spam is False
        assert reason == ""

    def test_sender_takes_priority(self):
        """Sender check should run first and short-circuit."""
        email = MockEmail(
            sender="noreply@newsletter.com",
            subject="unsubscribe",
            body="unsubscribe",
        )
        spam, reason = is_spam(email)
        assert spam is True
        assert "sender" in reason

    def test_subject_checked_when_sender_clean(self):
        """Subject should be checked when sender is clean."""
        email = MockEmail(
            sender="friend@example.com",
            subject="You have been selected!",
            body="Normal body text",
        )
        spam, reason = is_spam(email)
        assert spam is True
        assert "subject" in reason

    def test_body_checked_when_sender_and_subject_clean(self):
        """Body should be checked when sender and subject are clean."""
        email = MockEmail(
            sender="friend@example.com",
            subject="Hello",
            body="This is an automated message from our system.",
        )
        spam, reason = is_spam(email)
        assert spam is True
        assert "body" in reason

    def test_email_with_missing_attributes(self):
        """is_spam should handle objects missing sender/subject/body attributes."""
        class PartialEmail:
            pass

        email = PartialEmail()
        spam, reason = is_spam(email)
        assert spam is False
        assert reason == ""

    def test_email_with_none_attributes(self):
        """is_spam should handle None attributes gracefully."""
        email = MockEmail(sender=None, subject=None, body=None)
        spam, reason = is_spam(email)
        assert spam is False
        assert reason == ""

    def test_spam_keyword_lists_are_populated(self):
        """Sanity check: keyword lists should not be empty."""
        assert len(SPAM_SENDER_KEYWORDS) > 0
        assert len(SPAM_SUBJECT_KEYWORDS) > 0
        assert len(SPAM_BODY_KEYWORDS) > 0
