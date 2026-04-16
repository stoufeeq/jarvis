import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insider_trade import InsiderTrade
from app.models.signal import Signal, SignalDirection, SignalType
from app.signals.ai_news import AINewsSignalProvider
from app.signals.cross_impact import CrossImpactSignalProvider
from app.signals.earnings import EarningsSignalProvider
from app.signals.fundamental import FundamentalSignalProvider
from app.signals.insider import InsiderSignalProvider
from app.signals.macro_events import EconomicCalendarProvider
from app.signals.options_flow import OptionsFlowSignalProvider
from app.signals.technical import TechnicalSignalProvider

log = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._base_providers = [
            TechnicalSignalProvider(),
            InsiderSignalProvider(db),
            OptionsFlowSignalProvider(),
            FundamentalSignalProvider(),
            EarningsSignalProvider(),
            EconomicCalendarProvider(),
        ]
        self._ai_providers = [
            AINewsSignalProvider(db),
            CrossImpactSignalProvider(db),
        ]

    async def scan_ticker(self, ticker: str, include_ai: bool = False) -> list[Signal]:
        """Run signal providers for a ticker.

        include_ai=False (default): runs technical, insider, options, fundamental,
            earnings, macro — no Gemini calls.
        include_ai=True: also runs AINews and CrossImpact (each makes a Gemini call).

        Existing signals for this ticker are deleted first so the result
        always reflects the latest scan rather than accumulating stale rows.
        """
        await self.db.execute(delete(Signal).where(Signal.ticker == ticker))

        providers = self._base_providers + (self._ai_providers if include_ai else [])

        all_signals: list[Signal] = []
        for provider in providers:
            try:
                signals = await provider.scan(ticker)
                for s in signals:
                    self.db.add(s)
                    all_signals.append(s)
            except Exception as exc:
                log.warning("Signal provider %s failed for %s: %s", provider.name, ticker, exc)

        if all_signals:
            await self.db.flush()
        return all_signals

    async def get_signals(
        self,
        ticker: str | None,
        signal_type: SignalType | None,
        direction: SignalDirection | None,
        limit: int,
    ) -> list[Signal]:
        now = datetime.now(UTC)
        query = (
            select(Signal)
            .where(
                (Signal.expires_at == None) | (Signal.expires_at > now)  # noqa: E711
            )
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
        if ticker:
            query = query.where(Signal.ticker == ticker.upper())
        if signal_type:
            query = query.where(Signal.signal_type == signal_type)
        if direction:
            query = query.where(Signal.direction == direction)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_insider_trades(
        self, ticker: str | None, limit: int
    ) -> list[InsiderTrade]:
        query = (
            select(InsiderTrade)
            .order_by(InsiderTrade.filed_at.desc())
            .limit(limit)
        )
        if ticker:
            query = query.where(InsiderTrade.ticker == ticker.upper())
        result = await self.db.execute(query)
        return list(result.scalars().all())
