"""
Cross-Impact Signal Provider — powered by Gemini.

Looks at recently scored news items for a ticker and asks Gemini whether
that news implies a knock-on impact on *other* correlated tickers (e.g.
strong TSMC earnings → bullish for NVDA, AMD, AMAT).

Signals are attached to the *affected* ticker (not the source), with the
source event described in the rationale.

Rules:
  - Only runs if there are scored news items in the last 3 days for the
    scanned ticker with |sentiment_score| >= 0.6.
  - Makes a single Gemini call per scan; parses the JSON response.
  - Skips gracefully on any Gemini error.
  - Expires in 2 days.

Requires GEMINI_API_KEY in settings.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.news import NewsItem
from app.models.signal import Signal, SignalDirection, SignalType
from app.signals.base import BaseSignalProvider

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 3
SENTIMENT_THRESHOLD = 0.6
MAX_CORRELATED = 5  # max correlated tickers to generate signals for


class CrossImpactSignalProvider(BaseSignalProvider):
    """Uses Gemini to identify stocks likely impacted by news about the scanned ticker."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def scan(self, ticker: str) -> list[Signal]:
        settings = get_settings()
        if not settings.gemini_api_key:
            return []

        # Fetch recent high-conviction scored news for this ticker
        cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
        result = await self.db.execute(
            select(NewsItem)
            .where(
                NewsItem.ticker == ticker,
                NewsItem.processed_at != None,  # noqa: E711
                NewsItem.published_at >= cutoff,
            )
            .order_by(NewsItem.published_at.desc())
            .limit(5)
        )
        news_items = list(result.scalars().all())

        # Filter to items with strong sentiment
        strong_news = [
            n for n in news_items
            if n.sentiment_score is not None and abs(float(n.sentiment_score)) >= SENTIMENT_THRESHOLD
        ]

        if not strong_news:
            return []

        # Build context for Gemini
        news_summaries = "\n".join(
            f"- [{'+' if float(n.sentiment_score) > 0 else ''}{float(n.sentiment_score):.2f}] {n.headline}"
            for n in strong_news
        )

        prompt = f"""You are a financial analyst. The following recent news items about {ticker} have strong sentiment scores:

{news_summaries}

Based on this news, identify up to {MAX_CORRELATED} other publicly traded S&P 500 stocks (NOT {ticker} itself) that are likely to be meaningfully impacted due to supply chain relationships, sector correlation, competition, or shared macro exposure.

Respond with ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "impacts": [
    {{
      "ticker": "AAPL",
      "direction": "bullish",
      "strength": 3,
      "reason": "one sentence explanation"
    }}
  ]
}}

Direction must be "bullish", "bearish", or "neutral". Strength 1-5. Return empty impacts array if no meaningful cross-impact exists."""

        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel(settings.gemini_model)
            response = model.generate_content(prompt)
            raw = response.text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            data = json.loads(raw)
            impacts = data.get("impacts", [])
        except Exception as exc:
            logger.warning("CrossImpactSignalProvider Gemini call failed for %s: %s", ticker, exc)
            return []

        signals: list[Signal] = []
        expires = datetime.now(UTC) + timedelta(days=2)
        source_headline = strong_news[0].headline[:100]

        for item in impacts[:MAX_CORRELATED]:
            affected_ticker = str(item.get("ticker", "")).upper().strip()
            direction_str = str(item.get("direction", "neutral")).lower()
            strength = int(item.get("strength", 2))
            reason = str(item.get("reason", ""))

            if not affected_ticker or affected_ticker == ticker:
                continue

            try:
                direction = SignalDirection(direction_str)
            except ValueError:
                direction = SignalDirection.neutral

            strength = max(1, min(5, strength))

            rationale = f"Cross-impact from {ticker} news: {reason} | Source: \"{source_headline}\""

            signals.append(Signal(
                ticker=affected_ticker,
                signal_type=SignalType.cross_impact,
                direction=direction,
                strength=strength,
                rationale=rationale,
                indicators=f"SOURCE_TICKER={ticker},SENTIMENT={float(strong_news[0].sentiment_score):.2f}",
                timeframe="1-3d",
                expires_at=expires,
            ))

        return signals
