"""
News sentiment scoring service.

Finds unprocessed NewsItem rows, batches them, and calls Gemini to:
  - Score sentiment from -1.0 (very bearish) to +1.0 (very bullish)
  - Assign a ticker if one can be confidently extracted from the headline
  - Write a one-line signal summary

Returns the number of items processed.
"""

import json
import logging
from datetime import UTC, datetime

import google.generativeai as genai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.news import NewsItem

log = logging.getLogger(__name__)

BATCH_SIZE = 15  # articles per Gemini call

SCORING_PROMPT = """\
You are a financial news analyst. Score each news headline for its market sentiment.

Return a JSON array — one object per article — with EXACTLY these fields:
  "id"              : the integer id provided
  "ticker"          : stock ticker symbol if the headline clearly refers to a single company
                      (e.g. "AAPL", "TSLA"). Use null if it's general market/macro news.
  "sentiment_score" : float from -1.0 (very bearish) to +1.0 (very bullish). 0.0 = neutral.
  "signal"          : one concise sentence (max 120 chars) explaining the trading implication.
                      e.g. "Fed rate cut boosts growth stocks — bullish near-term catalyst."

Rules:
- Be decisive. Vague headlines score near 0.0.
- Earnings beats, buybacks, strong guidance → positive scores.
- Earnings misses, layoffs, regulatory action, debt concerns → negative scores.
- Macro events (rate cuts = bullish, rate hikes = bearish for equities).
- Only assign a ticker when the headline unambiguously names a single company.
- Return raw JSON only — no markdown, no code fences.

Articles:
{articles_json}
"""


class NewsSentimentService:
    def __init__(self):
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name=settings.gemini_model,
        )

    async def score_unprocessed(self, db: AsyncSession, limit: int = 60) -> int:
        """Score up to `limit` unprocessed NewsItems. Returns count updated."""
        result = await db.execute(
            select(NewsItem)
            .where(NewsItem.processed_at == None)  # noqa: E711
            .order_by(NewsItem.published_at.desc())
            .limit(limit)
        )
        items: list[NewsItem] = list(result.scalars().all())
        if not items:
            return 0

        import asyncio

        total = 0
        # Process in batches with a small delay to stay within Gemini free-tier RPM limits
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i : i + BATCH_SIZE]
            updated = await self._score_batch(batch)
            total += updated
            if updated and i + BATCH_SIZE < len(items):
                await asyncio.sleep(4)  # ~15 req/min on free tier

        await db.flush()
        return total

    async def _score_batch(self, items: list[NewsItem]) -> int:
        articles = [
            {"id": item.id, "headline": item.headline, "summary": item.summary or ""}
            for item in items
        ]
        prompt = SCORING_PROMPT.format(articles_json=json.dumps(articles, ensure_ascii=False))

        try:
            response = await self._model.generate_content_async(prompt)
            raw = response.text.strip()
            # Strip markdown code fences if Gemini adds them anyway
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            scored: list[dict] = json.loads(raw)
        except Exception as exc:
            log.warning("Gemini sentiment scoring failed: %s", exc)
            return 0

        id_map = {item.id: item for item in items}
        now = datetime.now(UTC)
        updated = 0

        for entry in scored:
            item_id = entry.get("id")
            news_item = id_map.get(item_id)
            if not news_item:
                continue

            score = entry.get("sentiment_score")
            if isinstance(score, (int, float)):
                news_item.sentiment_score = max(-1.0, min(1.0, float(score)))

            signal = entry.get("signal")
            if signal:
                news_item.ai_signal = str(signal)[:500]

            # Only set ticker if the item doesn't already have one
            if not news_item.ticker:
                ticker = entry.get("ticker")
                if ticker and isinstance(ticker, str) and len(ticker) <= 10:
                    news_item.ticker = ticker.upper()

            news_item.processed_at = now
            updated += 1

        return updated
