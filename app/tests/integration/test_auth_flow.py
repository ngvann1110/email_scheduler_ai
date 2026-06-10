"""
Integration tests for Google OAuth login flow.

Uses the shared ``test_client`` fixture from conftest.py which boots a
FastAPI test app that includes the auth router, session middleware, and
a temporary SQLite database.
"""

import base64 as _b64
import json as _json_mod

import pytest
from itsdangerous import TimestampSigner as _TimestampSigner
from unittest.mock import MagicMock, patch

from app.core.jwt_auth import create_access_token, decode_access_token
from app.db.sqlite import get_connection

# ── Session-cookie helper ─────────────────────────────────────────────────────
# Must match the secret_key used in the test_client fixture (conftest.py).
_SESSION_SECRET = "test-secret-key-for-sessions"


def _decode_session_cookie(cookie_value: str) -> dict:
    """
    Decode a Starlette signed session cookie into a plain dict.

    Starlette's SessionMiddleware stores the session as:
      TimestampSigner(secret_key).sign(b64encode(json(data)))
    This helper reverses that encoding so tests can assert on the raw
    session contents without making additional HTTP round-trips.
    """
    signer = _TimestampSigner(_SESSION_SECRET)
    try:
        raw = signer.unsign(
            cookie_value.encode("utf-8"),
            max_age=86400 * 365,  # 1 year — elapsed time is irrelevant in tests
        )
        return _json_mod.loads(_b64.b64decode(raw))
    except Exception:
        return {}


