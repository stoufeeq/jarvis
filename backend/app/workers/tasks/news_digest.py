"""
Celery tasks: news fetching + sentiment scoring.

Fetches two types of news:
  1. General business headlines (top-headlines)
  2. Ticker-specific news for every watchlist ticker (everything endpoint)

Then scores all unprocessed items via Gemini sentiment analysis.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.news import NewsItem
from app.models.watchlist import WatchlistItem
from app.services.news_sentiment import NewsSentimentService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.workers.tasks.news_digest.fetch_and_process_news", bind=True)
def fetch_and_process_news(self):
    asyncio.run(_fetch_and_process())


async def _fetch_and_process():
    if not settings.news_api_key:
        log.info("NEWS_API_KEY not set — using yfinance as news source")
        await _fetch_ticker_news_yfinance()
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. General business headlines
            await _fetch_headlines(client)
            # 2. Ticker-specific news for watchlist
            await _fetch_ticker_news(client)

    # Score everything unprocessed with Gemini
    await _score_pending()


async def _fetch_headlines(client: httpx.AsyncClient) -> None:
    """Fetch top business headlines and store new ones."""
    try:
        resp = await client.get(
            "https://newsapi.org/v2/top-headlines",
            params={
                "category": "business",
                "language": "en",
                "pageSize": 30,
                "apiKey": settings.news_api_key,
            },
        )
        if resp.status_code != 200:
            return
        articles = resp.json().get("articles", [])
    except Exception as exc:
        log.warning("Failed to fetch headlines: %s", exc)
        return

    async with AsyncSessionLocal() as db:
        await _store_articles(db, articles, ticker=None)
        await db.commit()


async def _fetch_ticker_news(client: httpx.AsyncClient) -> None:
    """Fetch recent news for each watchlist ticker."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WatchlistItem.ticker).distinct())
        tickers = [row[0] for row in result.all()]

    if not tickers:
        return

    for ticker in tickers:
        try:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": ticker,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": settings.news_api_key,
                },
            )
            if resp.status_code != 200:
                continue
            articles = resp.json().get("articles", [])
        except Exception as exc:
            log.warning("Failed to fetch news for %s: %s", ticker, exc)
            continue

        async with AsyncSessionLocal() as db:
            await _store_articles(db, articles, ticker=ticker)
            await db.commit()

        # Small delay to respect NewsAPI rate limits
        await asyncio.sleep(0.5)


async def _store_articles(db, articles: list[dict], ticker: str | None) -> None:
    """Store new articles, deduplicating by URL."""
    # Collect URLs already in DB for deduplication
    from sqlalchemy import select as sa_select
    urls = [a.get("url") for a in articles if a.get("url")]
    if not urls:
        return

    existing = set(
        row[0]
        for row in (
            await db.execute(
                sa_select(NewsItem.url).where(NewsItem.url.in_(urls))
            )
        ).all()
    )

    for article in articles:
        url = article.get("url")
        if not article.get("title") or url in existing:
            continue
        try:
            published = datetime.fromisoformat(
                article["publishedAt"].replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            published = datetime.now(UTC)

        db.add(NewsItem(
            ticker=ticker,
            headline=article["title"][:1000],
            summary=article.get("description"),
            url=url,
            source=article.get("source", {}).get("name"),
            published_at=published,
        ))
        if url:
            existing.add(url)


async def _fetch_ticker_news_yfinance() -> None:
    """Fetch recent news for each watchlist ticker via Yahoo Finance RSS (no API key needed)."""
    import xml.etree.ElementTree as ET

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WatchlistItem.ticker).distinct())
        tickers = [row[0] for row in result.all()]

    if not tickers:
        return

    headers = {"User-Agent": "Mozilla/5.0 (compatible; Jarvis/1.0)"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        for ticker in tickers:
            try:
                resp = await client.get(
                    "https://feeds.finance.yahoo.com/rss/2.0/headline",
                    params={"s": ticker, "region": "US", "lang": "en-US"},
                )
                if resp.status_code != 200:
                    log.warning("Yahoo RSS returned %d for %s", resp.status_code, ticker)
                    continue
                root = ET.fromstring(resp.text)
            except Exception as exc:
                log.warning("Yahoo RSS fetch failed for %s: %s", ticker, exc)
                continue

            articles = []
            for item in root.findall(".//item"):
                title = item.findtext("title")
                if not title:
                    continue
                url = item.findtext("link") or item.findtext("guid")
                pub_raw = item.findtext("pubDate")
                description = item.findtext("description")

                try:
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(pub_raw).isoformat() if pub_raw else datetime.now(UTC).isoformat()
                except Exception:
                    published = datetime.now(UTC).isoformat()

                articles.append({
                    "title": title,
                    "description": description,
                    "url": url or f"yf-rss:{ticker}:{title[:40]}",
                    "source": {"name": "Yahoo Finance"},
                    "publishedAt": published,
                })

            if articles:
                async with AsyncSessionLocal() as db:
                    await _store_articles(db, articles, ticker=ticker)
                    await db.commit()
                log.info("Stored %d articles for %s from Yahoo RSS", len(articles), ticker)

            await asyncio.sleep(0.5)


async def _score_pending() -> None:
    """Run Gemini sentiment scoring on unprocessed news items."""
    if not settings.gemini_api_key:
        return
    try:
        scorer = NewsSentimentService()
        async with AsyncSessionLocal() as db:
            count = await scorer.score_unprocessed(db)
            await db.commit()
            if count:
                log.info("Scored %d news items", count)
    except Exception as exc:
        log.warning("News sentiment scoring failed: %s", exc)
