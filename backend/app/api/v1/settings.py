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
    from app.services.llm import LLMError, Message, resolve_client

    # ── Pre-save validation ──────────────────────────────────────
    # Only test the fields the admin actually set to a non-null
    # value. Absent fields = no change (no test); None = clear the
    # override (no model to test against).
    fields_set = payload.model_fields_set
    task_field_map: dict[TaskType, str] = {
        "news": "news_model",
        "briefing": "briefing_model",
        "chat": "chat_model",
    }
    to_persist: list[tuple[TaskType, str | None]] = []
    for task, field in task_field_map.items():
        if field not in fields_set:
            continue
        value = getattr(payload, field)
        to_persist.append((task, value))
        if value:
            # Quick end-to-end sanity check: instantiate the client,
            # send one tiny completion. Any provider error surfaces
            # here as a 422 with the raw message.
            try:
                client = resolve_client(value)
                await client.chat(
                    [Message(role="user", content="ping")],
                    model=value,
                    temperature=0.0,
                )
            except LLMError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Model {value!r} rejected by provider: {exc}. "
                        "Check openrouter.ai/models (or your Gemini quota) "
                        "and pick an available model."
                    ),
                )
            except Exception as exc:
                # Non-LLMError probably means missing API key or
                # config bug. Same 422 for a coherent UI experience.
                raise HTTPException(
                    status_code=422,
                    detail=f"Model {value!r} could not be initialised: {exc}",
                )

    # ── Persist (only after every value passed its smoke test) ────
    svc = SystemSettingsService(db)
    for task, value in to_persist:
        await svc.set(SETTING_KEY[task], value, updated_by=admin.id)
    await db.commit()

    # Return the fresh state.
    return await get_model_settings(admin, db)  # type: ignore[arg-type]
