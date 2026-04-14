"""
AI News Signal Provider.

Generates signals from recently scored NewsItems for a ticker.

Logic:
  - Fetches news items for the ticker where |sentiment_score| >= 0.5
    and published within the last 3 days.
  - If the average bullish score is strong (> 0.6), emits a bullish signal.
  - If the average bearish score is strong (< -0.6), emits a bearish signal.
  - Also surfaces the single highest-conviction article as its own signal
    when |sentiment_score| >= 0.8.
  - If no scored news exists, runs the scorer on-demand so fresh articles
    get analysed immediately (useful for manual Scan Now calls).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import NewsItem
from app.models.signal import Signal, SignalDirection, SignalType
from app.services.market_data import MarketDataService
from app.services.news_sentiment import NewsSentimentService
from app.signals.base import BaseSignalProvider

log = logging.getLogger(__name__)

SENTIMENT_THRESHOLD = 0.5   # min |score| to be considered a signal candidate
STRONG_THRESHOLD = 0.8      # min |score| for a single-article signal
AVG_THRESHOLD = 0.6         # min |avg score| to emit a consensus signal
LOOKBACK_DAYS = 3


class AINewsSignalProvider(BaseSignalProvider):
    def __init__(self, db: AsyncSession):
        self.db = db

    async def scan(self, ticker: str) -> list[Signal]:
        cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

        # Fetch scored news for this ticker
        result = await self.db.execute(
            select(NewsItem)
            .where(
                NewsItem.ticker == ticker.upper(),
                NewsItem.sentiment_score != None,  # noqa: E711
                NewsItem.published_at >= cutoff,
            )
            .order_by(NewsItem.published_at.desc())
            .limit(20)
        )
        items = list(result.scalars().all())

        # If no scored items exist yet, try fetching + scoring on-demand
        if not items:
            # First: check if there are any unscored items in DB for this ticker
            unscored_result = await self.db.execute(
                select(NewsItem)
                .where(
                    NewsItem.ticker == ticker.upper(),
                    NewsItem.published_at >= cutoff,
                )
                .limit(1)
            )
            has_unscored = unscored_result.scalar_one_or_none() is not None

            # No items at all — fetch from yfinance on-demand
            if not has_unscored:
                try:
                    await self._fetch_yfinance_news(ticker, cutoff)
                except Exception as exc:
                    log.warning("On-demand yfinance fetch failed for %s: %s", ticker, exc)

            # Now score whatever we have
            try:
                scorer = NewsSentimentService()
                scored = await scorer.score_unprocessed(self.db, limit=30)
                if scored:
                    await self.db.flush()
                    # Re-query after scoring
                    result2 = await self.db.execute(
                        select(NewsItem)
                        .where(
                            NewsItem.ticker == ticker.upper(),
                            NewsItem.sentiment_score != None,  # noqa: E711
                            NewsItem.published_at >= cutoff,
                        )
                        .order_by(NewsItem.published_at.desc())
                        .limit(20)
                    )
                    items = list(result2.scalars().all())
            except Exception as exc:
                log.warning("On-demand sentiment scoring failed for %s: %s", ticker, exc)

        if not items:
            return []

        # Get current price for entry
        try:
            mds = MarketDataService()
            quote = await mds.get_quote(ticker)
            price = float(quote["price"])
        except Exception:
            price = None

        signals: list[Signal] = []

        def make(direction: SignalDirection, strength: int, indicators: str, rationale: str) -> Signal:
            return Signal(
                ticker=ticker.upper(),
                signal_type=SignalType.ai_news,
                direction=direction,
                strength=strength,
                entry_price=price,
                stop_loss=None,
                take_profit=None,
                indicators=indicators,
                rationale=rationale,
                timeframe="1-3d",
                expires_at=datetime.now(UTC) + timedelta(days=3),
            )

        # ── Consensus signal from multiple articles ───────────────────────
        candidates = [i for i in items if abs(float(i.sentiment_score)) >= SENTIMENT_THRESHOLD]
        if len(candidates) >= 2:
            scores = [float(i.sentiment_score) for i in candidates]
            avg = sum(scores) / len(scores)

            if avg >= AVG_THRESHOLD:
                signals.append(make(
                    SignalDirection.bullish,
                    strength=min(5, 2 + len(candidates)),
                    indicators=f"NEWS_CONSENSUS_BULLISH:{len(candidates)}_ARTICLES:avg={avg:.2f}",
                    rationale=(
                        f"{len(candidates)} recent news articles are bullish on {ticker} "
                        f"(avg sentiment {avg:+.2f}). "
                        + (candidates[0].ai_signal or "")
                    )[:400],
                ))
            elif avg <= -AVG_THRESHOLD:
                signals.append(make(
                    SignalDirection.bearish,
                    strength=min(5, 2 + len(candidates)),
                    indicators=f"NEWS_CONSENSUS_BEARISH:{len(candidates)}_ARTICLES:avg={avg:.2f}",
                    rationale=(
                        f"{len(candidates)} recent news articles are bearish on {ticker} "
                        f"(avg sentiment {avg:+.2f}). "
                        + (candidates[0].ai_signal or "")
                    )[:400],
                ))

        # ── Single high-conviction article ────────────────────────────────
        top = max(items, key=lambda i: abs(float(i.sentiment_score or 0)))
        top_score = float(top.sentiment_score or 0)
        if abs(top_score) >= STRONG_THRESHOLD and len(candidates) < 2:
            direction = SignalDirection.bullish if top_score > 0 else SignalDirection.bearish
            signals.append(make(
                direction,
                strength=4,
                indicators=f"NEWS_HIGH_CONVICTION:{top_score:+.2f}",
                rationale=(
                    f'[{top.source or "News"}] "{top.headline[:120]}" '
                    + (f"— {top.ai_signal}" if top.ai_signal else "")
                )[:400],
            ))

        return signals

    async def _fetch_yfinance_news(self, ticker: str, cutoff: datetime) -> None:
        """Fetch news via Yahoo Finance RSS and persist to DB (no API key required)."""
        import xml.etree.ElementTree as ET
        import httpx
        from email.utils import parsedate_to_datetime
        from sqlalchemy import select as sa_select

        headers = {"User-Agent": "Mozilla/5.0 (compatible; Jarvis/1.0)"}
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                resp = await client.get(
                    "https://feeds.finance.yahoo.com/rss/2.0/headline",
                    params={"s": ticker, "region": "US", "lang": "en-US"},
                )
            if resp.status_code != 200:
                return
            root = ET.fromstring(resp.text)
        except Exception as exc:
            log.warning("Yahoo RSS fetch failed for %s: %s", ticker, exc)
            return

        for item in root.findall(".//item"):
            title = item.findtext("title")
            if not title:
                continue
            url = item.findtext("link") or item.findtext("guid") or f"yf-rss:{ticker}:{title[:40]}"
            pub_raw = item.findtext("pubDate")
            description = item.findtext("description")

            try:
                published = parsedate_to_datetime(pub_raw) if pub_raw else datetime.now(UTC)
            except Exception:
                published = datetime.now(UTC)

            if published < cutoff:
                continue

            # Dedup by URL
            existing = await self.db.execute(
                sa_select(NewsItem.id).where(NewsItem.url == url).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue

            self.db.add(NewsItem(
                ticker=ticker.upper(),
                headline=title[:1000],
                summary=description,
                url=url,
                source="Yahoo Finance",
                published_at=published,
            ))

        await self.db.flush()
