"""Screener endpoints — systematic, whole-market rankings.

Currently one screen (Greenblatt's Magic Formula). Results come from a
cached table written by a weekly Celery job: computing the screen needs
three yfinance calls per ticker across ~500 names, which is far too slow
to do on a request.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.magic_formula import MagicFormulaService

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/magic-formula")
async def get_magic_formula(
    limit: int = Query(30, ge=5, le=200),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Top N by combined Magic Formula rank, with the exclusion tally.

    Returns computed_at so the UI can show data age — the underlying job
    runs weekly, and the inputs are annual-report figures, so a stale
    result is expected rather than alarming."""
    return await MagicFormulaService(db).get_screen(limit=limit)


@router.post("/magic-formula/refresh")
async def refresh_magic_formula(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recompute now rather than waiting for the weekly job.

    Slow — 15-25 minutes for ~500 tickers — so this is dispatched to
    Celery rather than run inline, which would hold a request open long
    past any sensible timeout and pin a DB connection while doing it."""
    from app.workers.tasks.magic_formula import refresh_magic_formula as task

    task.delay()
    return {
        "queued": True,
        "detail": "Refresh queued. Takes 15-25 minutes for ~500 tickers; "
                  "the page will show the new snapshot once it completes.",
    }
