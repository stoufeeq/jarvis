from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.models.user import User
from app.services.market_data import MarketDataService
from app.services.options_data import OptionsDataService

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quote/{ticker}")
async def get_quote(ticker: str, _: User = Depends(get_current_user)):
    """Latest price, change, volume for a ticker."""
    return await MarketDataService().get_quote(ticker.upper())


@router.get("/quotes")
async def get_quotes(
    tickers: list[str] = Query(...),
    _: User = Depends(get_current_user),
):
    """Batch quotes for multiple tickers."""
    return await MarketDataService().get_quotes([t.upper() for t in tickers])


@router.get("/history/{ticker}")
async def get_price_history(
    ticker: str,
    period: str = Query("3mo", description="e.g. 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y"),
    interval: str = Query("1d", description="e.g. 1m, 5m, 15m, 1h, 1d, 1wk, 1mo"),
    _: User = Depends(get_current_user),
):
    """OHLCV candlestick data."""
    return await MarketDataService().get_history(ticker.upper(), period, interval)


@router.get("/search")
async def search_ticker(
    q: str = Query(..., min_length=1),
    _: User = Depends(get_current_user),
):
    """Search for tickers by name or symbol."""
    return await MarketDataService().search(q)


@router.get("/currency/{ticker}")
async def get_currency(ticker: str, _: User = Depends(get_current_user)):
    """Return the trading currency for a ticker (e.g. USD, EUR, GBP)."""
    return await MarketDataService().get_currency(ticker.upper())


@router.get("/fx")
async def get_fx_rate(
    from_currency: str = Query(..., alias="from"),
    to_currency: str = Query(..., alias="to"),
    _: User = Depends(get_current_user),
):
    """Live exchange rate between two currencies. E.g. /market/fx?from=USD&to=SGD"""
    from fastapi import HTTPException
    from_c = from_currency.upper()
    to_c = to_currency.upper()
    if from_c == to_c:
        return {"from": from_c, "to": to_c, "rate": 1.0}
    rates = await MarketDataService().get_fx_rates([from_c], base=to_c)
    rate = rates.get(from_c)
    if rate is None:
        raise HTTPException(status_code=503, detail=f"FX rate for {from_c}/{to_c} temporarily unavailable")
    return {"from": from_c, "to": to_c, "rate": rate}


@router.get("/heatmap")
async def get_heatmap(_: User = Depends(get_current_user)):
    """S&P 500 sector heatmap — batch quotes cached 2 min. Trigger manually; no background polling."""
    from app.services.heatmap import HeatmapService
    return await HeatmapService().get_sp500_heatmap()


@router.get("/earnings")
async def get_earnings_calendar(
    ticker: str = Query(None, description="Filter to a specific ticker (optional)"),
    days: int = Query(7, ge=1, le=30),
    _: User = Depends(get_current_user),
):
    """Upcoming earnings within the next N days. Powered by Finnhub."""
    from datetime import UTC, datetime, timedelta

    import httpx
    from fastapi import HTTPException

    from app.config import get_settings
    settings = get_settings()
    if not settings.finnhub_api_key:
        raise HTTPException(status_code=503, detail="Finnhub API key not configured")

    today = datetime.now(UTC).date()
    to_date = today + timedelta(days=days)
    params: dict = {
        "from": today.isoformat(),
        "to": to_date.isoformat(),
        "token": settings.finnhub_api_key,
    }
    if ticker:
        params["symbol"] = ticker.upper()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://finnhub.io/api/v1/calendar/earnings", params=params)
            resp.raise_for_status()
            return resp.json().get("earningsCalendar", [])
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Finnhub error: {exc}")


@router.get("/economic-calendar")
async def get_economic_calendar(
    days: int = Query(7, ge=1, le=30),
    _: User = Depends(get_current_user),
):
    """Upcoming high/medium-impact economic events. Powered by Finnhub."""
    from datetime import UTC, datetime, timedelta

    import httpx
    from fastapi import HTTPException

    from app.config import get_settings
    from app.signals.macro_events import HIGH_IMPACT_KEYWORDS, MEDIUM_IMPACT_KEYWORDS

    settings = get_settings()
    if not settings.finnhub_api_key:
        raise HTTPException(status_code=503, detail="Finnhub API key not configured")

    today = datetime.now(UTC).date()
    to_date = today + timedelta(days=days)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"token": settings.finnhub_api_key},
            )
            resp.raise_for_status()
            all_events = resp.json().get("economicCalendar", [])
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Finnhub error: {exc}")

    results = []
    for e in all_events:
        date_str = (e.get("time") or "")[:10]
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (today <= event_date <= to_date):
            continue
        name = (e.get("event") or "").lower()
        impact = (e.get("impact") or "").lower()
        if impact == "low" and not any(k in name for k in HIGH_IMPACT_KEYWORDS | MEDIUM_IMPACT_KEYWORDS):
            continue
        results.append(e)

    return results


@router.get("/options/{ticker}")
async def get_options_flow(ticker: str, _: User = Depends(get_current_user)):
    """Options flow summary: P/C ratio, net premium, unusual contracts.
    Uses yfinance (free, ~15-min delayed). Overlays Unusual Whales real-time
    flow if UNUSUAL_WHALES_API_KEY is configured."""
    from fastapi import HTTPException as _HTTPException
    try:
        return await OptionsDataService().get_chain_summary(ticker.upper())
    except ValueError as exc:
        raise _HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"Options data unavailable: {exc}")


