"""
JWT authentication utilities for user session management.

Provides token creation, decoding, and FastAPI dependency functions
that extract the current user from an Authorization header or cookie.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException
from jose import jwt, JWTError, ExpiredSignatureError

from app.core.config import settings
from app.db.sqlite import get_user_by_id


def create_access_token(user_id: int, email: str) -> str:
    """
    Create a signed JWT access token for the given user.

    Args:
        user_id: The internal user ID from the database.
        email:   The user's email address.

    Returns:
        An encoded JWT string.
    """
    expire = datetime.now(timezone.utc) + \
        timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """
    Decode and validate a JWT access token.

    Args:
        token: The encoded JWT string.

    Returns:
        The decoded payload dict if valid, or ``None`` if invalid / expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except (JWTError, ExpiredSignatureError):
        return None


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency that extracts the authenticated user.

    Reads the JWT from:
    1. ``Authorization: Bearer <token>`` header
    2. ``access_token`` cookie

    Raises:
        HTTPException(401) if no valid token is found.
    """
    token = None

    # 1. Try Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2. Fallback to cookie
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=401, detail="Vui lòng đăng nhập để tiếp tục.")

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
        )

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token không hợp lệ.")

    user = get_user_by_id(int(user_id))
    if user is None:
        raise HTTPException(
            status_code=401, detail="Người dùng không tồn tại.")

    return user


async def get_optional_user(request: Request) -> dict | None:
    """
    FastAPI dependency that extracts the user if authenticated, otherwise returns ``None``.

    Use this for endpoints that work both with and without authentication.
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
