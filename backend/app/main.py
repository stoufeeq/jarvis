import logging
import os
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.config import get_settings

settings = get_settings()

# ── Sentry (optional) — only initializes when SENTRY_DSN is set ─────────────
# Enables exception capture + performance tracing across FastAPI, asyncpg,
# httpx, and celery. Disabled by default to avoid third-party noise during
# local dev. Set SENTRY_DSN in production .env to turn on.
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=settings.app_env,
            release=os.environ.get("GIT_SHA", "unknown")[:8],
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
        )
    except Exception as exc:
        # Never let Sentry init fail the app
        logging.warning("Sentry init failed: %s", exc)

# Root logger at WARNING — silences third-party noise (yfinance, httpx, urllib3, etc.)
# Our own "jarvis" logger stays at INFO so meaningful app events are still captured.
logging.basicConfig(level=logging.WARNING)
logging.getLogger("jarvis").setLevel(logging.INFO)

# Silence particularly chatty libraries
for _lib in ("yfinance", "urllib3", "httpx", "hpack", "peewee", "charset_normalizer"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

logger = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Jarvis API",
    description="Financial intelligence platform — portfolio management, signals, AI advisor",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# CORS must be added before any other middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to settings.cors_origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log unhandled exceptions and return a JSON 500 (with CORS headers via middleware)."""
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
    )


app.include_router(api_router)


def _git_sha() -> str:
    """Resolve the current git commit SHA (short).

    Works locally (reads .git) and in Docker images (reads GIT_SHA env
    var which the deploy workflow can set, or falls back to 'unknown')."""
    import os
    sha = os.environ.get("GIT_SHA")
    if sha:
        return sha[:8]
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


_VERSION = "0.1.0"
_GIT_SHA = _git_sha()


@app.get("/health", tags=["health"])
async def health(deep: bool = False):
    """Health check endpoint.

    By default returns a minimal 200 with version + git SHA — fast, suitable
    for load balancers and post-deploy verification.

    Pass `?deep=true` to also probe Postgres, Redis, and Celery worker
    freshness. Returns 503 if any critical subsystem is failing."""
    payload: dict = {
        "status": "ok",
        "version": _VERSION,
        "git_sha": _GIT_SHA,
    }
    if not deep:
        return payload

    # Deep check — probe subsystems
    from datetime import datetime, timezone
    import asyncio
    from sqlalchemy import text

    payload["checks"] = {}

    # Postgres
    try:
        from app.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        payload["checks"]["postgres"] = "ok"
    except Exception as exc:
        payload["checks"]["postgres"] = f"fail: {type(exc).__name__}"
        payload["status"] = "degraded"

    # Redis (broker)
    try:
        import redis.asyncio as redis_async
        client = redis_async.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        payload["checks"]["redis"] = "ok"
    except Exception as exc:
        payload["checks"]["redis"] = f"fail: {type(exc).__name__}"
        payload["status"] = "degraded"

    # Celery worker — verify a task succeeded recently (within 30 min)
    try:
        from app.workers.celery_app import celery_app
        from celery.result import AsyncResult  # noqa: F401

        # Inspect active workers
        i = celery_app.control.inspect(timeout=2)
        ping = await asyncio.get_event_loop().run_in_executor(None, i.ping)
        if ping:
            payload["checks"]["celery"] = f"ok ({len(ping)} worker(s))"
        else:
            payload["checks"]["celery"] = "fail: no workers responding"
            payload["status"] = "degraded"
    except Exception as exc:
        payload["checks"]["celery"] = f"fail: {type(exc).__name__}"
        payload["status"] = "degraded"

    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    if payload["status"] != "ok":
        return JSONResponse(status_code=503, content=payload)
    return payload
