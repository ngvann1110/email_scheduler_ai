"""
Unit tests for JWT token creation and decoding.
"""

import pytest

from app.core.jwt_auth import create_access_token, decode_access_token


class TestJwtToken:
    """Tests for create_access_token and decode_access_token."""

    def test_create_and_decode_token(self):
        """Create a token, decode it, and verify the payload."""
        user_id = 42
        email = "test@example.com"

        token = create_access_token(user_id, email)

        # Token should be a non-empty string
        assert isinstance(token, str)
        assert len(token) > 0

        payload = decode_access_token(token)
        assert payload is not None
        assert payload["user_id"] == user_id
        assert payload["email"] == email
        assert "exp" in payload

    def test_expired_token(self, monkeypatch):
        """A token with a past expiry should decode as None."""
        from datetime import datetime, timedelta, timezone

        # Force expiry to be in the past by patching JWT_EXPIRE_MINUTES
        monkeypatch.setattr(
            "app.core.jwt_auth.settings.JWT_EXPIRE_MINUTES", -1)

        token = create_access_token(1, "expired@example.com")
        payload = decode_access_token(token)
        assert payload is None

    def test_invalid_token(self):
        """A garbage string should decode as None."""
        result = decode_access_token("not.a.valid.token")
        assert result is None

    def test_empty_token(self):
        """An empty string should decode as None."""
        result = decode_access_token("")
        assert result is None

    def test_decoded_payload_has_expected_keys(self):
        """The decoded payload must contain user_id, email, and exp."""
        token = create_access_token(7, "payload@test.org")
        payload = decode_access_token(token)

        assert "user_id" in payload
        assert "email" in payload
        assert "exp" in payload
        assert payload["user_id"] == 7
        assert payload["email"] == "payload@test.org"
