"""
AI Advisor — powered by Google Gemini via the google-generativeai SDK.
"""

import google.generativeai as genai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.news import NewsItem

settings = get_settings()

SYSTEM_PROMPT = """You are Jarvis — a knowledgeable, no-nonsense financial advisor
talking to a single user (the one writing to you). Talk to them like a trusted
friend who happens to know markets cold, not a corporate analyst writing a report.

Conversational style:
- Match the user's tone and length. A one-line question gets a one- or two-line
  reply. A casual question gets a casual answer. Don't pad.
- Skip headings, bullet ladders, and markdown sections for normal chat. Use them
  ONLY when the user asks for a structured analysis (e.g. a full portfolio review,
  a multi-point comparison) or when a list is genuinely the clearest format.
- Stop volunteering disclaimers. The user opted into this tool and knows it isn't
  regulated advice. Mention risk only when it's directly relevant to the specific
  question (e.g. "this is a leveraged ETF, drawdowns are brutal") — never as a
  footer.
- Don't keep restating "do your own due diligence." They know.
- Don't repeat back the user's question or summarise it before answering. Answer.

What to bring to the table:
- Concrete numbers when useful (price levels, P/E, debt ratio).
- Honest "I don't know" or "the data doesn't support a strong view" when that's
  the truth. Don't manufacture certainty.
- Quantified risk (entry, stop, target) when the user is actually asking about a
  trade idea — not on every reply.
- If the system gave you a portfolio snapshot, use it to ground your answers in
  what the user actually owns. Reference live prices/P&L from the snapshot rather
  than generic stock-tips."""


