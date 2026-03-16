"""
Refresh BDC market data profile.json files from Yahoo Finance.
Writes to frontend/public/data/{TICKER}/profile.json.

Usage:
    python scripts/refresh_bdc_profiles.py [--ticker ARCC] [--dry-run]
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def _discover_tickers() -> list[str]:
    """Return all tickers that have a frontend data directory (all-caps subdirs)."""
    return sorted(
        d.name for d in DATA_DIR.iterdir()
        if d.is_dir() and d.name.isupper()
    )


ALL_TICKERS = _discover_tickers()


def _get(info: dict, key: str, default=None):
    v = info.get(key, default)
    return v if v is not None else default


def build_profile(ticker: str, info: dict) -> dict:
    return {
        "ticker": ticker,
        "name": _get(info, "longName") or _get(info, "shortName") or ticker,
        "price": {
            "regularMarketPrice": _get(info, "regularMarketPrice"),
            "regularMarketChange": _get(info, "regularMarketChange"),
            "regularMarketChangePercent": _get(info, "regularMarketChangePercent"),
            "currency": _get(info, "currency", "USD"),
            "regularMarketPreviousClose": _get(info, "regularMarketPreviousClose"),
            "regularMarketOpen": _get(info, "regularMarketOpen"),
            "regularMarketDayHigh": _get(info, "regularMarketDayHigh"),
            "regularMarketDayLow": _get(info, "regularMarketDayLow"),
            "regularMarketVolume": _get(info, "regularMarketVolume"),
            "fiftyTwoWeekHigh": _get(info, "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": _get(info, "fiftyTwoWeekLow"),
            "averageVolume": _get(info, "averageVolume"),
            "averageVolume10days": _get(info, "averageDailyVolume10Day"),
            "bid": _get(info, "bid"),
            "ask": _get(info, "ask"),
            "bidSize": _get(info, "bidSize"),
            "askSize": _get(info, "askSize"),
        },
        "summaryDetail": {
            "marketCap": _get(info, "marketCap"),
            "enterpriseValue": _get(info, "enterpriseValue"),
            "trailingPE": _get(info, "trailingPE"),
            "forwardPE": _get(info, "forwardPE"),
            "dividendYield": _get(info, "dividendYield"),
            "dividendRate": _get(info, "dividendRate"),
            "payoutRatio": _get(info, "payoutRatio"),
            "beta": _get(info, "beta"),
            "bookValue": _get(info, "bookValue"),
            "priceToBook": _get(info, "priceToBook"),
            "priceToSalesTrailing12Months": _get(info, "priceToSalesTrailing12Months"),
            "enterpriseToRevenue": _get(info, "enterpriseToRevenue"),
            "profitMargins": _get(info, "profitMargins"),
            "grossMargins": _get(info, "grossMargins"),
            "operatingMargins": _get(info, "operatingMargins"),
            "ebitdaMargins": _get(info, "ebitdaMargins"),
            "returnOnAssets": _get(info, "returnOnAssets"),
            "returnOnEquity": _get(info, "returnOnEquity"),
        },
        "assetProfile": {
            "sector": _get(info, "sector"),
            "industry": _get(info, "industry"),
            "website": _get(info, "website"),
            "longBusinessSummary": _get(info, "longBusinessSummary"),
            "city": _get(info, "city"),
            "state": _get(info, "state"),
            "country": _get(info, "country"),
            "address1": _get(info, "address1"),
            "zip": _get(info, "zip"),
            "phone": _get(info, "phone"),
            "fax": _get(info, "fax"),
            "longName": _get(info, "longName"),
            "shortName": _get(info, "shortName"),
            "exchange": _get(info, "exchange"),
            "quoteType": _get(info, "quoteType"),
        },
        "financialData": {
            "totalRevenue": _get(info, "totalRevenue"),
            "revenuePerShare": _get(info, "revenuePerShare"),
            "totalCash": _get(info, "totalCash"),
            "totalCashPerShare": _get(info, "totalCashPerShare"),
            "totalDebt": _get(info, "totalDebt"),
            "currentRatio": _get(info, "currentRatio"),
            "quickRatio": _get(info, "quickRatio"),
            "debtToEquity": _get(info, "debtToEquity"),
            "grossProfits": _get(info, "grossProfits"),
            "freeCashflow": _get(info, "freeCashflow"),
            "operatingCashflow": _get(info, "operatingCashflow"),
            "earningsGrowth": _get(info, "earningsGrowth"),
            "revenueGrowth": _get(info, "revenueGrowth"),
            "earningsQuarterlyGrowth": _get(info, "earningsQuarterlyGrowth"),
            "exDividendDate": _get(info, "exDividendDate"),
            "dividendDate": _get(info, "dividendDate"),
        },
        "keyStatistics": {
            "sharesOutstanding": _get(info, "sharesOutstanding"),
            "sharesShort": _get(info, "sharesShort"),
            "sharesShortPriorMonth": _get(info, "sharesShortPriorMonth"),
            "shortRatio": _get(info, "shortRatio"),
            "shortPercentOfFloat": _get(info, "shortPercentOfFloat"),
            "sharesPercentSharesOut": _get(info, "sharesPercentSharesOut"),
            "heldPercentInsiders": _get(info, "heldPercentInsiders"),
            "heldPercentInstitutions": _get(info, "heldPercentInstitutions"),
            "impliedSharesOutstanding": _get(info, "impliedSharesOutstanding"),
        },
        "historical": {
            "fiftyTwoWeekHigh": _get(info, "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": _get(info, "fiftyTwoWeekLow"),
            "currentPrice": _get(info, "currentPrice") or _get(info, "regularMarketPrice"),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_ticker(ticker: str, dry_run: bool = False) -> bool:
    out_path = DATA_DIR / ticker / "profile.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        info = yf.Ticker(ticker).info
        if not info.get("regularMarketPrice") and not info.get("currentPrice"):
            logger.warning(f"{ticker}: no price data returned")
            return False
        profile = build_profile(ticker, info)
        if dry_run:
            logger.info(f"{ticker}: [dry-run] price={profile['price']['regularMarketPrice']} nav={profile['summaryDetail']['bookValue']}")
            return True
        out_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        price = profile["price"]["regularMarketPrice"]
        nav = profile["summaryDetail"]["bookValue"]
        ptb = profile["summaryDetail"]["priceToBook"]
        disc = f"{(ptb - 1) * 100:+.1f}%" if ptb else "n/a"
        logger.info(f"{ticker}: price=${price} nav=${nav} disc/prem={disc}")
        return True
    except Exception as e:
        logger.error(f"{ticker}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Refresh BDC market data profile.json files")
    parser.add_argument("--ticker", help="Refresh a single ticker only")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing files")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else ALL_TICKERS
    ok = 0
    fail = 0
    for t in tickers:
        if refresh_ticker(t, dry_run=args.dry_run):
            ok += 1
        else:
            fail += 1

    logger.info(f"Done: {ok} refreshed, {fail} failed")
    if fail > 0 and ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
