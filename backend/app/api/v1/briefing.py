import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.briefing import BriefingService

router = APIRouter(prefix="/briefing", tags=["briefing"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BriefingRead(BaseModel):
    id: int
    briefing_date: date
    overall_sentiment: str
    summary: str | None
    content_json: str | None
    generated_at: datetime

    model_config = {"from_attributes": True}


class BriefingDetail(BriefingRead):
    """Same as BriefingRead but also exposes parsed content as a dict."""
    content: dict | None = None

    @classmethod
    def from_orm_with_content(cls, briefing) -> "BriefingDetail":
        obj = cls.model_validate(briefing)
        if briefing.content_json:
            try:
                obj.content = json.loads(briefing.content_json)
            except (json.JSONDecodeError, TypeError):
                obj.content = None
        return obj


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/today", response_model=BriefingDetail)
async def get_today_briefing(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return today's briefing, generating it on first request."""
    svc = BriefingService(db)
    briefing = await svc.get_or_create_today(user)
    await db.commit()
    return BriefingDetail.from_orm_with_content(briefing)


@router.post("/regenerate", response_model=BriefingDetail)
async def regenerate_today_briefing(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-regenerate today's briefing, replacing any existing one."""
    svc = BriefingService(db)
    briefing = await svc.regenerate_today(user)
    await db.commit()
    return BriefingDetail.from_orm_with_content(briefing)


@router.get("/history", response_model=list[BriefingRead])
async def get_briefing_history(
    limit: int = 30,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return past briefings for the current user, newest first."""
    svc = BriefingService(db)
    briefings = await svc.get_history(user.id, limit=limit)
    return briefings


@router.get("/{briefing_id}", response_model=BriefingDetail)
async def get_briefing(
    briefing_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a specific historical briefing by ID."""
    svc = BriefingService(db)
    briefing = await svc.get_by_id(briefing_id, user.id)
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return BriefingDetail.from_orm_with_content(briefing)


@router.delete("/{briefing_id}", status_code=204)
async def delete_briefing(
    briefing_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific briefing."""
    svc = BriefingService(db)
    deleted = await svc.delete_briefing(briefing_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Briefing not found")
    await db.commit()
