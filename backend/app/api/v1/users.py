from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services.email import send_email
from app.services.telegram import send_telegram
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.post("/me/test-email")
async def test_email(user: User = Depends(get_current_user)):
    """Send a test email to the current user to verify SMTP config."""
    settings = get_settings()
    if not settings.email_configured:
        return {"ok": False, "detail": "SMTP not configured — set SMTP_HOST, SMTP_USER and SMTP_PASSWORD in .env"}
    sent = await send_email(
        to_address=user.email,
        subject="Jarvis — email test",
        html_body=f"""
        <div style="font-family:sans-serif;padding:24px;">
          <h2>Email is working ✓</h2>
          <p>This test was sent to <strong>{user.email}</strong> from your Jarvis instance.</p>
          <p style="color:#64748b;font-size:13px;">You can now set alerts to <em>In-app + Email</em> and they will deliver here.</p>
        </div>""",
    )
    if sent:
        return {"ok": True, "detail": f"Test email sent to {user.email}"}
    return {"ok": False, "detail": "SMTP send failed — check server logs for details"}


@router.post("/me/test-telegram")
async def test_telegram(user: User = Depends(get_current_user)):
    """Send a test message to the user's configured Telegram chat."""
    settings = get_settings()
    if not settings.telegram_configured:
        return {"ok": False, "detail": "Telegram bot not configured — set TELEGRAM_BOT_TOKEN in .env"}
    if not user.telegram_chat_id:
        return {"ok": False, "detail": "No Telegram chat ID set. Save your chat ID in Settings first."}
    sent = await send_telegram(
        chat_id=user.telegram_chat_id,
        text=(
            "✅ <b>Jarvis Telegram link working</b>\n"
            "You will now receive triggered alerts and daily briefing summaries here."
        ),
    )
    if sent:
        return {"ok": True, "detail": f"Test message sent to chat {user.telegram_chat_id}"}
    return {"ok": False, "detail": "Telegram send failed — check chat ID and bot token"}


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # If changing password, require current password for verification
    if payload.password:
        if not payload.current_password:
            raise UnauthorizedError("Current password is required to set a new password")
        if not verify_password(payload.current_password, user.password_hash):
            raise UnauthorizedError("Current password is incorrect")

    return await UserService(db).update(user, payload)
