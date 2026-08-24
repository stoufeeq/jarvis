"""
News sentiment scoring service.

Finds unprocessed NewsItem rows, batches them, and asks an LLM to:
  - Score sentiment from -1.0 (very bearish) to +1.0 (very bullish)
  - Assign a ticker if one can be confidently extracted from the headline
  - Write a one-line signal summary

Returns the number of items processed.

Provider is resolved via `get_llm_for_task(db, "news")` — admin can
override the model from the Settings UI (persisted in system_settings)
or the default from `.env` (`NEWS_MODEL`) is used. The scorer wraps
any provider error in a warning + returns 0 so a bad key or upstream
outage doesn't crash the whole Celery task.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import NewsItem
from app.services.llm import LLMClient, LLMError, get_llm_for_task

log = logging.getLogger(__name__)

BATCH_SIZE = 15  # articles per LLM call

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
        # Model + client are resolved per call inside score_unprocessed so
        # admin changes take effect immediately. No instance state needed.
        pass

    async def score_unprocessed(self, db: AsyncSession, limit: int = 60) -> int:
        """Score up to `limit` unprocessed NewsItems. Returns count updated.

        Returns 0 (and logs a warning) if the configured LLM provider is
        misconfigured or the upstream call fails — never raises. Callers
        can rely on 'no exceptions from here' semantics for their
        higher-level pipelines."""
        result = await db.execute(
            select(NewsItem)
            .where(NewsItem.processed_at == None)  # noqa: E711
            .order_by(NewsItem.published_at.desc())
            .limit(limit)
        )
        items: list[NewsItem] = list(result.scalars().all())
        if not items:
            return 0

        try:
            client, model = await get_llm_for_task(db, "news")
        except LLMError as exc:
            log.warning("News sentiment: LLM resolution failed — %s", exc)
            return 0

        total = 0
        # Batches with a small delay to stay within free-tier RPM limits
        # (relevant for Gemini free / OpenRouter free tiers alike).
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i : i + BATCH_SIZE]
            updated = await self._score_batch(batch, client, model)
            total += updated
            if updated and i + BATCH_SIZE < len(items):
                await asyncio.sleep(4)

        await db.flush()
        return total

    async def _score_batch(
        self, items: list[NewsItem], client: LLMClient, model: str
    ) -> int:
        articles = [
            {"id": item.id, "headline": item.headline, "summary": item.summary or ""}
            for item in items
        ]
        prompt = SCORING_PROMPT.format(articles_json=json.dumps(articles, ensure_ascii=False))

        try:
            raw = await client.complete(prompt, model=model, temperature=0.2)
        except LLMError as exc:
            log.warning("Sentiment scoring failed (%s / %s): %s", client.provider, model, exc)
            return 0

        # Strip markdown code fences if the model adds them anyway.
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            scored: list[dict] = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("Sentiment scoring: JSON parse failed — %s (raw=%s)", exc, raw[:200])
            return 0

        id_map = {item.id: item for item in items}
        now = datetime.now(UTC)
        updated = 0

        for entry in scored:
            # LLMs occasionally return the id as a stringified int
            # (`"12"`) — coerce defensively rather than skipping.
            item_id_raw = entry.get("id")
            try:
                item_id = int(item_id_raw) if item_id_raw is not None else None
            except (TypeError, ValueError):
                continue
            if item_id is None:
                continue
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
