"""Pytest config — shared fixtures for the Jarvis test suite.

Tests run against an in-memory SQLite DB (separate from the dev/prod Postgres)
to keep them fast and isolated. Tests that need real market data are marked
@pytest.mark.live and skipped by default.
"""

import asyncio
import os
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


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.live tests unless RUN_LIVE_TESTS=1 is set."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test (requires network); set RUN_LIVE_TESTS=1 to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