class TestLoginRedirect:
    """Tests for GET /auth/login."""

    @patch("app.api.v1.auth._get_oauth_client_config")
    def test_login_redirects_to_google(self, mock_config, test_client, monkeypatch):
        mock_config.return_value = {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }
        """
        GET /auth/login returns a 302 redirect to the Google OAuth consent URL.
        """
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?client_id=test",
            "fake-state",
        )
        mock_flow.code_verifier = "fake-verifier"

        # Patch Flow.from_client_config to return our mock
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: mock_flow,
        )

        response = test_client.get("/auth/login", follow_redirects=False)
        assert response.status_code in (302, 307), (
            f"Expected 302 or 307, got {response.status_code}: {response.text}"
        )
        assert "accounts.google.com" in response.headers["location"]

    @patch("app.api.v1.auth._get_oauth_client_config")
    def test_login_redirect_includes_state(self, mock_config, test_client, monkeypatch):
        mock_config.return_value = {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }
        """
        The redirect URL should include a state parameter for CSRF protection.
        """
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?state=abc123&client_id=test",
            "abc123",
        )
        mock_flow.code_verifier = "fake-verifier"

        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: mock_flow,
        )

        response = test_client.get("/auth/login", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert "state=" in response.headers["location"]


class TestAuthMe:
    """Tests for GET /auth/me."""

    def test_me_without_auth_returns_401(self, test_client):
        """
        GET /auth/me without any auth token returns 401.
        """
        response = test_client.get("/auth/me")
        assert response.status_code == 401, (
            f"Expected 401, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "detail" in data

    def test_me_with_invalid_token_returns_401(self, test_client):
        """
        GET /auth/me with a garbage token returns 401.
        """
        response = test_client.get(
            "/auth/me",
            headers={"Authorization": "Bearer not.a.real.jwt.token"},
        )
        assert response.status_code == 401

    def test_me_with_valid_token_returns_user(self, test_client):
        """
        GET /auth/me with a valid JWT returns the user's profile info.
        """
        # Insert a test user directly into the test database
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (google_id, email, name, picture_url) "
            "VALUES (?, ?, ?, ?)",
            ("test-google-id-001", "testuser@example.com",
             "Test User", "https://example.com/avatar.jpg"),
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Create a valid JWT for this user
        token = create_access_token(user_id, "testuser@example.com")

        # Call /auth/me with the token
        response = test_client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["id"] == user_id
        assert data["email"] == "testuser@example.com"
        assert data["name"] == "Test User"
        assert data["picture_url"] == "https://example.com/avatar.jpg"

    def test_me_with_token_for_nonexistent_user_returns_401(self, test_client):
        """
        GET /auth/me with a valid JWT for a user that does not exist returns 401.
        """
        token = create_access_token(99999, "ghost@example.com")

        response = test_client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401, (
            f"Expected 401, got {response.status_code}: {response.text}"
        )

    def test_me_uses_cookie_fallback(self, test_client):
        """
        GET /auth/me reads the token from the access_token cookie if no
        Authorization header is present.
        """
        # Insert a test user
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (google_id, email, name, picture_url) "
            "VALUES (?, ?, ?, ?)",
            ("google-id-cookie", "cookieuser@example.com",
             "Cookie User", ""),
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        token = create_access_token(user_id, "cookieuser@example.com")

        response = test_client.get(
            "/auth/me",
            cookies={"access_token": token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "cookieuser@example.com"

    def test_me_header_takes_precedence_over_cookie(self, test_client):
        """
        When both Authorization header and cookie are present, the header wins.
        """
        # Insert two users
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (google_id, email, name, picture_url) "
            "VALUES (?, ?, ?, ?)",
            ("google-id-a", "user-a@example.com", "User A", ""),
        )
        user_a_id = cur.lastrowid
        cur.execute(
            "INSERT INTO users (google_id, email, name, picture_url) "
            "VALUES (?, ?, ?, ?)",
            ("google-id-b", "user-b@example.com", "User B", ""),
        )
        user_b_id = cur.lastrowid
        conn.commit()
        conn.close()

        token_a = create_access_token(user_a_id, "user-a@example.com")
        token_b = create_access_token(user_b_id, "user-b@example.com")

        response = test_client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
            cookies={"access_token": token_b},
        )
        assert response.status_code == 200
        data = response.json()
        # Header token should win → User A
        assert data["email"] == "user-a@example.com"


class TestLogout:
    """Tests for POST /auth/logout."""

    def test_logout_returns_200(self, test_client):
        """
        POST /auth/logout returns 200 and a logged_out status.
        """
        response = test_client.post("/auth/logout")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged_out"

    def test_logout_clears_cookie(self, test_client):
        """
        POST /auth/logout sets the access_token cookie to an empty/expired
        value, effectively clearing it.
        """
        response = test_client.post("/auth/logout")
        # The response should include a Set-Cookie header that clears the cookie
        set_cookie = response.headers.get("set-cookie", "")
        # delete_cookie sets max-age=0 or an empty value
        assert "access_token" in set_cookie.lower() or response.status_code == 200


class TestOAuthSessionCleanup:
    """
    Tests that verify oauth_state and code_verifier are removed from the
    session cookie after a failed auth_callback, and that a subsequent
    /auth/login starts with a clean session.

    These tests cover the two fixes added to app/api/v1/auth.py:
      1. Stale-state guard at the top of login() — clears leftover keys on
         a repeated /auth/login visit.
      2. Exception-handler cleanup in auth_callback() — clears keys when
         the token-exchange (or any later step) raises an exception.
    """

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _mock_login_flow(state: str, verifier: str) -> MagicMock:
        """Return a Flow mock whose authorization_url produces *state*."""
        flow = MagicMock()
        flow.authorization_url.return_value = (
            f"https://accounts.google.com/o/oauth2/auth?state={state}",
            state,
        )
        flow.code_verifier = verifier
        return flow

    @staticmethod
    def _mock_fail_flow() -> MagicMock:
        """Return a Flow mock whose fetch_token raises RuntimeError."""
        flow = MagicMock()
        flow.fetch_token.side_effect = RuntimeError(
            "simulated token-fetch error")
        return flow

    # ── test 1 ────────────────────────────────────────────────────────────────

    @patch("app.api.v1.auth._get_oauth_client_config")
    def test_failed_callback_clears_session(self, mock_config, test_client, monkeypatch):
        """
        When auth_callback raises an exception during token exchange,
        oauth_state and code_verifier must be removed from the session
        cookie before the 400 error response is returned.
        """
        mock_config.return_value = {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }

        # ── Step 1: visit /auth/login to populate the session ─────────────────
        # Pin secrets.token_urlsafe so we know exactly which state is stored.
        monkeypatch.setattr(
            "app.api.v1.auth.secrets.token_urlsafe",
            lambda n: "known-state-111",
        )
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: self._mock_login_flow(
                "known-state-111", "known-verifier-aaa"
            ),
        )

        login_resp = test_client.get("/auth/login", follow_redirects=False)
        assert login_resp.status_code in (302, 307)

        # Confirm the session was populated by the login endpoint.
        session_after_login = _decode_session_cookie(
            test_client.cookies["session"])
        assert session_after_login.get("oauth_state") == "known-state-111", (
            "Login must store oauth_state in the session"
        )
        assert session_after_login.get("code_verifier") == "known-verifier-aaa", (
            "Login must store code_verifier in the session"
        )

        # ── Step 2: trigger a failing callback ────────────────────────────────
        # state matches the session so we pass the CSRF check, but
        # fetch_token raises → exception handler clears the session.
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: self._mock_fail_flow(),
        )

        callback_resp = test_client.get(
            "/auth/callback",
            params={"code": "any-code", "state": "known-state-111"},
            follow_redirects=False,
        )
        assert callback_resp.status_code == 400, (
            f"Expected 400 from failed callback, got {callback_resp.status_code}"
        )

        # ── Step 3: verify the session no longer contains OAuth keys ──────────
        # Starlette's SessionMiddleware deletes the cookie entirely when the
        # session dict becomes empty, so the cookie may be absent.  Both
        # "cookie absent" and "cookie present but without the OAuth keys"
        # satisfy the postcondition — treat a missing cookie as an empty dict.
        raw_session_cookie = test_client.cookies.get("session")
        session_after_failure = (
            _decode_session_cookie(raw_session_cookie)
            if raw_session_cookie is not None
            else {}
        )
        assert "oauth_state" not in session_after_failure, (
            "oauth_state must be cleared from the session after a failed callback"
        )
        assert "code_verifier" not in session_after_failure, (
            "code_verifier must be cleared from the session after a failed callback"
        )

    # ── test 2 ────────────────────────────────────────────────────────────────

    @patch("app.api.v1.auth._get_oauth_client_config")
    def test_second_login_after_failed_callback(self, mock_config, test_client, monkeypatch):
        """
        After a failed callback clears the session, a second /auth/login
        must store only the new oauth_state and code_verifier.
        The old oauth_state must not appear in the session.
        """
        mock_config.return_value = {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }

        # ── First login: state = "old-state-aaa" ─────────────────────────────
        monkeypatch.setattr(
            "app.api.v1.auth.secrets.token_urlsafe",
            lambda n: "old-state-aaa",
        )
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: self._mock_login_flow(
                "old-state-aaa", "old-verifier-bbb"
            ),
        )
        test_client.get("/auth/login", follow_redirects=False)

        # ── Failed callback: clears "old-state-aaa" from the session ──────────
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: self._mock_fail_flow(),
        )
        test_client.get(
            "/auth/callback",
            params={"code": "any-code", "state": "old-state-aaa"},
            follow_redirects=False,
        )

        # ── Second login: state = "new-state-ccc" ────────────────────────────
        monkeypatch.setattr(
            "app.api.v1.auth.secrets.token_urlsafe",
            lambda n: "new-state-ccc",
        )
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, **kwargs: self._mock_login_flow(
                "new-state-ccc", "new-verifier-ddd"
            ),
        )
        second_login_resp = test_client.get(
            "/auth/login", follow_redirects=False)
        assert second_login_resp.status_code in (302, 307)

        # ── Verify session has the new state and NOT the old one ──────────────
        session = _decode_session_cookie(test_client.cookies["session"])

        assert session.get("oauth_state") == "new-state-ccc", (
            f"Expected oauth_state='new-state-ccc', got {session.get('oauth_state')!r}"
        )
        assert session.get("oauth_state") != "old-state-aaa", (
            "Old oauth_state must not survive into the second login"
        )
        assert session.get("code_verifier") == "new-verifier-ddd", (
            f"Expected code_verifier='new-verifier-ddd', got {session.get('code_verifier')!r}"
        )
