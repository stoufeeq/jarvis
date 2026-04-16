"""
SEC EDGAR 8-K Material Event Fetcher.

8-K filings disclose material corporate events: earnings releases, M&A,
CEO changes, FDA decisions, restructurings, bankruptcy, etc.

Flow:
  1. Reuse the ticker → CIK map already cached by insider_fetcher
  2. Fetch recent submissions for the CIK from EDGAR
  3. Filter 8-K filings within the last N days
  4. For each 8-K, extract Item codes + exhibit descriptions to classify the event
  5. Store as NewsItem (no new model needed) with ticker tagged and
     a synthetic headline describing the material event

Item codes we care about:
  1.01 — Entry into Material Agreement
  1.02 — Termination of Material Agreement
  1.03 — Bankruptcy or Receivership
  2.01 — Completion of Acquisition/Disposition
  2.02 — Results of Operations (earnings release)
  2.05 — Departure of Officers/Directors
  2.06 — Material Impairment
  3.01 — Notice of Delisting
  5.02 — Departure/Appointment of Officers/Directors
  7.01 — Regulation FD Disclosure
  8.01 — Other Events
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news_item import NewsItem

log = logging.getLogger(__name__)

SEC_HEADERS = {
    "User-Agent": "Jarvis/1.0 dev@jarvis.local",
    "Accept-Encoding": "gzip",
}

EDGAR_BASE = "https://data.sec.gov"
LOOKBACK_DAYS = 7
RATE_LIMIT_SLEEP = 0.15

# Item number → human readable label
ITEM_LABELS: dict[str, str] = {
    "1.01": "Material Agreement",
    "1.02": "Termination of Agreement",
    "1.03": "Bankruptcy/Receivership",
    "2.01": "Acquisition/Disposition",
    "2.02": "Earnings Release",
    "2.05": "Cost-Cutting/Impairment",
    "2.06": "Material Impairment",
    "3.01": "Delisting Notice",
    "5.02": "Executive Change",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Material Event",
}

# Items that warrant higher signal attention
HIGH_IMPORTANCE_ITEMS = {"1.03", "2.01", "2.02", "2.06", "3.01", "5.02"}


class EightKFetcher:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch_for_ticker(self, ticker: str) -> int:
        """Fetch recent 8-K filings for ticker. Returns count of new items stored."""
        from app.services.insider_fetcher import _load_cik_map  # reuse existing CIK cache

        cik_map = await asyncio.get_event_loop().run_in_executor(None, _load_cik_map)
        cik = cik_map.get(ticker.upper())
        if not cik:
            log.debug("No CIK found for %s, skipping 8-K fetch", ticker)
            return 0

        cik_padded = str(cik).zfill(10)
        filings = await self._get_recent_8k_filings(cik_padded)
        if not filings:
            return 0

        count = 0
        for filing in filings:
            stored = await self._store_filing(ticker.upper(), filing)
            if stored:
                count += 1
            await asyncio.sleep(RATE_LIMIT_SLEEP)

        return count

    async def _get_recent_8k_filings(self, cik_padded: str) -> list[dict]:
        """Fetch EDGAR submissions and return recent 8-K accession numbers."""
        url = f"{EDGAR_BASE}/submissions/CIK{cik_padded}.json"
        cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

        try:
            async with httpx.AsyncClient(headers=SEC_HEADERS, timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("EDGAR submissions fetch failed for CIK %s: %s", cik_padded, exc)
            return []

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        items_list = recent.get("items", [])

        results = []
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            try:
                filed_date = datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=UTC)
            except (ValueError, IndexError):
                continue
            if filed_date < cutoff:
                continue

            results.append({
                "accession": accessions[i] if i < len(accessions) else "",
                "filed_at": filed_date,
                "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                "items": items_list[i] if i < len(items_list) else "",
            })

        return results

    async def _store_filing(self, ticker: str, filing: dict) -> bool:
        """Parse 8-K item codes and store as NewsItem. Returns True if new."""
        accession = filing["accession"].replace("-", "")
        filed_at = filing["filed_at"]
        items_raw = filing.get("items", "") or ""

        # Deduplicate — check if we already have a NewsItem with this URL pattern
        accession_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{accession[:10].lstrip('0')}/{filing['accession']}/{filing['primary_doc']}"
        )
        existing = await self.db.execute(
            select(NewsItem).where(NewsItem.url == accession_url).limit(1)
        )
        if existing.scalar_one_or_none():
            return False

        # Parse item codes from the items string (e.g. "2.02,5.02")
        item_codes = [c.strip() for c in items_raw.split(",") if c.strip()]
        if not item_codes:
            item_codes = ["8.01"]  # fallback

        # Build headline from item labels
        labels = [ITEM_LABELS.get(c, f"Item {c}") for c in item_codes if c in ITEM_LABELS]
        if not labels:
            return False  # no recognised items — skip

        importance = "High" if any(c in HIGH_IMPORTANCE_ITEMS for c in item_codes) else "Medium"
        headline = f"[SEC 8-K] {ticker}: {' | '.join(labels)}"

        # Determine a rough sentiment: earnings/acquisitions → slightly positive,
        # bankruptcy/delisting/impairment → negative, everything else → neutral
        sentiment: float | None = None
        if any(c in {"2.02", "2.01", "1.01"} for c in item_codes):
            sentiment = 0.3  # mildly bullish — event happened, market will decide
        elif any(c in {"1.03", "3.01", "2.06"} for c in item_codes):
            sentiment = -0.6
        elif "5.02" in item_codes:
            sentiment = -0.2  # executive departure often negative short term

        news = NewsItem(
            ticker=ticker,
            headline=headline,
            summary=(
                f"SEC Form 8-K filed {filed_at.strftime('%b %d, %Y')}. "
                f"Items: {items_raw}. Importance: {importance}. "
                f"Accession: {filing['accession']}."
            ),
            url=accession_url,
            source="SEC EDGAR 8-K",
            published_at=filed_at,
            sentiment_score=sentiment,
            ai_signal=f"Material event ({', '.join(labels)})" if labels else None,
            processed_at=datetime.now(UTC) if sentiment is not None else None,
        )
        self.db.add(news)
        await self.db.flush()
        return True

    async def fetch_for_all_watchlist_tickers(self) -> dict[str, int]:
        """Fetch 8-Ks for all distinct watchlist tickers. Called by Celery."""
        from sqlalchemy import text
        result = await self.db.execute(
            text("SELECT DISTINCT ticker FROM watchlist_items")
        )
        tickers = [row[0] for row in result.fetchall()]

        totals: dict[str, int] = {}
        for ticker in tickers:
            count = await self.fetch_for_ticker(ticker)
            if count:
                totals[ticker] = count
        return totals
