import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Request
from fastapi.responses import RedirectResponse

ph = PasswordHasher()

JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 365
COOKIE_NAME = "dwmp_session"

# Password hash is stored in env var (pre-hashed with argon2)
# Generate with: python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-password'))"
PASSWORD_HASH = os.environ.get("PASSWORD_HASH", "")


def set_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str) -> bool:
    if not PASSWORD_HASH:
        return False
    try:
        return ph.verify(PASSWORD_HASH, password)
    except VerifyMismatchError:
        return False


def create_token() -> str:
    return jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False


def is_authenticated(request: Request) -> bool:
    if not PASSWORD_HASH:
        return True  # No password configured — open access
    # Check cookie (browser sessions)
    token = request.cookies.get(COOKIE_NAME, "")
    if verify_token(token):
        return True
    # Check Authorization header (API clients)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return verify_token(auth_header[7:])
    return False


def login_response(redirect_to: str = "/") -> RedirectResponse:
    response = RedirectResponse(redirect_to, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_token(),
        max_age=JWT_EXPIRY_DAYS * 86400,
        httponly=True,
        samesite="lax",
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
