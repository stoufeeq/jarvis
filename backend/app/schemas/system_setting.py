"""Pydantic schemas for the admin Settings API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ModelCatalogEntry(BaseModel):
    id: str
    provider: Literal["gemini", "openrouter"]
    label: str
    tier: Literal["free", "cheap", "premium"]
    notes: str
    price_hint: str
    # Whether the required API key for this model's provider is present
    # on the server. UI grays out entries whose provider isn't configured.
    available: bool


class ModelSettingsRead(BaseModel):
    """Current per-task model choices (source-labelled so the UI can
    show which are DB-overridden vs env-default)."""
    news_model: str
    news_model_source: Literal["override", "env"]
    briefing_model: str
    briefing_model_source: Literal["override", "env"]
    chat_model: str
    chat_model_source: Literal["override", "env"]


class ModelSettingsUpdate(BaseModel):
    """PUT payload. Any field omitted = leave unchanged. Explicit None
    clears the override (falls back to env default)."""
    news_model: str | None = None
    briefing_model: str | None = None
    chat_model: str | None = None
    # Signal a field is intentionally cleared vs just omitted. Present
    # keys with value None → clear; absent keys → leave unchanged.
    # (Uses model_fields_set on the receiver side to distinguish.)
