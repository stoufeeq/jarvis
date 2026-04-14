"""
Insider Trading Signal Provider.

Generates signals from recent SEC Form 4 filings stored in insider_trades:

  - Cluster buy   : 2+ insiders bought in last 30 days         → bullish strength 4
  - Large buy     : single purchase ≥ $100k                     → bullish strength 3
  - Exec buy      : CEO/CFO/President bought anything           → bullish strength 3
  - Cluster sell  : 3+ insiders sold in last 30 days            → bearish strength 3
  - Large exec sell: CEO/CFO selling > 50% of their position    → bearish strength 2
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insider_trade import InsiderTrade, InsiderTransactionType
from app.models.signal import Signal, SignalDirection, SignalType
from app.services.insider_fetcher import InsiderTradeFetcher
from app.services.market_data import MarketDataService
from app.signals.base import BaseSignalProvider

log = logging.getLogger(__name__)

EXEC_TITLES = {"ceo", "cfo", "president", "chief executive", "chief financial", "coo", "chairman"}


def _is_exec(title: str | None) -> bool:
    if not title:
        return False
    return any(t in title.lower() for t in EXEC_TITLES)


class InsiderSignalProvider(BaseSignalProvider):
    def __init__(self, db: AsyncSession):
        self.db = db

    async def scan(self, ticker: str) -> list[Signal]:
        # Ensure we have fresh data (fetch last 90 days if table is empty for this ticker)
        fetcher = InsiderTradeFetcher()
        try:
            count = await fetcher.fetch_for_ticker(ticker, self.db, days=90)
            if count:
                await self.db.flush()
        except Exception as exc:
            log.warning("Insider fetch for %s failed: %s", ticker, exc)
        finally:
            await fetcher.close()

        # Query recent trades (last 30 days)
        cutoff = datetime.now(UTC) - timedelta(days=30)
        result = await self.db.execute(
            select(InsiderTrade)
            .where(
                InsiderTrade.ticker == ticker.upper(),
                InsiderTrade.filed_at >= cutoff,
                InsiderTrade.transaction_type.in_([
                    InsiderTransactionType.buy,
                    InsiderTransactionType.sell,
                ]),
            )
            .order_by(InsiderTrade.filed_at.desc())
        )
        trades = list(result.scalars().all())

        if not trades:
            return []

        # Get current price for entry/sl/tp
        try:
            mds = MarketDataService()
            quote = await mds.get_quote(ticker)
            price = float(quote["price"])
        except Exception:
            price = None

        signals: list[Signal] = []
        buys = [t for t in trades if t.transaction_type == InsiderTransactionType.buy]
        sells = [t for t in trades if t.transaction_type == InsiderTransactionType.sell]

        def make(direction: SignalDirection, strength: int, indicators: str, rationale: str) -> Signal:
            return Signal(
                ticker=ticker.upper(),
                signal_type=SignalType.insider,
                direction=direction,
                strength=strength,
                entry_price=price,
                stop_loss=None,
                take_profit=None,
                indicators=indicators,
                rationale=rationale,
                timeframe="swing",
                expires_at=datetime.now(UTC) + timedelta(days=14),
            )

        # ── Cluster buy ──────────────────────────────────────────────────
        unique_buyers = {t.insider_name for t in buys}
        if len(unique_buyers) >= 2:
            names = ", ".join(list(unique_buyers)[:3])
            signals.append(make(
                SignalDirection.bullish, 4,
                f"CLUSTER_BUY:{len(unique_buyers)}_INSIDERS",
                f"{len(unique_buyers)} insiders bought {ticker} in the last 30 days "
                f"({names}{'…' if len(unique_buyers) > 3 else ''}). "
                "Cluster buying is a strong bullish signal.",
            ))

        # ── Large single buy ─────────────────────────────────────────────
        large_buys = [t for t in buys if t.total_value and float(t.total_value) >= 100_000]
        if large_buys and len(unique_buyers) < 2:  # avoid double-counting with cluster
            top = max(large_buys, key=lambda t: float(t.total_value or 0))
            signals.append(make(
                SignalDirection.bullish, 3,
                f"LARGE_BUY:${float(top.total_value or 0):,.0f}",
                f"{top.insider_name} ({top.insider_title or 'Insider'}) purchased "
                f"${float(top.total_value or 0):,.0f} worth of {ticker}.",
            ))

        # ── Executive buy ────────────────────────────────────────────────
        exec_buys = [t for t in buys if _is_exec(t.insider_title)]
        if exec_buys and len(unique_buyers) < 2:  # avoid triple-counting
            top = exec_buys[0]
            signals.append(make(
                SignalDirection.bullish, 3,
                f"EXEC_BUY:{top.insider_title}",
                f"{top.insider_name} ({top.insider_title}) bought {ticker}. "
                "C-suite open-market purchases are a strong confidence signal.",
            ))

        # ── Cluster sell: 2+ unique insiders selling ─────────────────────
        unique_sellers = {t.insider_name for t in sells}
        if len(unique_sellers) >= 2:
            names = ", ".join(list(unique_sellers)[:3])
            signals.append(make(
                SignalDirection.bearish, 3,
                f"CLUSTER_SELL:{len(unique_sellers)}_INSIDERS",
                f"{len(unique_sellers)} insiders sold {ticker} in the last 30 days "
                f"({names}{'…' if len(unique_sellers) > 3 else ''}). "
                "Multiple insiders selling may indicate reduced confidence.",
            ))

        # ── High-value exec sell: any C-suite sale ≥ $500k ───────────────
        exec_sells = [t for t in sells if _is_exec(t.insider_title)]
        large_exec_sells = [t for t in exec_sells if t.total_value and float(t.total_value) >= 500_000]
        if large_exec_sells and len(unique_sellers) < 2:  # avoid double-counting
            top = max(large_exec_sells, key=lambda t: float(t.total_value or 0))
            total = float(top.total_value or 0)
            signals.append(make(
                SignalDirection.bearish, 2,
                f"EXEC_SELL:${total:,.0f}",
                f"{top.insider_name} ({top.insider_title}) sold ${total:,.0f} "
                f"worth of {ticker}. Note: may be a scheduled 10b5-1 plan sale.",
            ))

        return signals
