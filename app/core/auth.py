"""
Centralized Google OAuth 2.0 authentication module.

Provides thread-safe, cached access to Gmail and Google Calendar API service
instances.  Credentials are loaded from disk only once and transparently
refreshed when expired.  Token and client-secret file paths are read from
the centralized ``app.core.config.settings``.
"""

import logging
import threading
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (read from centralised Settings)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]  # app/

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Paths from settings (defaults defined in config.py)
CREDENTIALS_FILE = Path(settings.GOOGLE_CREDENTIALS_PATH)
TOKEN_FILE = Path(settings.GOOGLE_TOKEN_PATH)

# ---------------------------------------------------------------------------
# Thread-safe singleton cache
# ---------------------------------------------------------------------------
_lock: threading.Lock = threading.Lock()
_creds: Credentials | None = None
_gmail_service = None
_calendar_service = None


def _get_or_refresh_credentials() -> Credentials:
    """
    Load credentials from disk (cached) and transparently refresh if expired.

    * **First call** – loads ``token.json`` (or runs interactive OAuth flow
      if no token exists).
    * **Subsequent calls** – returns the cached ``Credentials`` object.
    * If the cached token is expired but has a *refresh_token* the library
      refreshes it automatically on the next API call, so this function
      itself does not need to force a refresh unless the credentials are
      completely absent.

    Thread-safe: protected by an internal ``threading.Lock``.
    """
    global _creds

    with _lock:
        if _creds is not None:
            return _creds

        if TOKEN_FILE.exists():
            _creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE), SCOPES
            )
            logger.debug("Loaded credentials from %s", TOKEN_FILE)

        if not _creds or not _creds.valid:
            if _creds and _creds.expired and _creds.refresh_token:
                logger.info("Refreshing expired access token …")
                _creds.refresh(Request())
            else:
                logger.info(
                    "No valid token – starting interactive OAuth flow …"
                )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                _creds = flow.run_local_server(port=0)

            # Persist the (possibly refreshed) token back to disk
            with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
                fh.write(_creds.to_json())
            logger.info("Token persisted to %s", TOKEN_FILE)

        return _creds


def get_gmail_service():
    """
    Return a **cached** Gmail API service instance (v1).

    The underlying ``Credentials`` object is loaded and refreshed
    transparently (see :func:`_get_or_refresh_credentials`).  The service
    object itself is built once and reused on subsequent calls.

    Returns:
        googleapiclient.discovery.Resource: Authorised Gmail service.
    """
    global _gmail_service

    if _gmail_service is not None:
        return _gmail_service

    creds = _get_or_refresh_credentials()
    _gmail_service = build("gmail", "v1", credentials=creds)
    logger.info("Built & cached Gmail service")
    return _gmail_service


def get_calendar_service():
    """
    Return a **cached** Google Calendar API service instance (v3).

    The underlying ``Credentials`` object is loaded and refreshed
    transparently (see :func:`_get_or_refresh_credentials`).  The service
    object itself is built once and reused on subsequent calls.

    Returns:
        googleapiclient.discovery.Resource: Authorised Calendar service.
    """
    global _calendar_service

    if _calendar_service is not None:
        return _calendar_service

    creds = _get_or_refresh_credentials()
    _calendar_service = build("calendar", "v3", credentials=creds)
    logger.info("Built & cached Calendar service")
    return _calendar_service
