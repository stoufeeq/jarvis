import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.config import get_settings

settings = get_settings()

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


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": "0.1.0"}
