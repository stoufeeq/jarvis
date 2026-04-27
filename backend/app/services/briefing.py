"""
Daily Briefing Service.

Assembles portfolio positions, signals, recent news, macro events and
S&P 500 heatmap data, then calls Gemini to produce a structured JSON
briefing. Results are cached in the daily_briefings table — generated
once per day per user on first request, served from DB on subsequent
requests.

Briefing JSON structure:
{
  "overall_sentiment": "bullish" | "neutral" | "cautious" | "bearish",
  "market_context": "1–2 sentence market summary",
  "macro_events": [
    { "event": "...", "date": "...", "impact": "..." }
  ],
  "portfolio": [
    {
      "ticker": "AAPL",
      "action": "hold" | "trim" | "add" | "watch" | "exit",
      "verdict": "one-line verdict",
      "reasoning": "2–4 sentence detailed reasoning"
    }
  ],
  "watchlist_opportunities": [
    {
      "ticker": "NVDA",
      "action": "buy" | "watch" | "avoid",
      "verdict": "one-line",
      "reasoning": "2–4 sentences",
      "catalyst": "what's driving this"
    }
  ],
  "sp500_opportunities": [
    {
      "ticker": "ON",
      "action": "buy" | "watch",
      "verdict": "one-line",
      "reasoning": "2–4 sentences",
      "catalyst": "what's driving this"
    }
  ],
  "summary_bullets": [
    "bullet 1",
    "bullet 2",
    "bullet 3"
  ]
}
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.briefing import DailyBriefing
from app.models.news import NewsItem
from app.models.signal import Signal
from app.models.user import User
from app.services.market_session import MarketSession

log = logging.getLogger(__name__)

MAX_PORTFOLIO_POSITIONS = 15
MAX_WATCHLIST_TICKERS = 20
MAX_SP500_MOVERS = 10
MAX_SIGNALS_PER_TICKER = 3
MAX_NEWS_ITEMS = 10
MAX_MARKET_HEADLINES = 12

# Yahoo Finance RSS feeds for general market news
MARKET_NEWS_FEEDS = [
    ("S&P 500", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
    ("NASDAQ",  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EIXIC&region=US&lang=en-US"),
    ("Dow",     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EDJI&region=US&lang=en-US"),
]


class BriefingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_or_create_today(self, user: User) -> DailyBriefing:
        """Return the most recent briefing for today, generating one if none exists yet."""
        today = datetime.now(UTC).date()
        existing = await self._get_latest_by_date(user.id, today)
        if existing:
            return existing
        return await self._generate(user, today)

    async def regenerate_today(self, user: User) -> DailyBriefing:
        """Generate a new briefing for today, keeping all previous ones in history."""
        today = datetime.now(UTC).date()
        return await self._generate(user, today)

    async def get_history(self, user_id: int, limit: int = 30) -> list[DailyBriefing]:
        result = await self.db.execute(
            select(DailyBriefing)
            .where(DailyBriefing.user_id == user_id)
            .order_by(DailyBriefing.generated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_id(self, briefing_id: int, user_id: int) -> DailyBriefing | None:
        result = await self.db.execute(
            select(DailyBriefing).where(
                DailyBriefing.id == briefing_id,
                DailyBriefing.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete_briefing(self, briefing_id: int, user_id: int) -> bool:
        result = await self.db.execute(
            select(DailyBriefing).where(
                DailyBriefing.id == briefing_id,
                DailyBriefing.user_id == user_id,
            )
        )
        briefing = result.scalar_one_or_none()
        if not briefing:
            return False
        await self.db.delete(briefing)
        return True

    # ── Internal generation ───────────────────────────────────────────────────

    async def _get_latest_by_date(self, user_id: int, d: date) -> DailyBriefing | None:
        result = await self.db.execute(
            select(DailyBriefing)
            .where(
                DailyBriefing.user_id == user_id,
                DailyBriefing.briefing_date == d,
            )
            .order_by(DailyBriefing.generated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _generate(self, user: User, today: date) -> DailyBriefing:
        context = await self._build_context(user)
        content = await self._call_gemini(context)
        content = self._regroup_tickers(content, context)

        # Surface session info so the UI can show "Market closed for weekend" etc.
        content["session"] = context.get("session", {})

        sentiment = content.get("overall_sentiment", "neutral")
        bullets = content.get("summary_bullets", [])
        summary = "\n".join(f"• {b}" for b in bullets[:4])

        briefing = DailyBriefing(
            user_id=user.id,
            briefing_date=today,
            overall_sentiment=sentiment,
            summary=summary,
            content_json=json.dumps(content),
            generated_at=datetime.now(UTC),
        )
        self.db.add(briefing)
        await self.db.flush()
        return briefing

    @staticmethod
    def _regroup_tickers(content: dict, context: dict) -> dict:
        """Re-assign tickers to the correct section based on actual user data.

        Gemini sometimes places tickers in the wrong section (e.g. a watchlist
        ticker in the portfolio section). This method collects all items from
        all three sections and redistributes them to where they belong.
        Priority: portfolio > watchlist > sp500/other.
        """
        portfolio_tickers = {
            pos["ticker"]
            for p in context.get("portfolios", [])
            for pos in p.get("positions", [])
        }
        watchlist_tickers = set(context.get("watchlist_tickers", []))

        log.info(
            "Regroup: portfolio_tickers=%s, watchlist_tickers=%s",
            portfolio_tickers, watchlist_tickers,
        )
        log.info(
            "Regroup before: portfolio=%s, watchlist=%s, sp500=%s",
            [i.get("ticker") for i in content.get("portfolio", [])],
            [i.get("ticker") for i in content.get("watchlist_opportunities", [])],
            [i.get("ticker") for i in content.get("sp500_opportunities", [])],
        )

        # Collect all items from all three sections
        all_items = []
        for item in content.get("portfolio", []):
            all_items.append(item)
        for item in content.get("watchlist_opportunities", []):
            all_items.append(item)
        for item in content.get("sp500_opportunities", []):
            all_items.append(item)

        # Redistribute into correct sections
        portfolio_items = []
        watchlist_items = []
        sp500_items = []

        seen: set[str] = set()
        for item in all_items:
            ticker = item.get("ticker")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)

            if ticker in portfolio_tickers:
                portfolio_items.append(item)
            elif ticker in watchlist_tickers:
                watchlist_items.append(item)
            else:
                sp500_items.append(item)

        content["portfolio"] = portfolio_items
        content["watchlist_opportunities"] = watchlist_items
        content["sp500_opportunities"] = sp500_items

        log.info(
            "Regroup after: portfolio=%s, watchlist=%s, sp500=%s",
            [i.get("ticker") for i in portfolio_items],
            [i.get("ticker") for i in watchlist_items],
            [i.get("ticker") for i in sp500_items],
        )
        return content

    async def _build_context(self, user: User) -> dict:
        """Assemble all data needed for the Gemini prompt."""
        from app.services.portfolio import PortfolioService

        # --- Portfolio ---
        from app.models.portfolio import Portfolio
        port_result = await self.db.execute(
            select(Portfolio)
            .where(Portfolio.user_id == user.id, Portfolio.is_active == True)  # noqa: E712
        )
        portfolios = list(port_result.scalars().all())

        portfolio_context = []
        for portfolio in portfolios:
            try:
                svc = PortfolioService(self.db)
                summary = await svc.get_summary(portfolio)
                positions = (await svc.list_positions(portfolio.id))[:MAX_PORTFOLIO_POSITIONS]
                portfolio_context.append({
                    "name": portfolio.name,
                    "currency": portfolio.currency,
                    "total_value": summary.total_value,
                    "total_pnl_pct": summary.total_pnl_pct,
                    "day_change_pct": summary.day_change_pct,
                    "positions": [
                        {
                            "ticker": p.ticker,
                            "quantity": float(p.quantity),
                            "avg_cost": float(p.avg_cost),
                            "current_price": float(p.current_price) if p.current_price else None,
                            "unrealized_pnl_pct": float(p.unrealized_pnl_pct) if p.unrealized_pnl_pct else None,
                        }
                        for p in positions
                    ],
                })
            except Exception as exc:
                log.warning("Failed to get portfolio summary for %s: %s", portfolio.name, exc)

        # --- Watchlist tickers ---
        from app.models.watchlist import Watchlist, WatchlistItem
        wl_result = await self.db.execute(
            select(WatchlistItem.ticker)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == user.id)
            .distinct()
            .limit(MAX_WATCHLIST_TICKERS)
        )
        watchlist_tickers = [row[0] for row in wl_result.fetchall()]

        # --- Recent signals (last 3 days, non-expired) ---
        cutoff = datetime.now(UTC) - timedelta(days=3)
        sig_result = await self.db.execute(
            select(Signal)
            .where(
                Signal.created_at >= cutoff,
                (Signal.expires_at == None) | (Signal.expires_at > datetime.now(UTC)),  # noqa: E711
            )
            .order_by(Signal.strength.desc(), Signal.created_at.desc())
            .limit(50)
        )
        signals = list(sig_result.scalars().all())

        signals_by_ticker: dict[str, list[dict]] = {}
        for s in signals:
            signals_by_ticker.setdefault(s.ticker, [])
            if len(signals_by_ticker[s.ticker]) < MAX_SIGNALS_PER_TICKER:
                signals_by_ticker[s.ticker].append({
                    "type": s.signal_type.value,
                    "direction": s.direction.value,
                    "strength": s.strength,
                    "rationale": s.rationale,
                })

        # --- Recent high-sentiment news ---
        news_cutoff = datetime.now(UTC) - timedelta(days=2)
        news_result = await self.db.execute(
            select(NewsItem)
            .where(
                NewsItem.processed_at != None,  # noqa: E711
                NewsItem.published_at >= news_cutoff,
                NewsItem.sentiment_score != None,  # noqa: E711
            )
            .order_by(NewsItem.published_at.desc())
            .limit(MAX_NEWS_ITEMS)
        )
        news_items = list(news_result.scalars().all())
        news_context = [
            {
                "ticker": n.ticker,
                "headline": n.headline,
                "sentiment": float(n.sentiment_score) if n.sentiment_score else None,
                "ai_signal": n.ai_signal,
            }
            for n in news_items
        ]

        # --- S&P 500 top movers (from heatmap cache) ---
        sp500_movers: list[dict] = []
        try:
            from app.services.heatmap import HeatmapService
            heatmap = await HeatmapService().get_sp500_heatmap()
            all_stocks = [
                s for sector in heatmap.get("sectors", [])
                for s in sector.get("children", [])
                if s.get("change_pct") is not None
            ]
            # Top movers by absolute change
            top = sorted(all_stocks, key=lambda x: abs(x["change_pct"]), reverse=True)[:MAX_SP500_MOVERS]
            sp500_movers = [
                {"ticker": s["ticker"], "name": s["name"], "change_pct": s["change_pct"]}
                for s in top
            ]
        except Exception as exc:
            log.warning("Could not fetch heatmap for briefing: %s", exc)

        # --- Macro events from signals ---
        macro_events = [
            {"event": s.rationale, "ticker": s.ticker}
            for s in signals
            if s.signal_type.value == "macro_event"
        ]

        # --- Market session state ---
        session = MarketSession()
        session_info = {
            "state": session.state,
            "is_trading_day": session.is_trading_day,
            "is_weekend": session.is_weekend,
            "is_holiday": session.is_holiday,
            "current_et": session.now_et.strftime("%A %Y-%m-%d %H:%M ET"),
            "next_trading_day": session.next_trading_day().strftime("%A %Y-%m-%d"),
            "description": session.describe(),
        }

        # --- Global market headlines (Yahoo Finance RSS) ---
        market_headlines = await self._fetch_market_headlines()

        return {
            "date": str(datetime.now(UTC).date()),
            "session": session_info,
            "portfolios": portfolio_context,
            "watchlist_tickers": watchlist_tickers,
            "signals_by_ticker": signals_by_ticker,
            "recent_news": news_context,
            "market_headlines": market_headlines,
            "sp500_top_movers": sp500_movers,
            "macro_events": macro_events,
        }

    @staticmethod
    async def _fetch_market_headlines() -> list[dict]:
        """Fetch general market headlines from Yahoo Finance RSS feeds.

        Returns up to MAX_MARKET_HEADLINES deduplicated headlines from the
        S&P 500, NASDAQ, and Dow news feeds.
        """
        headlines: list[dict] = []
        seen_titles: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                for index_name, feed_url in MARKET_NEWS_FEEDS:
                    try:
                        r = await client.get(feed_url)
                        r.raise_for_status()
                        root = ET.fromstring(r.text)
                        for item in root.findall(".//item")[:6]:
                            title = (item.findtext("title") or "").strip()
                            pub_date = (item.findtext("pubDate") or "").strip()
                            if not title or title in seen_titles:
                                continue
                            seen_titles.add(title)
                            headlines.append({
                                "source": index_name,
                                "title": title,
                                "pub_date": pub_date,
                            })
                    except Exception as exc:
                        log.warning("Failed to fetch %s headlines: %s", index_name, exc)
        except Exception as exc:
            log.warning("Market headlines fetch failed entirely: %s", exc)

        return headlines[:MAX_MARKET_HEADLINES]

    async def _call_gemini(self, context: dict) -> dict:
        """Send assembled context to Gemini and parse the structured JSON response."""
        settings = get_settings()
        if not settings.gemini_api_key:
            return self._fallback_content("Gemini API key not configured")

        portfolio_str = ""
        for p in context.get("portfolios", []):
            portfolio_str += f"\nPortfolio: {p['name']} ({p['currency']})\n"
            portfolio_str += f"  Total value: {p.get('total_value')}, Day change: {p.get('day_change_pct')}%, PnL: {p.get('total_pnl_pct')}%\n"
            for pos in p.get("positions", []):
                portfolio_str += (
                    f"  - {pos['ticker']}: {pos['quantity']} shares @ avg ${pos['avg_cost']}, "
                    f"current ${pos['current_price']}, PnL {pos['unrealized_pnl_pct']}%\n"
                )

        signals_str = ""
        for ticker, sigs in list(context.get("signals_by_ticker", {}).items())[:20]:
            for s in sigs:
                signals_str += f"  [{ticker}] {s['type']} {s['direction']} (strength {s['strength']}): {s['rationale']}\n"

        news_str = "\n".join(
            f"  [{n.get('ticker','?')}] {n['headline']} (sentiment {n.get('sentiment','?')})"
            for n in context.get("recent_news", [])
        )

        movers_str = "\n".join(
            f"  {m['ticker']} ({m['name']}): {m['change_pct']:+.2f}%"
            for m in context.get("sp500_top_movers", [])
        )

        macro_str = "\n".join(
            f"  {e['event']}" for e in context.get("macro_events", [])
        ) or "  No high-impact macro events in the next 7 days."

        market_headlines_str = "\n".join(
            f"  [{h.get('source', '?')}] {h['title']}"
            for h in context.get("market_headlines", [])
        ) or "  No global market headlines available."

        watchlist = ", ".join(context.get("watchlist_tickers", []))

        # Build session-aware framing
        session = context.get("session", {})
        session_state = session.get("state", "open")
        session_desc = session.get("description", "")
        next_open = session.get("next_trading_day", "")

        if session_state == "open":
            framing = "The market is currently OPEN. Provide intraday-actionable analysis."
            briefing_focus = f"actionable plays for the rest of today's session ({context['date']})"
        elif session_state == "pre_market":
            framing = "We are in PRE-MARKET hours. Focus on what's likely to drive today's regular session at 9:30 AM ET."
            briefing_focus = f"today's open and regular session ({context['date']})"
        elif session_state == "after_hours":
            framing = (
                "We are in AFTER-HOURS. Reflect on today's close, identify overnight risks, "
                f"and outline expectations for the next regular session ({next_open})."
            )
            briefing_focus = f"reflection on today's close + outlook for next open ({next_open})"
        elif session_state in ("closed_weekend", "closed_holiday"):
            day_type = "weekend" if session_state == "closed_weekend" else "US public holiday"
            framing = (
                f"The US market is CLOSED ({day_type}). Use this briefing to look at developments "
                f"during the closure (overseas markets, geopolitical events, weekend news flow) "
                f"that could shape the next open on {next_open}. Avoid intraday tactical language; "
                f"frame everything as preparation for the next trading day."
            )
            briefing_focus = f"outlook and preparation for the next open on {next_open}"
        else:  # closed_overnight
            framing = (
                f"The market is closed overnight. Reflect on today's session and outline "
                f"what to watch for the next open on {next_open}."
            )
            briefing_focus = f"overnight risks + next open ({next_open})"

        prompt = f"""You are a professional financial advisor preparing a market briefing.

