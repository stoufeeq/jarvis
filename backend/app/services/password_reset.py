"""
Password reset — generates short-lived 6-digit codes, emails them, and
consumes them to set a new password.

Security shape:
- Code is 6 random decimal digits (1M keyspace). Sufficient given the
  attempt-count lock + 15-minute expiry; a brute-force has ~5 tries
  inside the expiry window before the token self-locks.
- Code is hashed (sha256) before being written to the DB so a DB leak
  doesn't surrender unused codes.
- Requesting a new code invalidates all prior unused codes for the user
  (marks them used) — prevents users from stockpiling codes.
- Forgot-password endpoint always returns 200 (no email-existence leak).
- Consume requires (email, code) — narrows brute-force to one user at a time.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    """6-digit numeric, zero-padded."""
    return f"{secrets.randbelow(1_000_000):06d}"


class PasswordResetService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_code_for_email(self, email: str) -> tuple[User, str] | None:
        """Look up the user by email and create a fresh 6-digit code.

        Returns (user, plaintext_code) or None if the email isn't registered.
        The plaintext is returned ONLY to be emailed — never persisted.

        Invalidates all prior unused codes for the user so an attacker who
        sees stale codes can't combine them with a fresh one.
        """
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        if user is None:
            return None

        now = datetime.now(UTC)

        # Invalidate any unused codes for this user
        await self.db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=now)
        )

        code = _generate_code()
        token = PasswordResetToken(
            user_id=user.id,
            code_hash=_hash_code(code),
            expires_at=now + CODE_TTL,
            used_at=None,
            attempt_count=0,
            created_at=now,
        )
        self.db.add(token)
        await self.db.flush()
        return user, code

    async def consume_code(self, email: str, code: str, new_password: str) -> bool:
        """Validate (email, code) and set the new password.

        Returns True on success, False on any failure (expired, wrong code,
        locked, no such user). The caller surfaces the same error message
        for all failure modes so we don't leak which condition tripped.

        Side effects:
        - Wrong-code attempt: increments attempt_count; locks the token
          (sets used_at) on the MAX_ATTEMPTS-th failure.
        - Success: sets used_at, updates user.password_hash.
        """
        now = datetime.now(UTC)

        # Find the most recent unused unexpired token for this user
        result = await self.db.execute(
            select(PasswordResetToken)
            .join(User, PasswordResetToken.user_id == User.id)
            .where(
                User.email == email.lower(),
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .order_by(PasswordResetToken.created_at.desc())
            .limit(1)
        )
        token = result.scalar_one_or_none()
        if token is None:
            return False

        if _hash_code(code) != token.code_hash:
            token.attempt_count += 1
            if token.attempt_count >= MAX_ATTEMPTS:
                token.used_at = now  # lock it
                log.warning(
                    "Password reset token %s locked after %d wrong attempts",
                    token.id, MAX_ATTEMPTS,
                )
            await self.db.flush()
            return False

        # Right code → set new password, mark token used.
        user_result = await self.db.execute(
            select(User).where(User.id == token.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return False
        user.password_hash = hash_password(new_password)
        token.used_at = now
        await self.db.flush()
        return True
