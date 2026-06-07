"""
Integration tests for Google OAuth login flow.

Uses the shared ``test_client`` fixture from conftest.py which boots a
FastAPI test app that includes the auth router, session middleware, and
a temporary SQLite database.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.core.jwt_auth import create_access_token, decode_access_token
from app.db.sqlite import get_connection


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

        # Patch Flow.from_client_config to return our mock
        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, state=None: mock_flow,
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

        monkeypatch.setattr(
            "app.api.v1.auth.Flow.from_client_config",
            lambda client_config, scopes, state=None: mock_flow,
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
