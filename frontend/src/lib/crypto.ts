/**
 * Frontend mirror of backend/app/data/crypto.py — keeps the curated
 * crypto ticker set in sync so the UI can render badges, hide P/E columns,
 * and behave appropriately for crypto without making a network call.
 *
 * If you add a new ticker to the backend file, also add it here.
 */
export const CRYPTO_TICKERS = new Set([
  "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TRX",
  "AVAX", "LINK", "MATIC", "DOT", "LTC", "UNI", "ATOM",
  "NEAR", "APT", "ARB", "OP", "INJ", "SUI", "TIA",
]);

export function isCrypto(ticker: string | null | undefined): boolean {
  if (!ticker) return false;
  return CRYPTO_TICKERS.has(ticker.toUpperCase());
}