class AIAdvisor:
    def __init__(self):
        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=SYSTEM_PROMPT,
        )

    async def chat(
        self,
        user_message: str,
        portfolio_context: dict | None = None,
        history: list[dict] | None = None,
        market_snapshot: dict | None = None,
    ) -> str:
        """Generate a reply, replaying conversation history so multi-turn
        chats stay grounded in earlier turns.

        Args:
            user_message: the new user input (raw, as the user typed it).
            portfolio_context: optional snapshot from PortfolioService. Prepended
                to this turn's message so the model sees current prices/P&L.
                Refreshed each call so a long chat stays current.
            history: prior turns as [{role: "user"|"assistant", content: str}, ...]
                in chronological order. Should NOT include `user_message` itself.
            market_snapshot: cached market data (indices, commodities, crypto,
                forex, sectors, movers, headlines, macro). Injected as a full
                preamble on the FIRST turn of a conversation, then condensed
                to a one-line refresher on subsequent turns — Gemini already
                has the full context from turn 1's user message in history.
        """
        # Build the current turn's message — portfolio context (if any) and
        # market snapshot (if any) ride along with this turn only; they
        # aren't saved to the DB so the conversation history stays clean.
        preamble_parts: list[str] = []
        if portfolio_context:
            preamble_parts.append(
                "(Current portfolio snapshot — use this to ground your answer; "
                "don't dwell on it unless asked.)\n"
                + self._format_portfolio_context(portfolio_context)
            )
        if market_snapshot:
            is_first_turn = not history
            if is_first_turn:
                preamble_parts.append(
                    "(Current market snapshot — use this whenever the user asks "
                    "about general markets, asset prices, sectors, or macro. Don't "
                    "fall back to your training data for current prices.)\n"
                    + self._format_market_snapshot_full(market_snapshot)
                )
            else:
                preamble_parts.append(
                    "(Market refresher; the full snapshot was in the first turn.)\n"
                    + self._format_market_snapshot_refresher(market_snapshot)
                )

        if preamble_parts:
            current_turn_text = "\n\n".join(preamble_parts) + "\n\n" + user_message
        else:
            current_turn_text = user_message

        # Convert our role labels to Gemini's. Skip the empty leading model
        # turn case — Gemini errors if history starts with a "model" turn.
        contents: list[dict] = []
        if history:
            for msg in history:
                if not msg.get("content"):
                    continue
                role = "model" if msg["role"] == "assistant" else "user"
                # First message must be from "user"; drop any leading model turns.
                if not contents and role == "model":
                    continue
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        contents.append({"role": "user", "parts": [{"text": current_turn_text}]})

        response = await self._model.generate_content_async(contents)
        return response.text

    async def portfolio_review(self, context: dict) -> str:
        prompt = f"""Please provide a comprehensive portfolio review.

{self._format_portfolio_context(context)}

Structure your review as:
1. **Portfolio Overview** — key stats, overall health
2. **Risk Assessment** — concentration risk, correlated positions, overexposure
3. **Positions to Watch** — underperformers or high-risk holdings
4. **Exit Candidates** — positions you'd recommend reducing or closing, with rationale
5. **Opportunities** — gaps or rebalancing suggestions
6. **Summary** — top 3 actions to take this week"""

        response = await self._model.generate_content_async(prompt)
        return response.text

    async def news_digest(self, db: AsyncSession, ticker: str | None = None) -> str:
        query = select(NewsItem).order_by(NewsItem.published_at.desc()).limit(20)
        if ticker:
            query = query.where(NewsItem.ticker == ticker)
        result = await db.execute(query)
        news_items = result.scalars().all()

        if not news_items:
            return "No recent news found."

        news_text = "\n".join(
            f"- [{item.source}] {item.headline} (sentiment: {item.sentiment_score})"
            for item in news_items
        )

        scope = f"for {ticker}" if ticker else "for the broader market"
        prompt = f"""Digest the following recent news {scope} and extract trading signals.

NEWS:
{news_text}

For each significant signal, provide:
- What happened
- Why it matters for the stock/market
- Directional bias (bullish/bearish/neutral)
- Suggested action or watch level"""

        response = await self._model.generate_content_async(prompt)
        return response.text

    def _format_portfolio_context(self, ctx: dict) -> str:
        lines = [
            f"## Portfolio: {ctx['portfolio_name']} ({ctx['currency']})",
            f"Total Value: ${ctx.get('total_value', 0):,.2f}",
            f"Total P&L: ${ctx.get('total_pnl', 0):,.2f} ({ctx.get('total_pnl_pct', 0):.2f}%)",
            "",
            "### Positions",
        ]
        for p in ctx.get("positions", []):
            pnl = p.get("unrealized_pnl") or 0
            pnl_pct = p.get("unrealized_pnl_pct") or 0
            cp = p.get("current_price") or "N/A"
            lines.append(
                f"- {p['ticker']}: {p['quantity']} shares @ avg ${p['avg_cost']:.2f} | "
                f"Current: ${cp} | P&L: ${pnl:,.2f} ({pnl_pct:.2f}%)"
            )
        return "\n".join(lines)

    # ── Market snapshot formatting ────────────────────────────────────────

    @staticmethod
    def _fmt_cell(name: str, cell: dict) -> str | None:
        """Render one quote cell, e.g. 'Gold $2,450.30 (+0.5%)'."""
        price = cell.get("price")
        chg = cell.get("change_pct")
        if price is None:
            return None
        sign = "+" if chg is not None and chg >= 0 else ""
        chg_str = f" ({sign}{chg:.2f}%)" if chg is not None else ""
        # Format price compactly: small values get more decimals.
        if price >= 1000:
            price_str = f"{price:,.2f}"
        elif price >= 1:
            price_str = f"{price:.2f}"
        else:
            price_str = f"{price:.4f}"
        return f"{name} {price_str}{chg_str}"

    def _format_market_snapshot_full(self, snap: dict) -> str:
        """Full preamble — every section. Used on the first turn of a chat."""
        from datetime import datetime as _dt

        lines = []
        captured = snap.get("_captured_at")
        if captured:
            try:
                ts = _dt.fromisoformat(captured).strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"**Market snapshot** (as of {ts}):")
            except Exception:
                lines.append("**Market snapshot**:")
        else:
            lines.append("**Market snapshot**:")

        def section(label: str, mapping: dict) -> None:
            cells = [self._fmt_cell(n, c) for n, c in mapping.items()]
            cells = [c for c in cells if c]
            if cells:
                lines.append(f"{label}: " + ", ".join(cells))

        section("INDICES",       snap.get("indices", {}))
        section("ASSET CLASSES", snap.get("asset_classes", {}))
        section("CRYPTO",        snap.get("crypto", {}))
        section("FOREX",         snap.get("forex", {}))

        sectors = snap.get("sectors") or []
        sector_strs = [
            f"{s['name']} {'+' if (s.get('change_pct') or 0) >= 0 else ''}"
            f"{s.get('change_pct'):.2f}%"
            for s in sectors if s.get("change_pct") is not None
        ]
        if sector_strs:
            lines.append("SECTORS (S&P 500): " + ", ".join(sector_strs))

        movers = snap.get("top_movers") or {}
        gainers = movers.get("gainers") or []
        losers = movers.get("losers") or []
        if gainers or losers:
            g_str = ", ".join(f"{m['ticker']} +{m['change_pct']:.1f}%" for m in gainers)
            l_str = ", ".join(f"{m['ticker']} {m['change_pct']:.1f}%" for m in losers)
            lines.append(f"TOP MOVERS: {g_str} | {l_str}")

        headlines = snap.get("headlines") or []
        if headlines:
            lines.append("LATEST HEADLINES:")
            for h in headlines:
                lines.append(f"- {h.get('title', '')}")

        upcoming = snap.get("upcoming_macro") or []
        if upcoming:
            lines.append("UPCOMING MACRO: " + "; ".join(
                u.get("event", "") for u in upcoming
            ))

        return "\n".join(lines)

    def _format_market_snapshot_refresher(self, snap: dict) -> str:
        """One-line refresher used on turns after the first — Gemini already
        saw the full snapshot in turn 1, this just nudges with latest top-line
        prices in case the conversation drifted onto a different asset."""
        from datetime import datetime as _dt

        captured = snap.get("_captured_at")
        ts = ""
        if captured:
            try:
                ts = _dt.fromisoformat(captured).strftime("%H:%M UTC")
            except Exception:
                pass

        highlights: list[str] = []
        indices = snap.get("indices", {})
        for name in ("S&P 500", "Nasdaq", "VIX"):
            cell = indices.get(name)
            if cell:
                rendered = self._fmt_cell(name, cell)
                if rendered:
                    highlights.append(rendered)

        for name in ("Gold", "Oil (WTI)", "10Y Treasury"):
            cell = snap.get("asset_classes", {}).get(name)
            if cell:
                rendered = self._fmt_cell(name, cell)
                if rendered:
                    highlights.append(rendered)

        for name in ("Bitcoin", "Ethereum"):
            cell = snap.get("crypto", {}).get(name)
            if cell:
                rendered = self._fmt_cell(name, cell)
                if rendered:
                    highlights.append(rendered)

        prefix = f"Refresher ({ts}): " if ts else "Refresher: "
        return prefix + " | ".join(highlights) if highlights else prefix + "(no fresh data)"