CURRENT TIME (US Eastern): {session.get('current_et', context['date'])}
MARKET STATUS: {session_desc}

{framing}

USER PORTFOLIO:
{portfolio_str or "  No portfolio data available."}

WATCHLIST TICKERS: {watchlist or "None"}

RECENT SIGNALS (last 3 days):
{signals_str or "  No recent signals."}

RECENT NEWS (scored by AI, ticker-specific):
{news_str or "  No recent ticker-specific news."}

GLOBAL MARKET HEADLINES (from S&P 500 / NASDAQ / Dow news feeds):
{market_headlines_str}

S&P 500 TOP MOVERS:
{movers_str or "  No heatmap data."}

UPCOMING MACRO EVENTS:
{macro_str}

Generate a comprehensive briefing focused on: {briefing_focus}.

Use the GLOBAL MARKET HEADLINES to identify macro themes, geopolitical risks, central bank actions,
or sector-wide narratives that could impact the user's positions and watchlist. When the market is
closed, weight these headlines heavily — they shape the next open.

For each portfolio/watchlist/S&P 500 item, provide specific, actionable analysis grounded in
either ticker-specific signals/news, OR a clear thread to a global headline above.

Respond with ONLY valid JSON (no markdown, no explanation) in exactly this structure:
{{
  "overall_sentiment": "bullish|neutral|cautious|bearish",
  "market_context": "2-3 sentence market overview for today",
  "macro_events": [
    {{"event": "event name", "date": "date", "impact": "brief impact analysis"}}
  ],
  "portfolio": [
    {{
      "ticker": "AAPL",
      "action": "hold|trim|add|watch|exit",
      "verdict": "one concise sentence",
      "reasoning": "2-4 sentences with specific data points"
    }}
  ],
  "watchlist_opportunities": [
    {{
      "ticker": "NVDA",
      "action": "buy|watch|avoid",
      "verdict": "one concise sentence",
      "reasoning": "2-4 sentences with specific data points",
      "catalyst": "what specific event or signal is driving this"
    }}
  ],
  "sp500_opportunities": [
    {{
      "ticker": "ON",
      "action": "buy|watch|avoid",
      "verdict": "one concise sentence",
      "reasoning": "2-4 sentences",
      "catalyst": "what is driving the opportunity"
    }}
  ],
  "summary_bullets": [
    "Actionable bullet 1 (max 15 words)",
    "Actionable bullet 2 (max 15 words)",
    "Actionable bullet 3 (max 15 words)",
    "Actionable bullet 4 (max 15 words)"
  ]
}}

Include up to 5 portfolio items, 5 watchlist opportunities, and 3 S&P 500 opportunities. Focus on items with the strongest signals and clearest catalysts."""

        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel(settings.gemini_model)
            response = model.generate_content(prompt)
            raw = response.text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            return json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("Gemini returned invalid JSON for briefing: %s", exc)
            return self._fallback_content(f"JSON parse error: {exc}")
        except Exception as exc:
            log.error("Gemini briefing call failed: %s", exc)
            return self._fallback_content(str(exc))

    @staticmethod
    def _fallback_content(error: str) -> dict:
        return {
            "overall_sentiment": "neutral",
            "market_context": f"Briefing generation failed: {error}",
            "macro_events": [],
            "portfolio": [],
            "watchlist_opportunities": [],
            "sp500_opportunities": [],
            "summary_bullets": ["Briefing unavailable — check API configuration"],
        }
