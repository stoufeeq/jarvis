import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertType
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertUpdate
from app.services.email import alert_triggered_email, send_email
from app.services.telegram import alert_message as telegram_alert_message
from app.services.telegram import send_telegram
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)


class AlertService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, alert_id: int) -> Alert | None:
        result = await self.db.execute(select(Alert).where(Alert.id == alert_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[Alert]:
        result = await self.db.execute(
            select(Alert).where(Alert.user_id == user_id).order_by(Alert.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, user_id: int, payload: AlertCreate) -> Alert:
        alert = Alert(user_id=user_id, **payload.model_dump())
        self.db.add(alert)
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def update(self, alert: Alert, payload: AlertUpdate) -> Alert:
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(alert, field, value)
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def delete(self, alert: Alert) -> None:
        await self.db.delete(alert)
        await self.db.flush()

    async def acknowledge(self, alert: Alert) -> Alert:
        """Mark a triggered alert as seen. It stays visible in history but
        no longer counts toward the unread badge."""
        alert.acknowledged_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def rearm(self, alert: Alert) -> Alert:
        """Reset a triggered/acknowledged alert so it watches for the
        condition again."""
        alert.is_triggered = False
        alert.triggered_at = None
        alert.acknowledged_at = None
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def check_and_trigger(self, user: User) -> list[Alert]:
        """Fetch live prices for all active, un-triggered alerts for this user,
        mark any whose condition is met, and send email if the alert's channel
        includes 'email'. Returns the newly-triggered alerts."""
        result = await self.db.execute(
            select(Alert).where(
                Alert.user_id == user.id,
                Alert.is_active == True,            # noqa: E712
                Alert.is_triggered == False,        # noqa: E712
                Alert.acknowledged_at == None,      # noqa: E711
                Alert.alert_type.in_([AlertType.price_above, AlertType.price_below]),
            )
        )
        active = list(result.scalars().all())
        if not active:
            return []

        # Fetch prices for all unique tickers in one pass
        tickers = list({a.ticker for a in active})
        mds = MarketDataService()

        async def _price(ticker: str) -> tuple[str, float | None]:
            try:
                data = await mds.get_quote(ticker)
                return ticker, float(data["price"])
            except Exception as exc:
                log.warning("Could not fetch price for %s: %s", ticker, exc)
                return ticker, None

        prices: dict[str, float | None] = dict(
            await asyncio.gather(*[_price(t) for t in tickers])
        )

        newly_triggered: list[Alert] = []
        now = datetime.now(UTC)

        for alert in active:
            price = prices.get(alert.ticker)
            if price is None:
                continue

            threshold = float(alert.threshold_value) if alert.threshold_value is not None else None
            if threshold is None:
                continue

            triggered = False
            if alert.alert_type == AlertType.price_above and price >= threshold:
                triggered = True
            elif alert.alert_type == AlertType.price_below and price <= threshold:
                triggered = True

            if triggered:
                alert.is_triggered = True
                alert.triggered_at = now
                newly_triggered.append(alert)

        if newly_triggered:
            await self.db.flush()

            # Send email for alerts that include "email" in their channels
            email_alerts = [
                a for a in newly_triggered
                if "email" in (a.channels or "").lower()
            ]
            if email_alerts:
                email_tasks = []
                for alert in email_alerts:
                    subject, html = alert_triggered_email(
                        ticker=alert.ticker,
                        alert_type=alert.alert_type.value,
                        threshold=float(alert.threshold_value) if alert.threshold_value else None,
                        triggered_price=prices.get(alert.ticker),
                    )
                    email_tasks.append(send_email(user.email, subject, html))
                await asyncio.gather(*email_tasks, return_exceptions=True)

            # Send Telegram messages for alerts that include "telegram" in channels.
            # Skips silently if user.telegram_chat_id is unset or bot isn't configured.
            telegram_alerts = [
                a for a in newly_triggered
                if "telegram" in (a.channels or "").lower()
            ]
            if telegram_alerts and user.telegram_chat_id:
                tg_tasks = []
                for alert in telegram_alerts:
                    msg = telegram_alert_message(
                        ticker=alert.ticker,
                        alert_type=alert.alert_type.value,
                        threshold=float(alert.threshold_value) if alert.threshold_value else None,
                        price=prices.get(alert.ticker),
                    )
                    tg_tasks.append(send_telegram(user.telegram_chat_id, msg))
                await asyncio.gather(*tg_tasks, return_exceptions=True)

        return newly_triggered
