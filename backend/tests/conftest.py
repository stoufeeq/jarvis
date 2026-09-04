"""Pytest config — shared fixtures for the Jarvis test suite.

Tests run against an in-memory SQLite DB (separate from the dev/prod Postgres)
to keep them fast and isolated. Tests that need real market data are marked
@pytest.mark.live and skipped by default.

Two layers of fixture:

  db          — a bare AsyncSession, for unit-testing services directly.
  api / auth_api — an httpx client wired to the real FastAPI app, for
                exercising endpoints end-to-end (routing, dependencies,
                Pydantic validation, status codes, ownership checks).

The API fixtures override `get_db` so requests share the test session, and
they issue REAL JWTs via app.core.security rather than stubbing
get_current_user. That keeps the auth dependency itself under test — an
endpoint that forgets `Depends(get_current_user)` will show up as a 200 on
an unauthenticated request instead of silently passing.
"""

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Force test database BEFORE app imports so settings pick it up
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///:memory:"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["GEMINI_API_KEY"] = ""
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-production"

# Import the models package so every model registers with Base.metadata
# — otherwise FKs to tables a given test doesn't directly import (e.g.
# the new trades.account_id → accounts.id) fail with NoReferencedTable.
from app import models  # noqa: E402, F401
from app.database import Base  # noqa: E402


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Fresh in-memory SQLite DB with schema, per-test isolation."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


# ── API test scaffolding ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def api(db: AsyncSession) -> AsyncGenerator:
    """Unauthenticated httpx client bound to the real FastAPI app.

    `get_db` is overridden to hand every request the test session, so
    rows created directly in a test are visible to the endpoint (and
    vice versa) without a commit dance.

    Deliberately does NOT override get_current_user — protected routes
    must genuinely 401/403 here. Use `auth_api` for authenticated calls.
    """
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    async def _override_get_db():
        # The endpoint's own commit would normally close over its own
        # session; here we yield the shared test session and swallow the
        # commit so the fixture keeps control of the transaction.
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# bcrypt at 12 rounds costs ~150ms per hash by design. The ownership
# suite creates two users per test, which pushed the run to ~20s of pure
# key-stretching. Memoising by plaintext keeps REAL bcrypt in the loop
# (so login/verify paths are genuinely exercised) while collapsing
# hundreds of identical hashes to one per distinct password. A bcrypt
# hash is self-contained salt+digest, so reusing one for the same
# plaintext verifies exactly as a fresh hash would.
_HASH_CACHE: dict[str, str] = {}


def _cached_hash(password: str) -> str:
    from app.core.security import hash_password

    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = hash_password(password)
    return _HASH_CACHE[password]


@pytest_asyncio.fixture
async def make_user(db: AsyncSession):
    """Factory for real User rows. Ownership tests need at least two
    distinct users, so this returns a callable rather than a single user."""
    from app.models.user import User

    async def _make(
        email: str = "test@example.com",
        *,
        is_admin: bool = False,
        is_active: bool = True,
        password: str = "hunter2hunter2",
    ) -> User:
        u = User(
            email=email,
            password_hash=_cached_hash(password),
            full_name=email.split("@")[0],
            is_active=is_active,
            is_admin=is_admin,
        )
        db.add(u)
        await db.flush()
        return u

    return _make


@pytest.fixture
def auth_headers():
    """Build an Authorization header carrying a REAL signed access token
    for the given user. Using the genuine token path (rather than
    overriding get_current_user) keeps decode_token, the 'type' claim
    check, and the is_active check all under test."""
    from app.core.security import create_access_token

    def _headers(user) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _headers


@pytest_asyncio.fixture
async def auth_api(api, make_user, auth_headers):
    """Client pre-authenticated as a default non-admin user.

    Yields (client, user) so tests can attribute rows to the right owner.
    For multi-user ownership tests, create the second user with
    `make_user` and pass its headers explicitly via `auth_headers`.
    """
    user = await make_user("owner@example.com")
    api.headers.update(auth_headers(user))
    return api, user


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.live tests unless RUN_LIVE_TESTS=1 is set."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test (requires network); set RUN_LIVE_TESTS=1 to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
