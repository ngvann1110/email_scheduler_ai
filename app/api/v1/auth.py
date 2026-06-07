"""
Google OAuth login + JWT session management router.

Provides endpoints for:
- Redirecting to Google login
- Handling the OAuth callback
- Getting current user info
- Logging out
"""

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.core.config import settings
from app.core.jwt_auth import create_access_token, get_current_user
from app.db.sqlite import create_or_update_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Google OAuth scopes
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Base directory for resolving credentials file
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # app/


def _get_oauth_client_config() -> dict:
    """
    Return the OAuth client ID and secret.

    Reads from settings first; falls back to credentials.json if settings are empty.
    """
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET

    if not client_id or not client_secret:
        creds_path = BASE_DIR / settings.GOOGLE_CREDENTIALS_PATH
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text())
                web = creds.get("web") or creds.get("installed", {})
                client_id = client_id or web.get("client_id", "")
                client_secret = client_secret or web.get("client_secret", "")
            except (json.JSONDecodeError, KeyError):
                logger.warning(
                    "[Auth] Không đọc được credentials.json, dùng giá trị mặc định."
                )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
    }


@router.get("/login")
async def login(request: Request):
    """Redirect the user to the Google OAuth consent screen."""
    oauth_config = _get_oauth_client_config()

    flow = Flow.from_client_config(
        client_config={
            "web": {
                "client_id": oauth_config["client_id"],
                "client_secret": oauth_config["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.GOOGLE_OAUTH_REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    # Requires starlette SessionMiddleware
    request.session["oauth_state"] = state

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )

    request.session["code_verifier"] = flow.code_verifier

    logger.info("[Auth] Chuyển hướng đến Google OAuth")
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def auth_callback(request: Request, code: str = None, state: str = None):
    """
    Google OAuth callback endpoint.

    Exchanges the authorization code for tokens, extracts user info from the
    id_token, creates or updates the user in the database, and returns a JWT.
    """
    if not code:
        return JSONResponse(
            status_code=400,
            content={"error": "Thiếu mã xác thực từ Google."},
        )

    expected_state = request.session.get("oauth_state")
    if expected_state != state:
        logger.warning(
            "[Auth] OAuth state mismatch | expected=%s | received=%s",
            expected_state,
            state,
        )
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid OAuth state"},
        )

    oauth_config = _get_oauth_client_config()

    try:
        flow = Flow.from_client_config(
            client_config={
                "web": {
                    "client_id": oauth_config["client_id"],
                    "client_secret": oauth_config["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            state=state,
            autogenerate_code_verifier=True,
        )
        flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

        flow.code_verifier = request.session.get("code_verifier")

        # Exchange the authorization code for credentials
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Decode the id_token to get user info
        token_request = google_requests.Request()
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            token_request,
            oauth_config["client_id"],
        )

        google_id = id_info.get("sub", "")
        email = id_info.get("email", "")
        name = id_info.get("name", "")
        picture_url = id_info.get("picture", "")

        if not google_id or not email:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Không lấy được thông tin người dùng từ Google."},
            )

        # Convert token expiry to string for DB storage
        token_expiry = None
        if credentials.expiry:
            token_expiry = credentials.expiry.isoformat()

        # Create or update user in database
        user = create_or_update_user(
            google_id=google_id,
            email=email,
            name=name,
            picture_url=picture_url,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_expiry=token_expiry,
        )

        # Create JWT
        access_token = create_access_token(user["id"], user["email"])

        logger.info(
            "[Auth] Đăng nhập thành công | email=%s | user_id=%s", email, user["id"])

        # Cleanup OAuth session data
        request.session.pop("oauth_state", None)
        request.session.pop("code_verifier", None)

        # Set JWT as httpOnly cookie and redirect to /ui
        response = RedirectResponse(url="/ui", status_code=302)
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            max_age=settings.JWT_EXPIRE_MINUTES * 60,
            samesite="lax",
        )
        return response

    except Exception as e:
        logger.error("[Auth] Lỗi xác thực OAuth: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": f"Lỗi xác thực: {str(e)}"},
        )


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Return the currently authenticated user's info.

    Requires a valid JWT in the Authorization header or access_token cookie.
    """
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "name": current_user["name"],
        "picture_url": current_user["picture_url"],
    }


@router.post("/logout")
async def logout():
    """
    Clear the access_token cookie to log the user out.
    """
    response = JSONResponse(content={"status": "logged_out"})
    response.delete_cookie(key="access_token", path="/")
    logger.info("[Auth] Đăng xuất thành công")
    return response
