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
    # ── OpenRouter free tier — mostly dead as of Aug 2026 ─────────
    # DeepSeek V3.1, Llama 3.3, and Qwen 2.5 :free variants have all
    # been pulled by their providers within days of each other. The
    # remaining slugs below are believed still-free but the tier is
    # unreliable — save-time validation will reject a dead slug with
    # a clear error. If we hit "all my free slugs die weekly" as a
    # pattern, promote the dynamic-catalog build (fetch OpenRouter's
    # /api/v1/models on load, filter for pricing.prompt == "0").
    #
    # Gemini's free tier via Google's own API is more stable than
    # OpenRouter's aggregator arrangement — see Gemini 2.5 Flash above.

    # ── OpenRouter — free / research (add cautiously) ─────────────
    # (Ox Alpha stealth/ox-alpha was pulled from OpenRouter within
    # ~48h of listing — Aug 28 2026. Removed here so validate-on-save
    # doesn't let admins step on it. If a similar stealth model
    # appears, add it with the same tier=free + "may vanish" hint.)
    #
    # Ling 3.0 Flash Fin is a finance-tuned free model (inclusion AI).
    # Genuinely interesting fit for the news scoring workload —
    # small, fast, domain-specialised. Verify availability before
    # recommending broadly.
    ModelEntry(
        id="inclusionai/ling-3.0-flash-fin:free",
        provider="openrouter",
        label="Ling 3.0 Flash Fin (free)",
        tier="free",
        notes="Finance-tuned small model. Good fit for news sentiment. Free-tier may rotate.",
        price_hint="Free (may rotate)",
    ),

    # ── OpenRouter — cheap paid (pennies at Jarvis volume) ────────
    ModelEntry(
        id="deepseek/deepseek-chat-v3.1",
        provider="openrouter",
        label="DeepSeek V3.1 (paid)",
        tier="cheap",
        notes="V3 chat model — reliable, no free-tier rate caps. Pennies/month at Jarvis volume.",
        price_hint="~$0.27/M in",
    ),
    ModelEntry(
        id="meta-llama/llama-3.3-70b-instruct",
        provider="openrouter",
        label="Llama 3.3 70B (paid)",
        tier="cheap",
        notes="Meta's open-weight 70B via cheapest provider. Solid all-rounder.",
        price_hint="~$0.20/M in",
    ),
    ModelEntry(
        id="deepseek/deepseek-r1",
        provider="openrouter",
        label="DeepSeek R1 (paid)",
        tier="cheap",
        notes="Full R1 reasoning model without free-tier caps. Best reasoning $/perf on the market.",
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
