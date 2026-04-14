from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertRead, AlertUpdate
from app.services.alert import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/", response_model=list[AlertRead])
async def list_alerts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await AlertService(db).list_for_user(user.id)


@router.post("/", response_model=AlertRead, status_code=201)
async def create_alert(
    payload: AlertCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await AlertService(db).create(user.id, payload)


@router.patch("/{alert_id}", response_model=AlertRead)
async def update_alert(
    alert_id: int,
    payload: AlertUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AlertService(db)
    alert = await svc.get(alert_id)
    if not alert:
        raise NotFoundError("Alert not found")
    if alert.user_id != user.id:
        raise ForbiddenError()
    return await svc.update(alert, payload)


@router.post("/check", response_model=list[AlertRead])
async def check_alerts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate active alerts against live prices and return any that just fired."""
    return await AlertService(db).check_and_trigger(user)


@router.post("/{alert_id}/acknowledge", response_model=AlertRead)
async def acknowledge_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a triggered alert as seen (removes from unread badge)."""
    svc = AlertService(db)
    alert = await svc.get(alert_id)
    if not alert:
        raise NotFoundError("Alert not found")
    if alert.user_id != user.id:
        raise ForbiddenError()
    return await svc.acknowledge(alert)


@router.post("/{alert_id}/rearm", response_model=AlertRead)
async def rearm_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset a triggered alert so it will watch for the condition again."""
    svc = AlertService(db)
    alert = await svc.get(alert_id)
    if not alert:
        raise NotFoundError("Alert not found")
    if alert.user_id != user.id:
        raise ForbiddenError()
    return await svc.rearm(alert)


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AlertService(db)
    alert = await svc.get(alert_id)
    if not alert:
        raise NotFoundError("Alert not found")
    if alert.user_id != user.id:
        raise ForbiddenError()
    await svc.delete(alert)
