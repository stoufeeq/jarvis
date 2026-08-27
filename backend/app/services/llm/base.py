"""Provider-agnostic LLM types.

Two-method surface:
  - `complete(prompt, model)` — single-shot text-in, text-out. Used by
    news sentiment scoring and briefing.
  - `chat(messages, model)` — multi-turn conversation. Used by the AI
    advisor which passes conversation history each turn.

Deliberately narrow — we don't try to expose every provider feature
(tool use, streaming, function calling). If a future feature needs it,
extend the interface then rather than pre-emptively bloating it.

All exceptions from underlying SDKs / HTTP layers are wrapped in
`LLMError` so callers only have one exception class to handle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


class LLMError(RuntimeError):
    """Raised on any provider-side failure (network, quota, malformed
    response). Wraps the original error's message; callers should log
    and fall back rather than crash."""


class LLMClient(Protocol):
    """Provider-agnostic contract. Implementations translate to the
    concrete SDK / HTTP call, wrap failures in LLMError, and return
    the assistant's raw text (no post-processing).

    `model` is passed per call because the same provider client may
    serve multiple models (e.g. one OpenRouterClient with an API key
    can route to deepseek-r1 or claude-sonnet-5 on demand)."""

    provider: str

    async def complete(self, prompt: str, *, model: str, temperature: float = 0.4) -> str:
        ...

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        ...
