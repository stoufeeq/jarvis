"""OpenRouter implementation of LLMClient.

OpenRouter exposes an OpenAI-compatible /chat/completions endpoint that
routes to ~100 upstream models (Claude, GPT, DeepSeek, Llama, Qwen,
Gemini, Mistral, …) with one API key. We call it directly with httpx
rather than pulling in the openai SDK — the payload is small and this
avoids a new dependency.

Rate-limit / retry note: OpenRouter can return 429 or 503 during model
provider hiccups. We don't retry automatically here — callers should
handle LLMError with fallback (e.g. news sentiment already skips the
batch and moves on).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.llm.base import LLMClient, LLMError, Message

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 90.0  # some reasoning models (r1, o1) legitimately take 60s+


class OpenRouterClient(LLMClient):
    provider = "openrouter"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        referer: str = "https://jarvis.local",
        title: str = "Jarvis",
    ):
        if not api_key:
            raise LLMError("OpenRouter API key missing")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # OpenRouter uses these headers for their public leaderboard /
        # attribution. They're optional but polite to include.
        self._referer = referer
        self._title = title

    async def complete(self, prompt: str, *, model: str, temperature: float = 0.4) -> str:
        return await self.chat(
            [Message(role="user", content=prompt)],
            model=model,
            temperature=temperature,
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._referer,
            "X-Title": self._title,
        }

        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            log.warning("OpenRouter transport error on model=%s: %s", model, exc)
            raise LLMError(f"OpenRouter transport error: {exc}") from exc

        if resp.status_code >= 400:
            # OpenRouter returns useful JSON error bodies; surface a
            # trimmed version so the caller's logs are informative.
            body = resp.text[:500]
            log.warning(
                "OpenRouter HTTP %s on model=%s: %s",
                resp.status_code, model, body,
            )
            raise LLMError(f"OpenRouter HTTP {resp.status_code}: {body}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise LLMError("OpenRouter returned non-string content")
            return content.strip()
        except (KeyError, IndexError, ValueError) as exc:
            log.warning(
                "OpenRouter malformed response on model=%s: %s (body=%s)",
                model, exc, resp.text[:500],
            )
            raise LLMError(f"OpenRouter malformed response: {exc}") from exc
