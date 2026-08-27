"""Gemini implementation of LLMClient.

Wraps google-generativeai. Kept intentionally minimal — the SDK already
handles auth, retries, and streaming under the hood.

Note on multi-turn: Gemini's SDK has a `start_chat(history=...)` helper
but it takes its own `{role, parts}` shape. We translate our Message
list into that format inside chat().
"""
from __future__ import annotations

import logging

import google.generativeai as genai

from app.services.llm.base import LLMClient, LLMError, Message

log = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, api_key: str):
        if not api_key:
            raise LLMError("Gemini API key missing")
        genai.configure(api_key=api_key)
        self._api_key = api_key

    async def complete(self, prompt: str, *, model: str, temperature: float = 0.4) -> str:
        try:
            gen_model = genai.GenerativeModel(
                model_name=model,
                generation_config={"temperature": temperature},
            )
            response = await gen_model.generate_content_async(prompt)
            return (response.text or "").strip()
        except Exception as exc:  # SDK raises many types; unify them
            log.warning("Gemini complete() failed on model=%s: %s", model, exc)
            raise LLMError(f"Gemini complete failed: {exc}") from exc

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        timeout: float | None = None,  # noqa: ARG002 — SDK doesn't expose per-call timeout
    ) -> str:
        try:
            # Gemini's role names differ ("user"/"model" rather than
            # "user"/"assistant"). System messages are prepended to the
            # first user turn via the model's system_instruction slot.
            system_instructions = "\n\n".join(m.content for m in messages if m.role == "system")
            history: list[dict] = []
            for m in messages:
                if m.role == "system":
                    continue
                role = "user" if m.role == "user" else "model"
                history.append({"role": role, "parts": [m.content]})

            if not history:
                raise LLMError("chat() called with no user/assistant messages")

            gen_config: dict[str, float | int] = {"temperature": temperature}
            if max_tokens is not None:
                gen_config["max_output_tokens"] = max_tokens
            gen_model = genai.GenerativeModel(
                model_name=model,
                generation_config=gen_config,  # type: ignore[arg-type]
                system_instruction=system_instructions or None,
            )
            # Last message is the current turn; preceding history seeds
            # the chat context.
            last = history[-1]
            chat_session = gen_model.start_chat(history=history[:-1])
            response = await chat_session.send_message_async(last["parts"][0])
            return (response.text or "").strip()
        except LLMError:
            raise
        except Exception as exc:
            log.warning("Gemini chat() failed on model=%s: %s", model, exc)
            raise LLMError(f"Gemini chat failed: {exc}") from exc
