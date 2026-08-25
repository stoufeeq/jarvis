"""Curated model catalog.

A hand-picked set (~10 models) covering the sensible price/quality
points for Jarvis. We don't expose all 100+ OpenRouter models —
choice paralysis + risk of picking something unproven.

Each entry captures what the admin needs to make a per-task decision:
  - id           — the exact model ID sent to the provider
  - provider     — "gemini" or "openrouter" (drives client selection)
  - label        — human-readable name for the dropdown
  - tier         — free / cheap / premium (rough cost band)
  - notes        — one-line strength/tradeoff for tooltip
  - price_hint   — informal "$/million tokens" summary; may drift

`get_provider_for_model(id)` is the routing helper — factory calls it
to decide whether to instantiate GeminiClient or OpenRouterClient.

MAINTENANCE NOTE — OpenRouter free tiers rotate. Vendors periodically
pull free variants (e.g. DeepSeek pulled deepseek-chat-v3.1:free in
Aug 2026, redirecting users to the paid slug). When a user reports
"model unavailable for free" errors:
  - Check openrouter.ai/models for what's currently free
  - Update this catalog with the new slugs
  - Consider migrating admins to the paid equivalent — usually
    cents/month at Jarvis's volume
A dynamic-catalog fetch from OpenRouter's /api/v1/models would remove
this maintenance step entirely — see comment near MODEL_CATALOG.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provider = Literal["gemini", "openrouter"]
Tier = Literal["free", "cheap", "premium"]


@dataclass(frozen=True)
class ModelEntry:
    id: str
    provider: Provider
    label: str
    tier: Tier
    notes: str
    price_hint: str  # informal; verify at openrouter.ai/models before relying on it


MODEL_CATALOG: tuple[ModelEntry, ...] = (
    # ── Gemini (Google, free tier available) ──────────────────────
    ModelEntry(
        id="gemini-2.5-flash",
        provider="gemini",
        label="Gemini 2.5 Flash",
        tier="cheap",
        notes="Fast, cheap, JSON-friendly. Free tier caps at 20 req/day per key.",
        price_hint="Free tier / ~$0.15/M in",
    ),
    ModelEntry(
        id="gemini-2.5-pro",
        provider="gemini",
        label="Gemini 2.5 Pro",
        tier="premium",
        notes="Google's top reasoning model. Best for briefing synthesis.",
        price_hint="~$1.25/M in",
    ),
    # ── OpenRouter — free tier models ─────────────────────────────
    # (DeepSeek V3.1 free variant was pulled by vendor Aug 2026 —
    # use the paid slug below.)
    ModelEntry(
        id="deepseek/deepseek-r1:free",
        provider="openrouter",
        label="DeepSeek R1 (free)",
        tier="free",
        notes="Reasoning model — thinks step-by-step. Slower but stronger on hard tasks.",
        price_hint="Free (may rotate)",
    ),
    ModelEntry(
        id="meta-llama/llama-3.3-70b-instruct:free",
        provider="openrouter",
        label="Llama 3.3 70B (free)",
        tier="free",
        notes="Meta's open-weight 70B. Solid all-rounder, no signup upcharge.",
        price_hint="Free (may rotate)",
    ),
    ModelEntry(
        id="qwen/qwen-2.5-72b-instruct:free",
        provider="openrouter",
        label="Qwen 2.5 72B (free)",
        tier="free",
        notes="Alibaba's 72B — strong on structured output and multilingual.",
        price_hint="Free (may rotate)",
    ),
    # ── OpenRouter — cheap paid ──────────────────────────────────
    ModelEntry(
        id="deepseek/deepseek-chat-v3.1",
        provider="openrouter",
        label="DeepSeek V3.1 (paid)",
        tier="cheap",
        notes="V3 chat model — reliable, no free-tier rate caps. Pennies at Jarvis volume.",
        price_hint="~$0.27/M in",
    ),
    ModelEntry(
        id="deepseek/deepseek-r1",
        provider="openrouter",
        label="DeepSeek R1 (paid)",
        tier="cheap",
        notes="Full R1 without free-tier rate limits. Best reasoning $/perf on the paid market.",
        price_hint="~$0.55/M in",
    ),
    ModelEntry(
        id="anthropic/claude-haiku-4-5",
        provider="openrouter",
        label="Claude Haiku 4.5",
        tier="cheap",
        notes="Anthropic's fast tier — great JSON, quick, cheap. Good news sentiment pick.",
        price_hint="~$1/M in",
    ),
    ModelEntry(
        id="anthropic/claude-sonnet-5",
        provider="openrouter",
        label="Claude Sonnet 5",
        tier="premium",
        notes="Balanced Anthropic model. Great chat / advisor pick.",
        price_hint="~$3/M in",
    ),
    ModelEntry(
        id="anthropic/claude-opus-5",
        provider="openrouter",
        label="Claude Opus 5",
        tier="premium",
        notes="Anthropic's top reasoning. Best briefing quality; most expensive.",
        price_hint="~$15/M in",
    ),
    ModelEntry(
        id="openai/gpt-5",
        provider="openrouter",
        label="GPT-5",
        tier="premium",
        notes="OpenAI's top model. Good all-rounder alternative to Opus 5.",
        price_hint="~$10/M in",
    ),
)


_BY_ID: dict[str, ModelEntry] = {m.id: m for m in MODEL_CATALOG}


def get_entry(model_id: str) -> ModelEntry | None:
    return _BY_ID.get(model_id)


def get_provider_for_model(model_id: str) -> Provider:
    """Return the provider that should be used for the given model.
    Falls back to 'openrouter' for unknown IDs — most non-Gemini IDs
    look like 'org/model' and are OpenRouter-routable. Gemini IDs are
    the exception (no slash prefix)."""
    entry = _BY_ID.get(model_id)
    if entry:
        return entry.provider
    # Fallback heuristic: Gemini IDs start with 'gemini-'; everything
    # else assumed OpenRouter-routable.
    if model_id.startswith("gemini-"):
        return "gemini"
    return "openrouter"
