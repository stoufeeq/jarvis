"""Task → model → client resolution.

Callers use one function: `get_llm_for_task(db, task)`. That returns
a (client, model_id) pair — the client to call, the model to pass to
`.complete()` / `.chat()`.

Resolution order for the model:
  1. Runtime override in system_settings (admin-set via UI)
  2. Env default from Settings (news_model / briefing_model / chat_model)

Provider is derived from the model ID via catalog.get_provider_for_model,
so switching a task's model in the UI to (e.g.) `anthropic/claude-sonnet-5`
automatically routes through OpenRouterClient.

Design note: we construct a fresh client per call instead of caching.
Clients are cheap (just holding an API key + tiny state) and this
prevents a stale key from persisting after an admin edit.
"""
from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.llm.base import LLMClient, LLMError
from app.services.llm.catalog import get_provider_for_model
from app.services.llm.gemini import GeminiClient
from app.services.llm.openrouter import OpenRouterClient
from app.services.system_settings import SystemSettingsService

log = logging.getLogger(__name__)

TaskType = Literal["news", "briefing", "chat"]

# system_settings keys — namespaced under 'llm.' so a future
# non-LLM setting doesn't collide.
SETTING_KEY = {
    "news": "llm.news_model",
    "briefing": "llm.briefing_model",
    "chat": "llm.chat_model",
}


def _env_default(task: TaskType) -> str:
    s = get_settings()
    return {
        "news": s.news_model,
        "briefing": s.briefing_model,
        "chat": s.chat_model,
    }[task]


async def get_model_for_task(db: AsyncSession, task: TaskType) -> str:
    """Return the currently-active model ID for a task. DB override
    takes precedence over env default."""
    override = await SystemSettingsService(db).get(SETTING_KEY[task])
    return override or _env_default(task)


def resolve_client(model_id: str) -> LLMClient:
    """Instantiate the right provider client for a given model ID.
    Raises LLMError if the required API key isn't configured."""
    settings = get_settings()
    provider = get_provider_for_model(model_id)
    if provider == "gemini":
        return GeminiClient(api_key=settings.gemini_api_key)
    if provider == "openrouter":
        return OpenRouterClient(api_key=settings.openrouter_api_key)
    # Exhaustive Literal — unreachable at runtime, but keeps type checkers happy.
    raise LLMError(f"Unknown provider for model {model_id!r}")


async def get_llm_for_task(db: AsyncSession, task: TaskType) -> tuple[LLMClient, str]:
    """One-shot resolver used by services. Returns (client, model_id).

    Raises LLMError if resolution fails (e.g. admin picked an
    OpenRouter model but the OPENROUTER_API_KEY env var is empty).
    Callers should catch LLMError and log; do not crash the request."""
    model_id = await get_model_for_task(db, task)
    client = resolve_client(model_id)
    return client, model_id
