"""Admin-only global settings endpoints.

Currently just LLM model selection per task (news / briefing / chat).
The catalog endpoint is admin-only too — no reason for a non-admin
user to see the pricing table since they can't act on it.

`available` on catalog entries reflects whether the required API key
is present in server env. UI grays out providers without a key so
admins don't pick something the backend will fail to call.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.schemas.system_setting import ModelCatalogEntry, ModelSettingsRead, ModelSettingsUpdate
from app.services.llm.catalog import MODEL_CATALOG
from app.services.llm.factory import SETTING_KEY, TaskType, _env_default
from app.services.system_settings import SystemSettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


def _catalog_availability() -> dict[str, bool]:
    """Which providers have keys configured — drives the UI's disabled state."""
    settings = get_settings()
    return {
        "gemini": bool(settings.gemini_api_key),
        "openrouter": bool(settings.openrouter_api_key),
    }


@router.get("/models/catalog", response_model=list[ModelCatalogEntry])
async def list_catalog(_: User = Depends(require_admin)):
    """Full curated catalog for the model-picker UI."""
    avail = _catalog_availability()
    return [
        ModelCatalogEntry(
            id=m.id,
            provider=m.provider,
            label=m.label,
            tier=m.tier,
            notes=m.notes,
            price_hint=m.price_hint,
            available=avail.get(m.provider, False),
        )
        for m in MODEL_CATALOG
    ]


@router.get("/models", response_model=ModelSettingsRead)
async def get_model_settings(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Currently active model per task, plus whether the value comes
    from a DB override or the env default (so UI can indicate 'edited')."""
    svc = SystemSettingsService(db)
    keys = [SETTING_KEY["news"], SETTING_KEY["briefing"], SETTING_KEY["chat"]]
    overrides = await svc.get_many(keys)

    def _resolve(task: TaskType) -> tuple[str, str]:
        override = overrides.get(SETTING_KEY[task])
        if override:
            return override, "override"
        return _env_default(task), "env"

    news, news_src = _resolve("news")
    briefing, briefing_src = _resolve("briefing")
    chat, chat_src = _resolve("chat")
    return ModelSettingsRead(
        news_model=news, news_model_source=news_src,  # type: ignore[arg-type]
        briefing_model=briefing, briefing_model_source=briefing_src,  # type: ignore[arg-type]
        chat_model=chat, chat_model_source=chat_src,  # type: ignore[arg-type]
    )


@router.put("/models", response_model=ModelSettingsRead)
async def update_model_settings(
    payload: ModelSettingsUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Persist per-task model choices. Semantics:
      - Field absent from payload  → leave existing setting untouched
      - Field present with value    → upsert override
      - Field present with `null`   → clear override (falls back to env)

    Each non-null model value is smoke-tested against the provider
    before being persisted (a 1-token 'ping' completion). If the
    provider rejects it (deprecated slug, missing API key, quota
    exhausted, etc.), we return 422 with the raw provider message so
    the admin sees the failure at save time instead of hours later
    when a Celery task or a chat request finally exercises it. Prior
    to this, a stored bad value could silently break the daily
    briefing or advisor chat.
    """
    import asyncio as _asyncio
    from app.services.llm import LLMError, Message, resolve_client

    # ── Pre-save validation ──────────────────────────────────────
    # Test each non-null model with a tiny ping. Runs in parallel so
    # total wait = slowest single test, not the sum. Uses a 30s cap
    # (down from the client's 90s default) + max_tokens=5 so reasoning
    # models don't burn tokens or block the UI on validation.
    fields_set = payload.model_fields_set
    task_field_map: dict[TaskType, str] = {
        "news": "news_model",
        "briefing": "briefing_model",
        "chat": "chat_model",
    }
    to_persist: list[tuple[TaskType, str | None]] = []
    to_validate: list[tuple[str, str]] = []  # (label, model_id) pairs to smoke-test
    for task, field in task_field_map.items():
        if field not in fields_set:
            continue
        value = getattr(payload, field)
        to_persist.append((task, value))
        if value:
            to_validate.append((task, value))

    VALIDATE_TIMEOUT = 30.0
    VALIDATE_MAX_TOKENS = 5

    async def _validate(label: str, value: str) -> tuple[str, str, Exception | None]:
        """Returns (label, model_id, error_or_None). We collect errors
        instead of raising so a single slow model doesn't hide others."""
        try:
            client = resolve_client(value)
            await client.chat(  # type: ignore[call-arg]
                [Message(role="user", content="ping")],
                model=value,
                temperature=0.0,
                max_tokens=VALIDATE_MAX_TOKENS,
                timeout=VALIDATE_TIMEOUT,
            )
            return (label, value, None)
        except Exception as exc:
            return (label, value, exc)

    if to_validate:
        results = await _asyncio.gather(*(_validate(t, v) for t, v in to_validate))
        for label, value, err in results:
            if err is None:
                continue
            if isinstance(err, LLMError):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Model {value!r} rejected by provider: {err}. "
                        "Check openrouter.ai/models (or your Gemini quota) "
                        "and pick an available model."
                    ),
                )
            raise HTTPException(
                status_code=422,
                detail=f"Model {value!r} could not be initialised: {err}",
            )

    # ── Persist (only after every value passed its smoke test) ────
    svc = SystemSettingsService(db)
    for task, value in to_persist:
        await svc.set(SETTING_KEY[task], value, updated_by=admin.id)
    await db.commit()

    # Return the fresh state.
    return await get_model_settings(admin, db)  # type: ignore[arg-type]
