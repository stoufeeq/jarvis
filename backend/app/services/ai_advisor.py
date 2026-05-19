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
        """
        # Build the current turn's message — portfolio context (if any)
        # rides along with this turn only; it isn't saved to the DB so the
        # conversation history stays clean.
        if portfolio_context:
            current_turn_text = (
                "(Current portfolio snapshot — use this to ground your answer; "
                "don't dwell on it unless asked.)\n"
                f"{self._format_portfolio_context(portfolio_context)}\n\n"
                f"{user_message}"
            )
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
