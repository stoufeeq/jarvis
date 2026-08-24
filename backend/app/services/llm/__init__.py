"""LLM provider abstraction.

Three tasks in Jarvis touch LLMs — news sentiment scoring, daily
briefing generation, AI advisor chat. Each has different requirements
(volume, latency, reasoning depth) so each is configured independently.

Provider-agnostic interface: `LLMClient` + `Message` + `LLMError`.

Two providers today: `GeminiClient` (google-generativeai) and
`OpenRouterClient` (thin httpx wrapper around OpenRouter's
OpenAI-compatible endpoint — one API key, many models).

Selection is done via `factory.get_llm_for_task(db, task)`. The factory
reads the admin's runtime override from `system_settings` (falling back
to env defaults), looks up the model in the catalog to determine the
provider, and returns the appropriate client. Callers should invoke
the factory per request — clients are cheap to construct and this
avoids caching a stale API key when the admin updates settings.
"""

from app.services.llm.base import LLMClient, LLMError, Message
from app.services.llm.catalog import MODEL_CATALOG, ModelEntry, get_provider_for_model
from app.services.llm.factory import (
    TaskType,
    get_llm_for_task,
    get_model_for_task,
    resolve_client,
)
from app.services.llm.gemini import GeminiClient
from app.services.llm.openrouter import OpenRouterClient

__all__ = [
    "LLMClient",
    "LLMError",
    "Message",
    "GeminiClient",
    "OpenRouterClient",
    "MODEL_CATALOG",
    "ModelEntry",
    "get_provider_for_model",
    "TaskType",
    "get_llm_for_task",
    "get_model_for_task",
    "resolve_client",
]
