"""
AI Advisor — routed through the LLM abstraction.

Previously hard-coded to Gemini; now respects the admin's CHAT_MODEL
override (Settings UI → AI Models). Same three methods:
  - chat()             multi-turn conversational reply
  - portfolio_review() single-shot structured review
  - news_digest()      single-shot summarisation of recent news

Provider is resolved per call from the DB override (falling back to
env default) so admin changes take effect immediately without restart.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import NewsItem
from app.services.llm import LLMError, Message, get_llm_for_task

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
    def __init__(self, db: AsyncSession):
        # DB needed to look up the current CHAT_MODEL override (admin
        # setting) at call time. Previously this class was stateless
        # and hard-coded to Gemini.
        self.db = db

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
                to a one-line refresher on subsequent turns.
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

        # Build the message list for the abstraction: system prompt +
        # history + current turn. The abstraction handles per-provider
        # role translation (Gemini's user/model vs OpenAI-style user/
        # assistant) internally.
        messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]
        if history:
            for msg in history:
                if not msg.get("content"):
                    continue
                role = "assistant" if msg["role"] == "assistant" else "user"
                # First non-system message should be a user turn; drop
                # any leading assistant turns that would confuse Gemini.
                if len(messages) == 1 and role == "assistant":
                    continue
                messages.append(Message(role=role, content=msg["content"]))
        messages.append(Message(role="user", content=current_turn_text))

        client, model_id = await get_llm_for_task(self.db, "chat")
        return await client.chat(messages, model=model_id, temperature=0.7)

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

        client, model_id = await get_llm_for_task(self.db, "chat")
        # Prepend system prompt so tone/style matches the chat surface.
        messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]
        return await client.chat(messages, model=model_id, temperature=0.5)

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

        client, model_id = await get_llm_for_task(self.db, "chat")
        return await client.complete(prompt, model=model_id, temperature=0.4)

    def _format_portfolio_context(self, ctx: dict) -> str:
        lines = [
            f"## Portfolio: {ctx['portfolio_name']} ({ctx['currency']})",
            f"Total Value: ${ctx.get('total_value', 0):,.2f}",
            f"Total P&L: ${ctx.get('total_pnl', 0):,.2f} ({ctx.get('total_pnl_pct', 0):.2f}%)",
            "",
            "### Positions",
        ]
        # Momentum scores are enriched by the /advisor/chat endpoint;
        # keyed by upper-case ticker. When present, the per-position line
        # gets a short suffix so the model can factor it into answers.
        # Absent (or None per ticker) means no intraday data — usually
        # crypto or after-hours weekend fetches.
        momentum = ctx.get("momentum_scores") or {}
        for p in ctx.get("positions", []):
            pnl = p.get("unrealized_pnl") or 0
            pnl_pct = p.get("unrealized_pnl_pct") or 0
            cp = p.get("current_price") or "N/A"
            m = momentum.get(str(p.get("ticker", "")).upper())
            mom_suffix = ""
            if m:
                verdict_label = m.get("verdict", "").replace("_", " ").title()
                mom_suffix = f" | Momentum(15m): {verdict_label} (score {m.get('score')})"
            lines.append(
                f"- {p['ticker']}: {p['quantity']} shares @ avg ${p['avg_cost']:.2f} | "
                f"Current: ${cp} | P&L: ${pnl:,.2f} ({pnl_pct:.2f}%)"
                f"{mom_suffix}"
            )
        if momentum:
            lines.append("")
            lines.append(
                "(Momentum(15m) = intraday 9/20/50 EMA + VWAP composite. Strong Bull / "
                "Bull = trend + VWAP + trigger aligned bullish; Neutral = mixed; Bear / "
                "Strong Bear = aligned bearish. Backtest showed this is directional "
                "context for human judgment, not an automated edge signal — weight it "
                "accordingly.)"
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
        """One-line refresher used on turns after the first — the model
        already saw the full snapshot in turn 1, this just nudges with
        latest top-line prices."""
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


# Kept so an old caller like `AIAdvisor()` fails loudly at construction
# rather than mysteriously later. LLMError is a runtime issue, this is
# a call-site contract issue.
_ = LLMError
