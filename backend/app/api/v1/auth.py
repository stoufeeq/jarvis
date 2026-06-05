import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.database import get_db
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.user import UserCreate, UserRead
from app.services.email import password_reset_email, send_email
from app.services.password_reset import CODE_TTL, PasswordResetService
from app.services.user import UserService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    svc = UserService(db)
    if await svc.get_by_email(payload.email):
        raise ConflictError("Email already registered")
    user = await svc.create(payload)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    svc = UserService(db)
    user = await svc.get_by_email(payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/forgot-password", status_code=200)
async def forgot_password(payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Generate a 6-digit reset code and email it.

    Always returns 200 with the same response body so we don't leak
    whether the email is registered. Failures (SMTP misconfigured,
    user doesn't exist) are logged server-side but invisible to the
    caller — including to a brute-force enumerator.
    """
    svc = PasswordResetService(db)
    result = await svc.create_code_for_email(payload.email)

    if result is not None:
        user, code = result
        await db.commit()
        settings = get_settings()
        reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password"
        ttl_minutes = int(CODE_TTL.total_seconds() // 60)
        try:
            subject, html = password_reset_email(code, reset_url, ttl_minutes)
            await send_email(user.email, subject, html)
        except Exception as exc:
            log.warning("Password reset email send failed for %s: %s", user.email, exc)

    # Generic response either way.
    return {"ok": True, "detail": "If that email is registered, a reset code is on its way."}


@router.post("/reset-password", status_code=200)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Consume a (email, code) pair to set a new password.

    Same generic error message for every failure mode (no such email,
    expired code, wrong code, locked, etc.) so an attacker can't tell
    which step failed.
    """
    svc = PasswordResetService(db)
    ok = await svc.consume_code(payload.email, payload.code, payload.new_password)
    await db.commit()
    if not ok:
        raise UnauthorizedError("Invalid or expired reset code")
    return {"ok": True, "detail": "Password reset. You can now sign in."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token)
    except ValueError:
        raise UnauthorizedError("Invalid refresh token")

    if data.get("type") != "refresh":
        raise UnauthorizedError("Invalid token type")

    user_id = int(data["sub"])
    user = await UserService(db).get_by_id(user_id)
    if not user or not user.is_active:
        raise UnauthorizedError("User not found")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )
