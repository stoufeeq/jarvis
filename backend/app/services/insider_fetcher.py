"""
SEC EDGAR Form 4 fetcher.

Flow:
  1. Load ticker → CIK mapping from EDGAR's static JSON (cached in memory)
  2. Fetch recent submissions for the CIK
  3. Filter Form 4 filings within the requested date window
  4. For each filing, download & parse the raw form4.xml
  5. Persist InsiderTrade rows; deduplicate via sec_accession_number

Transaction codes we import:
  P = Purchase (open-market buy)  → buy
  S = Sale (open-market sell)     → sell
  G = Gift                        → gift
  M = Option exercise             → option_exercise
  A = Award/grant — skipped (not a market transaction)
  F = Tax withholding  — skipped
  D = Return/cancellation — skipped
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insider_trade import InsiderTrade, InsiderTransactionType

log = logging.getLogger(__name__)

SEC_HEADERS = {
    "User-Agent": "Jarvis/1.0 dev@jarvis.local",
    "Accept-Encoding": "gzip",
}

# Transaction code → our enum
TX_CODE_MAP: dict[str, InsiderTransactionType] = {
    "P": InsiderTransactionType.buy,
    "S": InsiderTransactionType.sell,
    "G": InsiderTransactionType.gift,
    "M": InsiderTransactionType.option_exercise,
}

_TICKER_CIK_CACHE: dict[str, str] = {}


def _txt(el: ET.Element | None, path: str) -> str:
    """Safely extract text from an XML element path like 'a/b/value'."""
    if el is None:
        return ""
    node = el.find(path)
    return (node.text or "").strip() if node is not None else ""


class InsiderTradeFetcher:
    BASE = "https://www.sec.gov"
    DATA = "https://data.sec.gov"

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=SEC_HEADERS, timeout=20)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── CIK lookup ──────────────────────────────────────────────────────────

    async def _load_cik_map(self) -> dict[str, str]:
        """Download the SEC's ticker→CIK mapping (cached for the lifetime of
        the process)."""
        global _TICKER_CIK_CACHE
        if _TICKER_CIK_CACHE:
            return _TICKER_CIK_CACHE
        client = await self._get_client()
        r = await client.get(f"{self.BASE}/files/company_tickers.json")
        r.raise_for_status()
        raw: dict[str, dict[str, Any]] = r.json()
        _TICKER_CIK_CACHE = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in raw.values()
        }
        return _TICKER_CIK_CACHE

    async def cik_for_ticker(self, ticker: str) -> str | None:
        mapping = await self._load_cik_map()
        return mapping.get(ticker.upper())

    # ── Submissions ──────────────────────────────────────────────────────────

    async def _recent_form4s(
        self, cik: str, since: datetime
    ) -> list[tuple[str, str, str]]:
        """Return list of (accession_number, filing_date, xml_filename)
        for Form 4 filings on or after `since`."""
        client = await self._get_client()
        r = await client.get(f"{self.DATA}/submissions/CIK{cik}.json")
        r.raise_for_status()
        recent = r.json()["filings"]["recent"]

        filings = []
        for i, form in enumerate(recent["form"]):
            if form not in ("4", "4/A"):
                continue
            date_str: str = recent["filingDate"][i]
            filed = datetime.fromisoformat(date_str)
            if filed < since.replace(tzinfo=None):
                continue
            acc: str = recent["accessionNumber"][i]
            primary_doc: str = recent["primaryDocument"][i]
            # Strip the XSL wrapper path (e.g. "xslF345X06/form4.xml" → "form4.xml")
            xml_file = primary_doc.split("/")[-1] if "/" in primary_doc else primary_doc
            filings.append((acc, date_str, xml_file))

        return filings

    # ── XML parsing ──────────────────────────────────────────────────────────

    async def _fetch_xml(self, cik: str, acc: str, xml_file: str) -> ET.Element | None:
        """Download the raw Form 4 XML for a filing."""
        client = await self._get_client()
        acc_nd = acc.replace("-", "")
        cik_int = str(int(cik))  # no leading zeros in path
        url = f"{self.BASE}/Archives/edgar/data/{cik_int}/{acc_nd}/{xml_file}"
        try:
            r = await client.get(url)
            r.raise_for_status()
            return ET.fromstring(r.text)
        except Exception as exc:
            log.warning("Could not fetch Form 4 XML %s: %s", url, exc)
            return None

    def _parse_form4(
        self, root: ET.Element, ticker: str, acc: str, filing_date: str
    ) -> list[dict]:
        """Parse an ownershipDocument XML element into a list of transaction dicts."""
        transactions: list[dict] = []

        issuer_symbol = _txt(root, "issuer/issuerTradingSymbol")
        company_name = _txt(root, "issuer/issuerName")

        owner_el = root.find("reportingOwner")
        if owner_el is None:
            return []

        insider_name = _txt(owner_el, "reportingOwnerId/rptOwnerName")
        rel_el = owner_el.find("reportingOwnerRelationship")
        is_director = _txt(rel_el, "isDirector").lower() in ("1", "true") if rel_el is not None else False
        is_officer = _txt(rel_el, "isOfficer").lower() in ("1", "true") if rel_el is not None else False
        officer_title = _txt(rel_el, "officerTitle") if rel_el is not None else None

        # Non-derivative transactions (plain stock)
        for tx in root.findall(".//nonDerivativeTransaction"):
            code = _txt(tx, "transactionCoding/transactionCode")
            tx_type = TX_CODE_MAP.get(code)
            if tx_type is None:
                continue  # skip awards, tax withholds, etc.

            shares_str = _txt(tx, "transactionAmounts/transactionShares/value")
            price_str = _txt(tx, "transactionAmounts/transactionPricePerShare/value")
            owned_str = _txt(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            date_str = _txt(tx, "transactionDate/value") or filing_date

            try:
                shares = float(shares_str) if shares_str else 0.0
            except ValueError:
                shares = 0.0
            try:
                price = float(price_str) if price_str else None
            except ValueError:
                price = None
            try:
                shares_owned = float(owned_str) if owned_str else None
            except ValueError:
                shares_owned = None

            total_value = shares * price if price else None

            try:
                tx_date = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
            except ValueError:
                tx_date = None

            try:
                filed_at = datetime.fromisoformat(filing_date).replace(tzinfo=UTC)
            except ValueError:
                filed_at = datetime.now(UTC)

            transactions.append(dict(
                ticker=ticker.upper(),
                company_name=company_name or None,
                insider_name=insider_name,
                insider_title=officer_title or None,
                is_director=is_director,
                is_officer=is_officer,
                is_ten_pct_owner=False,
                transaction_type=tx_type,
                shares=shares,
                price_per_share=price,
                total_value=total_value,
                shares_owned_after=shares_owned,
                transaction_date=tx_date,
                filed_at=filed_at,
                sec_accession_number=acc,
            ))

        return transactions

    # ── Main entry point ─────────────────────────────────────────────────────

    async def fetch_for_ticker(
        self,
        ticker: str,
        db: AsyncSession,
        days: int = 90,
    ) -> int:
        """Fetch recent Form 4 filings for `ticker`, persist new rows,
        return count of rows inserted."""
        cik = await self.cik_for_ticker(ticker)
        if not cik:
            log.info("No CIK found for ticker %s", ticker)
            return 0

        since = datetime.now(UTC) - timedelta(days=days)
        filings = await self._recent_form4s(cik, since)
        if not filings:
            return 0

        # Load already-known accession numbers to skip re-parsing
        existing_accs: set[str] = set(
            row[0]
            for row in (
                await db.execute(
                    select(InsiderTrade.sec_accession_number).where(
                        InsiderTrade.ticker == ticker.upper(),
                        InsiderTrade.sec_accession_number != None,  # noqa: E711
                    )
                )
            ).all()
        )

        inserted = 0
        # Small delay between requests to respect SEC rate limit (10 req/s)
        for acc, filing_date, xml_file in filings:
            if acc in existing_accs:
                continue
            await asyncio.sleep(0.15)
            root = await self._fetch_xml(cik, acc, xml_file)
            if root is None:
                continue
            rows = self._parse_form4(root, ticker, acc, filing_date)
            for row in rows:
                db.add(InsiderTrade(**row))
                inserted += 1

        if inserted:
            await db.flush()

        log.info("InsiderFetcher: %s → %d new rows", ticker, inserted)
        return inserted