@router.get("/session")
async def get_market_session(_: User = Depends(get_current_user)):
    """US equity market session state for the header badge.

    Returns ISO timestamps so the frontend can compute countdowns locally
    using the user's clock between polls."""
    from app.services.market_session import MarketSession

    s = MarketSession()
    next_open_dt = s.next_trading_day()
    today_close_dt = s.now_et.replace(hour=16, minute=0, second=0, microsecond=0) if s.is_trading_day else None
    today_pre_open_dt = s.now_et.replace(hour=9, minute=30, second=0, microsecond=0) if s.is_trading_day else None

    return {
        "state": s.state,
        "is_trading_day": s.is_trading_day,
        "is_weekend": s.is_weekend,
        "is_holiday": s.is_holiday,
        "current_et": s.now_et.isoformat(),
        "next_open": next_open_dt.isoformat(),
        "todays_close": today_close_dt.isoformat() if today_close_dt else None,
        "todays_open": today_pre_open_dt.isoformat() if today_pre_open_dt else None,
        "description": s.describe(),
    }


@router.get("/details/{ticker}")
async def get_stock_details(ticker: str, _: User = Depends(get_current_user)):
    """Comprehensive stock details — quote, valuation, growth, technicals,
    IV analytics, analyst ratings. Cached 5 min per ticker.

    Used by the Stock Browser / Explore page. Read-only, no DB writes."""
    from app.services.stock_details import StockDetailsService
    return await StockDetailsService.get_details(ticker.upper())


@router.get("/details/{ticker}/news")
async def get_stock_news(ticker: str, limit: int = Query(15, le=50), _: User = Depends(get_current_user)):
    """Recent news for a ticker. Returns DB-cached news first; if none
    found, fetches fresh from Yahoo Finance RSS on demand."""
    from datetime import UTC, datetime, timedelta
    from app.database import AsyncSessionLocal
    from app.models.news import NewsItem
    from sqlalchemy import select

    ticker = ticker.upper().strip()
    async with AsyncSessionLocal() as db:
        cutoff = datetime.now(UTC) - timedelta(days=14)
        result = await db.execute(
            select(NewsItem)
            .where(NewsItem.ticker == ticker, NewsItem.published_at >= cutoff)
            .order_by(NewsItem.published_at.desc())
            .limit(limit)
        )
        items = list(result.scalars().all())

        # If nothing in DB, fetch fresh from Yahoo RSS
        if not items:
            try:
                import httpx
                import xml.etree.ElementTree as ET

                async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                    r = await client.get(
                        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
                    )
                    if r.status_code == 200:
                        root = ET.fromstring(r.text)
                        from app.models.news import NewsItem as NI
                        for item in root.findall(".//item")[:limit]:
                            title = (item.findtext("title") or "").strip()
                            url = (item.findtext("link") or "").strip()
                            pub = (item.findtext("pubDate") or "").strip()
                            if not title:
                                continue
                            try:
                                pub_dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z") if pub else datetime.now(UTC)
                            except Exception:
                                pub_dt = datetime.now(UTC)
                            items.append(NI(
                                ticker=ticker,
                                headline=title,
                                url=url,
                                source="Yahoo Finance",
                                published_at=pub_dt,
                            ))
            except Exception:
                pass

    return [
        {
            "id": getattr(n, "id", None),
            "ticker": n.ticker,
            "headline": n.headline,
            "url": n.url,
            "source": n.source,
            "sentiment_score": float(n.sentiment_score) if getattr(n, "sentiment_score", None) is not None else None,
            "ai_signal": getattr(n, "ai_signal", None),
            "published_at": n.published_at.isoformat() if n.published_at else None,
        }
        for n in items
    ]


@router.get("/details/{ticker}/insider")
async def get_stock_insider(ticker: str, limit: int = Query(10, le=30), _: User = Depends(get_current_user)):
    """Recent insider trades for a ticker. Returns DB-cached trades first;
    if none, fetches from SEC EDGAR on demand (90-day window)."""
    from app.database import AsyncSessionLocal
    from app.models.insider_trade import InsiderTrade
    from sqlalchemy import select

    ticker = ticker.upper().strip()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(InsiderTrade)
            .where(InsiderTrade.ticker == ticker)
            .order_by(InsiderTrade.filed_at.desc())
            .limit(limit)
        )
        trades = list(result.scalars().all())

        # On-demand fetch if none in DB
        if not trades:
            try:
                from app.services.insider_fetcher import InsiderTradeFetcher
                fetcher = InsiderTradeFetcher(db)
                await fetcher.fetch_for_ticker(ticker)
                await db.commit()
                # Re-query
                result = await db.execute(
                    select(InsiderTrade)
                    .where(InsiderTrade.ticker == ticker)
                    .order_by(InsiderTrade.filed_at.desc())
                    .limit(limit)
                )
                trades = list(result.scalars().all())
            except Exception:
                pass

    return [
        {
            "id": t.id,
            "insider_name": t.insider_name,
            "insider_title": t.insider_title,
            "transaction_type": t.transaction_type.value if t.transaction_type else None,
            "shares": float(t.shares) if t.shares else None,
            "price_per_share": float(t.price_per_share) if t.price_per_share else None,
            "total_value": float(t.total_value) if t.total_value else None,
            "filed_at": t.filed_at.isoformat() if t.filed_at else None,
            "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
        }
        for t in trades
    ]
