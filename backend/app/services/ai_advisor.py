"""
AI Advisor — powered by Google Gemini via the google-generativeai SDK.
"""

import google.generativeai as genai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.news import NewsItem

settings = get_settings()

SYSTEM_PROMPT = """You are Jarvis, an expert financial advisor and portfolio analyst.
You have deep knowledge of equity markets, technical analysis, fundamental analysis,
macroeconomics, and risk management.

Your role is to:
- Review portfolios and identify risks, opportunities, and imbalances
- Interpret market signals and news to extract actionable insights
- Suggest entry/exit points with clear reasoning
- Always quantify risk (stop-loss levels, position sizing)
- Be direct and specific — avoid vague generalities
- Acknowledge uncertainty honestly; never guarantee outcomes

You are NOT a regulated financial advisor. Always remind users that your analysis
is for informational purposes and they should do their own due diligence.

Output format: Use markdown with clear sections. Use bullet points for lists.
Lead with the most actionable insight."""


class AIAdvisor:
    def __init__(self):
        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=SYSTEM_PROMPT,
        )

    async def chat(self, user_message: str, portfolio_context: dict | None = None) -> str:
        prompt = user_message
        if portfolio_context:
            prompt = f"{self._format_portfolio_context(portfolio_context)}\n\n{user_message}"

        response = await self._model.generate_content_async(prompt)
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
