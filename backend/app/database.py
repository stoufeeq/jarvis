import os
import sys
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()

# Detect Celery worker/beat context.
# - Production (Hetzner): COMMAND env var is set to "worker" or "beat" by docker-compose.prod.yml
# - Local dev: process is started via `celery -A app.workers...` so "celery" appears in argv
_in_worker = (
    os.environ.get("COMMAND") in ("worker", "beat")
    or any("celery" in arg for arg in sys.argv)
)

# In Celery context: NullPool means no pooling. Each operation gets a fresh
# connection bound to the current event loop. This avoids the
# "Task got Future attached to a different loop" RuntimeError that occurs when
# SQLAlchemy's pooled connections are reused across asyncio.run() calls (each
# task spins up a new event loop). Connection-creation overhead is negligible
# for the periodic batch tasks Celery runs.
#
# In API context (uvicorn): keep the pool — single long-lived event loop, high
# request throughput, pooling materially improves latency.
_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if _in_worker:
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
