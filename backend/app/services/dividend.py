"""
Dividend tracking.

Two halves:

  sync_*    — pull per-share dividend events from yfinance into the
              `dividends` table, keyed by (ticker, ex_date).
  compute_* — derive what a given portfolio actually received, by
              reconstructing shares-held-at-ex-date from the trade ledger.

Why derive rather than store per-portfolio amounts: back-dated trades are
routine here (an IBKR CSV import drops months of history in one go, and
trade edits are supported). A denormalised amount would silently go stale
the moment history changed underneath it. Walking the ledger is cheap at
this scale and always agrees with the trades you can see.

Entitlement rule: you receive a dividend if you held shares at the close
of the day BEFORE the ex-date — equivalently, if your position was open
strictly before ex_date. A buy ON the ex-date does not qualify.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dividend import Dividend
from app.models.portfolio import BrokerType, Portfolio, Position, Trade, TradeAction
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

# How far back to pull on a sync. Covers the user's holding history
# (earliest trade Nov 2022) with headroom; yfinance returns the full
# series anyway so this only bounds what we persist.
DEFAULT_SYNC_YEARS = 6


class DividendService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Sync from yfinance ────────────────────────────────────────────

    async def sync_ticker(self, ticker: str, *, years: int = DEFAULT_SYNC_YEARS) -> int:
        """Upsert dividend events for one ticker. Returns rows written.

        Never raises — a delisted or dividend-less ticker is a normal
        outcome, not an error, and one bad ticker must not abort a
        whole-portfolio sync.
        """
        import asyncio

        ticker = ticker.upper().strip()
        try:
            rows = await asyncio.to_thread(self._fetch_dividends_sync, ticker, years)
        except Exception as exc:
            log.warning("Dividend sync: fetch failed for %s: %s", ticker, exc)
            return 0

        if not rows:
            return 0

        # Existing ex-dates for this ticker, so we only insert what's new
        # and can refresh an amount if the provider corrected it.
        existing = {
            d.ex_date: d
            for d in (await self.db.execute(
                select(Dividend).where(Dividend.ticker == ticker)
            )).scalars().all()
        }

        written = 0
        for ex_date, amount, currency in rows:
            current = existing.get(ex_date)
            if current is None:
                self.db.add(Dividend(
                    ticker=ticker,
                    ex_date=ex_date,
                    amount_per_share=Decimal(str(amount)),
                    currency=currency,
                ))
                written += 1
            elif float(current.amount_per_share) != float(amount):
                # Providers do occasionally restate an amount (splits,
                # corrections). Keep ours in step rather than drifting.
                current.amount_per_share = Decimal(str(amount))
                written += 1

        await self.db.flush()
        return written

    @staticmethod
    def _fetch_dividends_sync(ticker: str, years: int) -> list[tuple[date, float, str]]:
        """Blocking yfinance call — run via to_thread. Returns
        [(ex_date, amount_per_share, currency), ...]."""
        import yfinance as yf

        t = yf.Ticker(ticker)
        series = t.dividends
        if series is None or len(series) == 0:
            return []

        # yfinance returns either a Series or a single-column DataFrame
        # depending on version; normalise to a Series of floats.
        if hasattr(series, "columns"):
            col = series.columns[0]
            series = series[col]

        currency = "USD"
        try:
            fi = t.fast_info
            currency = (fi.get("currency") or "USD").upper()
            if currency == "GBP":  # yfinance reports pence for LSE names
                currency = "GBP"
        except Exception:
            pass

        cutoff = date.today() - timedelta(days=365 * years)
        out: list[tuple[date, float, str]] = []
        for ts, amount in series.items():
            try:
                d = ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)[:10]).date()
            except Exception:
                continue
            if d < cutoff:
                continue
            try:
                amt = float(amount)
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            out.append((d, amt, currency))
        return out

    async def sync_for_portfolio(self, portfolio: Portfolio) -> dict:
        """Sync every ticker this portfolio has ever traded — not just
        current holdings, since past holdings still earned income that
        belongs in the history."""
        tickers = await self._all_traded_tickers(portfolio.id)
        written = 0
        for t in tickers:
            written += await self.sync_ticker(t)
        return {"tickers": len(tickers), "rows_written": written}

    # ── Shares held at a past date ────────────────────────────────────

    async def _all_traded_tickers(self, portfolio_id: int) -> list[str]:
        rows = (await self.db.execute(
            select(Trade.ticker).where(Trade.portfolio_id == portfolio_id).distinct()
        )).all()
        return sorted({r[0].upper() for r in rows})

    @staticmethod
    def _shares_at(trades: list[Trade], ticker: str, on: date) -> float:
        """Net shares of `ticker` held strictly BEFORE `on`.

        Entitlement requires holding at the close of the day before the
        ex-date, so a trade executed ON the ex-date does not count. Uses
        the same long-only convention as realised P&L: short/cover are
        ignored rather than sign-flipped.
        """
        qty = 0.0
        for t in trades:
            if t.ticker.upper() != ticker:
                continue
            traded = t.traded_at.date() if hasattr(t.traded_at, "date") else t.traded_at
            if traded >= on:
                continue
            if t.action == TradeAction.buy:
                qty += float(t.quantity)
            elif t.action == TradeAction.sell:
                qty -= float(t.quantity)
        return max(0.0, qty)

    # ── Income for a portfolio ────────────────────────────────────────

    async def compute_income(
        self, portfolio: Portfolio, base_ccy: str | None = None
    ) -> dict:
        """Full dividend picture for one portfolio.

        Returns received history (each event the portfolio was entitled
        to), YTD / trailing-12-month totals, and a forward estimate from
        current holdings × each ticker's annual rate. All money figures
        are FX-converted to `base_ccy` (defaults to the portfolio's).
        """
        base = (base_ccy or portfolio.currency or "USD").upper()

        trades = list((await self.db.execute(
            select(Trade).where(Trade.portfolio_id == portfolio.id)
            .order_by(Trade.traded_at.asc(), Trade.id.asc())
        )).scalars().all())
        if not trades:
            return self._empty_income(base)

        tickers = sorted({t.ticker.upper() for t in trades})
        divs = list((await self.db.execute(
            select(Dividend).where(Dividend.ticker.in_(tickers))
            .order_by(Dividend.ex_date.desc())
        )).scalars().all())
        if not divs:
            return self._empty_income(base)

        # One FX lookup for every currency involved, not per event.
        foreign = {d.currency.upper() for d in divs if d.currency.upper() != base}
        fx: dict[str, float] = {}
        if foreign:
            try:
                fx = await MarketDataService().get_fx_rates(list(foreign), base=base)
            except Exception:
                log.warning("Dividend income: FX fetch failed, reporting at 1:1")

        def to_base(amount: float, ccy: str) -> float:
            ccy = ccy.upper()
            if ccy == base:
                return amount
            rate = fx.get(ccy)
            return amount * rate if rate else amount

        today = datetime.now(UTC).date()
        year_start = date(today.year, 1, 1)
        ttm_start = today - timedelta(days=365)

        received: list[dict] = []
        ytd = ttm = total = 0.0
        by_ticker: dict[str, float] = defaultdict(float)

        for d in divs:
            shares = self._shares_at(trades, d.ticker.upper(), d.ex_date)
            if shares <= 0:
                continue  # didn't hold it at the time
            gross = float(d.amount_per_share) * shares
            amount = to_base(gross, d.currency)

            received.append({
                "ticker": d.ticker,
                "ex_date": d.ex_date.isoformat(),
                "pay_date": d.pay_date.isoformat() if d.pay_date else None,
                "amount_per_share": float(d.amount_per_share),
                "shares": round(shares, 6),
                "amount": round(amount, 2),
                "currency": d.currency,
                "amount_base": round(amount, 2),
            })
            total += amount
            by_ticker[d.ticker] += amount
            if d.ex_date >= year_start:
                ytd += amount
            if d.ex_date >= ttm_start:
                ttm += amount

        forward = await self._forward_estimate(portfolio, base, fx)

        return {
            "base_currency": base,
            "ytd": round(ytd, 2),
            "trailing_12m": round(ttm, 2),
            "total_all_time": round(total, 2),
            "forward_annual_estimate": round(forward["annual"], 2),
            "forward_yield_on_cost_pct": forward["yield_on_cost_pct"],
            "received": received,
            "by_ticker": [
                {"ticker": t, "amount": round(a, 2)}
                for t, a in sorted(by_ticker.items(), key=lambda kv: -kv[1])
            ],
        }

    @staticmethod
    def _empty_income(base: str) -> dict:
        return {
            "base_currency": base,
            "ytd": 0.0,
            "trailing_12m": 0.0,
            "total_all_time": 0.0,
            "forward_annual_estimate": 0.0,
            "forward_yield_on_cost_pct": None,
            "received": [],
            "by_ticker": [],
        }

    async def _forward_estimate(
        self, portfolio: Portfolio, base: str, fx: dict[str, float]
    ) -> dict:
        """Next-12-months income from current holdings × each ticker's
        published annual dividend rate. Best-effort: tickers whose rate
        can't be fetched simply contribute zero rather than blocking the
        whole figure."""
        import asyncio

        positions = list((await self.db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio.id,
                Position.quantity > 0,
            )
        )).scalars().all())
        if not positions:
            return {"annual": 0.0, "yield_on_cost_pct": None}

        def _rates(tickers: list[str]) -> dict[str, float]:
            import yfinance as yf

            out: dict[str, float] = {}
            for t in tickers:
                try:
                    rate = yf.Ticker(t).info.get("dividendRate")
                    if rate:
                        out[t] = float(rate)
                except Exception:
                    continue
            return out

        tickers = [p.ticker.upper() for p in positions]
        try:
            rates = await asyncio.to_thread(_rates, tickers)
        except Exception:
            rates = {}

        annual = 0.0
        cost_basis = 0.0
        for p in positions:
            qty = float(p.quantity)
            ccy = (p.currency or "USD").upper()
            rate = rates.get(p.ticker.upper())
            if rate:
                gross = rate * qty
                annual += gross * (1.0 if ccy == base else fx.get(ccy, 1.0))
            basis = float(p.avg_cost or 0) * qty
            cost_basis += basis * (1.0 if ccy == base else fx.get(ccy, 1.0))

        yoc = round(annual / cost_basis * 100, 2) if cost_basis > 0 else None
        return {"annual": annual, "yield_on_cost_pct": yoc}

    # ── Upcoming ──────────────────────────────────────────────────────

    async def upcoming(self, portfolio: Portfolio, *, days_ahead: int = 60) -> list[dict]:
        """Declared dividends with an ex-date in the future, sized by the
        portfolio's CURRENT holdings (entitlement isn't settled yet, so
        today's position is the right basis)."""
        positions = list((await self.db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio.id,
                Position.quantity > 0,
            )
        )).scalars().all())
        if not positions:
            return []

        held = {p.ticker.upper(): float(p.quantity) for p in positions}
        today = datetime.now(UTC).date()
        horizon = today + timedelta(days=days_ahead)

        rows = list((await self.db.execute(
            select(Dividend).where(
                Dividend.ticker.in_(list(held.keys())),
                Dividend.ex_date >= today,
                Dividend.ex_date <= horizon,
            ).order_by(Dividend.ex_date.asc())
        )).scalars().all())

        return [
            {
                "ticker": d.ticker,
                "ex_date": d.ex_date.isoformat(),
                "pay_date": d.pay_date.isoformat() if d.pay_date else None,
                "amount_per_share": float(d.amount_per_share),
                "shares": held[d.ticker.upper()],
                "amount": round(float(d.amount_per_share) * held[d.ticker.upper()], 2),
                "currency": d.currency,
            }
            for d in rows
        ]
