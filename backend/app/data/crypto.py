"""
Curated list of supported crypto tickers and their CoinGecko IDs.

Tickers are uppercase symbols matching how users type them in the watchlist.
CoinGecko IDs are required by their API (e.g. "bitcoin" not "BTC").

To add a new coin:
1. Look up its ID at https://api.coingecko.com/api/v3/coins/list
2. Add the symbol → id mapping below
3. Restart the backend
"""

CRYPTO_MAPPING: dict[str, dict[str, str]] = {
    "BTC":   {"id": "bitcoin",          "name": "Bitcoin"},
    "ETH":   {"id": "ethereum",         "name": "Ethereum"},
    "SOL":   {"id": "solana",           "name": "Solana"},
    "BNB":   {"id": "binancecoin",      "name": "BNB"},
    "XRP":   {"id": "ripple",           "name": "XRP"},
    "ADA":   {"id": "cardano",          "name": "Cardano"},
    "DOGE":  {"id": "dogecoin",         "name": "Dogecoin"},
    "TRX":   {"id": "tron",             "name": "TRON"},
    "AVAX":  {"id": "avalanche-2",      "name": "Avalanche"},
    "LINK":  {"id": "chainlink",        "name": "Chainlink"},
    "MATIC": {"id": "matic-network",    "name": "Polygon"},
    "DOT":   {"id": "polkadot",         "name": "Polkadot"},
    "LTC":   {"id": "litecoin",         "name": "Litecoin"},
    "UNI":   {"id": "uniswap",          "name": "Uniswap"},
    "ATOM":  {"id": "cosmos",           "name": "Cosmos"},
    "NEAR":  {"id": "near",             "name": "NEAR Protocol"},
    "APT":   {"id": "aptos",            "name": "Aptos"},
    "ARB":   {"id": "arbitrum",         "name": "Arbitrum"},
    "OP":    {"id": "optimism",         "name": "Optimism"},
    "INJ":   {"id": "injective-protocol","name": "Injective"},
    "SUI":   {"id": "sui",              "name": "Sui"},
    "TIA":   {"id": "celestia",         "name": "Celestia"},
}

CRYPTO_TICKERS: frozenset[str] = frozenset(CRYPTO_MAPPING.keys())


def is_crypto(ticker: str) -> bool:
    """Check if a ticker is a known crypto symbol."""
    return ticker.upper() in CRYPTO_TICKERS


def get_coingecko_id(ticker: str) -> str | None:
    """Resolve a ticker symbol to its CoinGecko ID."""
    entry = CRYPTO_MAPPING.get(ticker.upper())
    return entry["id"] if entry else None


def get_crypto_name(ticker: str) -> str | None:
    """Resolve a ticker symbol to its display name."""
    entry = CRYPTO_MAPPING.get(ticker.upper())
    return entry["name"] if entry else None


def list_supported_cryptos() -> list[dict[str, str]]:
    """Return all supported cryptos as a list of {ticker, name} dicts."""
    return [
        {"ticker": t, "name": v["name"], "type": "crypto"}
        for t, v in CRYPTO_MAPPING.items()
    ]
