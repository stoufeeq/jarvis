"""
Signal aggregation — groups individual rule-level signals by
(ticker, signal_type) and computes a net direction + confidence using
sum-of-strengths math.

This is Phase 1 of the planned aggregation upgrade. See the
"Signal Aggregation Roadmap" memory for IC-weighting and ML phases planned
once the signal_outcomes table has accumulated more data.

Math:
  score = Σ (strength × direction_sign), where bullish=+1, bearish=-1, neutral=0
  net_direction = sign(score)
  net_strength  = round(|score| / max_possible_score × 5), capped at 5
  confidence    = strong (all agree) / moderate (≥70% agree) / mixed (otherwise)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal, SignalDirection, SignalType


def _direction_sign(direction: SignalDirection) -> int:
    return {
        SignalDirection.bullish: 1,
        SignalDirection.bearish: -1,
        SignalDirection.neutral: 0,
    }[direction]


def _aggregate_one(rules: list[Signal]) -> dict[str, Any]:
    """Aggregate a list of rule-level signals (same ticker + signal_type)."""
    score = sum(_direction_sign(r.direction) * (r.strength or 0) for r in rules)
    bullish_count = sum(1 for r in rules if r.direction == SignalDirection.bullish)
    bearish_count = sum(1 for r in rules if r.direction == SignalDirection.bearish)
    neutral_count = sum(1 for r in rules if r.direction == SignalDirection.neutral)
    total = len(rules)

    if score > 0:
        net_direction = "bullish"
    elif score < 0:
        net_direction = "bearish"
    else:
        net_direction = "neutral"

    # Net strength: normalised to 1–5 scale based on max possible score.
    # Max possible = total × 5 (everyone agrees at strength 5).
    max_score = total * 5
    if max_score > 0 and abs(score) > 0:
        net_strength = max(1, min(5, round(abs(score) / max_score * 5)))
    else:
        net_strength = 0

    # Confidence: how unanimous is the agreement?
    if total == 0:
        confidence = "mixed"
    elif net_direction == "bullish":
        agree_pct = bullish_count / total
        confidence = "strong" if agree_pct == 1.0 else ("moderate" if agree_pct >= 0.7 else "mixed")
    elif net_direction == "bearish":
        agree_pct = bearish_count / total
        confidence = "strong" if agree_pct == 1.0 else ("moderate" if agree_pct >= 0.7 else "mixed")
    else:
        confidence = "mixed"

    return {
        "net_direction": net_direction,
        "net_strength": net_strength,
        "score": score,
        "confidence": confidence,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "rule_count": total,
        "rules": [
            {
                "id": r.id,
                "direction": r.direction.value,
                "strength": r.strength,
                "rationale": r.rationale,
                "indicators": r.indicators,
                "timeframe": r.timeframe,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in rules
        ],
    }


class SignalAggregator:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def aggregated_by_ticker_category(
        self,
        ticker: str | None = None,
        signal_type: SignalType | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return one aggregated entry per (ticker, signal_type), filtered.

        Each entry contains the net direction/strength/confidence plus the
        underlying rule-level signals.
        """
        now = datetime.now(UTC)

        query = select(Signal).where(
            (Signal.expires_at.is_(None)) | (Signal.expires_at > now)
        )
        if ticker:
            query = query.where(Signal.ticker == ticker.upper())
        if signal_type:
            query = query.where(Signal.signal_type == signal_type)
        query = query.order_by(Signal.ticker.asc(), Signal.signal_type.asc())

        result = await self.db.execute(query)
        signals = list(result.scalars().all())

        # Group by (ticker, signal_type)
        grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
        for s in signals:
            grouped[(s.ticker, s.signal_type.value)].append(s)

        out: list[dict[str, Any]] = []
        for (tkr, stype), rules in grouped.items():
            agg = _aggregate_one(rules)
            out.append({
                "ticker": tkr,
                "signal_type": stype,
                **agg,
            })

        # Sort: highest |score| first so most decisive signals lead, then ticker
        out.sort(key=lambda x: (-abs(x["score"]), x["ticker"], x["signal_type"]))
        return out[:limit]

    async def aggregated_by_ticker(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return one entry per ticker with per-category breakdown.

        Useful for the "ticker scorecard" view: shows AAPL with technical/
        fundamental/insider/options as sub-rows.
        """
        category_aggs = await self.aggregated_by_ticker_category(limit=10_000)

        # Group categories under each ticker
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for agg in category_aggs:
            by_ticker[agg["ticker"]].append(agg)

        out: list[dict[str, Any]] = []
        for tkr, categories in by_ticker.items():
            # Overall ticker score = sum of all category scores
            overall_score = sum(c["score"] for c in categories)
            total_rules = sum(c["rule_count"] for c in categories)
            total_bullish = sum(c["bullish_count"] for c in categories)
            total_bearish = sum(c["bearish_count"] for c in categories)

            if overall_score > 0:
                overall_direction = "bullish"
            elif overall_score < 0:
                overall_direction = "bearish"
            else:
                overall_direction = "neutral"

            out.append({
                "ticker": tkr,
                "overall_direction": overall_direction,
                "overall_score": overall_score,
                "total_rules": total_rules,
                "total_bullish": total_bullish,
                "total_bearish": total_bearish,
                "category_count": len(categories),
                "categories": categories,
            })

        out.sort(key=lambda x: (-abs(x["overall_score"]), x["ticker"]))
        return out[:limit]
