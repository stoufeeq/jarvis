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
