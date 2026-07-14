"""Tests for BriefingService dedup behaviour.

Advisory-lock concurrency itself requires Postgres and true concurrent
sessions, which the SQLite in-memory test DB can't reproduce. What we
CAN cover here:

- get_or_create_today returns the existing row when one exists (no
  duplicate _generate call).
- regenerate_today respects REGENERATE_MIN_AGE_MINUTES and returns the
  existing briefing instead of calling Gemini when it's fresh.
- regenerate_today does call _generate when the existing briefing is
  older than the threshold.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.briefing import DailyBriefing
from app.models.user import User
from app.services.briefing import BriefingService


async def _make_user(db) -> User:
    user = User(
        email="test@example.com",
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_briefing(db, user_id: int, generated_at: datetime) -> DailyBriefing:
    briefing = DailyBriefing(
        user_id=user_id,
        briefing_date=generated_at.date(),
        overall_sentiment="neutral",
        summary="test",
        content_json=json.dumps({"overall_sentiment": "neutral"}),
        generated_at=generated_at,
    )
    db.add(briefing)
    await db.flush()
    return briefing


@pytest.mark.asyncio
async def test_get_or_create_returns_existing(db):
    user = await _make_user(db)
    existing = await _make_briefing(db, user.id, datetime.now(UTC))

    svc = BriefingService(db)
    with patch.object(svc, "_generate", new=AsyncMock()) as gen_mock:
        result = await svc.get_or_create_today(user)

    assert result.id == existing.id
    gen_mock.assert_not_called()


@pytest.mark.asyncio
async def test_regenerate_respects_min_age_window(db):
    """A briefing generated 5 min ago should be returned as-is; no new
    generation, no new Telegram push."""
    user = await _make_user(db)
    fresh = await _make_briefing(
        db, user.id, datetime.now(UTC) - timedelta(minutes=5)
    )

    svc = BriefingService(db)
    with patch.object(svc, "_generate", new=AsyncMock()) as gen_mock:
        result = await svc.regenerate_today(user)

    assert result.id == fresh.id
    gen_mock.assert_not_called()


@pytest.mark.asyncio
async def test_regenerate_generates_when_stale(db):
    """A briefing older than REGENERATE_MIN_AGE_MINUTES triggers a
    fresh generation."""
    user = await _make_user(db)
    stale_age = BriefingService.REGENERATE_MIN_AGE_MINUTES + 1
    await _make_briefing(
        db, user.id, datetime.now(UTC) - timedelta(minutes=stale_age)
    )

    svc = BriefingService(db)
    new_briefing = DailyBriefing(
        user_id=user.id,
        briefing_date=datetime.now(UTC).date(),
        overall_sentiment="bullish",
        summary="new",
        content_json=json.dumps({"overall_sentiment": "bullish"}),
        generated_at=datetime.now(UTC),
    )
    with patch.object(svc, "_generate", new=AsyncMock(return_value=new_briefing)) as gen_mock:
        result = await svc.regenerate_today(user)

    gen_mock.assert_called_once()
    assert result.overall_sentiment == "bullish"


@pytest.mark.asyncio
async def test_get_or_create_generates_when_none_exist(db):
    user = await _make_user(db)

    svc = BriefingService(db)
    new_briefing = DailyBriefing(
        user_id=user.id,
        briefing_date=datetime.now(UTC).date(),
        overall_sentiment="neutral",
        summary="fresh",
        content_json=json.dumps({"overall_sentiment": "neutral"}),
        generated_at=datetime.now(UTC),
    )
    with patch.object(svc, "_generate", new=AsyncMock(return_value=new_briefing)) as gen_mock:
        result = await svc.get_or_create_today(user)

    gen_mock.assert_called_once()
    assert result.summary == "fresh"
