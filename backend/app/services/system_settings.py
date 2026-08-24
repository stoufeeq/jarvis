"""Read/write helpers for the `system_settings` key-value table.

Values are opaque text — callers own encoding (usually a plain string,
occasionally a small JSON blob). Keys are namespaced with dots by
convention (`llm.news_model`, `llm.briefing_model`, etc.) so future
non-LLM settings don't collide.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting


class SystemSettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, key: str) -> str | None:
        row = await self.db.execute(
            select(SystemSetting.value).where(SystemSetting.key == key)
        )
        return row.scalar_one_or_none()

    async def get_many(self, keys: list[str]) -> dict[str, str | None]:
        """Batch fetch — single query for multiple keys, returns
        {key: value or None}. Missing keys are present in the dict
        with value None so callers can safely dict-lookup."""
        if not keys:
            return {}
        rows = await self.db.execute(
            select(SystemSetting.key, SystemSetting.value).where(SystemSetting.key.in_(keys))
        )
        found = {k: v for k, v in rows.all()}
        return {k: found.get(k) for k in keys}

    async def set(self, key: str, value: str | None, updated_by: int | None = None) -> None:
        """Upsert. NULL value = clear (falls back to env default in
        readers). Passing updated_by threads the acting admin's user
        id into the audit column."""
        existing = await self.db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        row = existing.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            self.db.add(
                SystemSetting(
                    key=key,
                    value=value,
                    updated_by=updated_by,
                    updated_at=now,
                )
            )
        else:
            row.value = value
            row.updated_by = updated_by
            row.updated_at = now
        await self.db.flush()
