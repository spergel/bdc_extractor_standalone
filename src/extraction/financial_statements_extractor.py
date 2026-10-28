#!/usr/bin/env python3
"""
Extract quarterly financial statements (balance sheet, income statement, cash flow)
from SEC XBRL using the company facts API (one JSON per company).

Usage:
    python src/extraction/financial_statements_extractor.py --ticker ARCC
    python src/extraction/financial_statements_extractor.py --tickers ARCC MAIN BBDC
    python src/extraction/financial_statements_extractor.py --all --cache-dir output/financials/companyfacts

Outputs (per ticker):
    app/public/data/financials/{TICKER}_financials.json
    app/public/data/financials/financials_index.json  (regenerated after each run)
"""

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
FINANCIALS_DIR = DATA_DIR / "financials"
DEFAULT_CACHE_DIR = REPO_ROOT / "output" / "financials" / "companyfacts"

HEADERS = {"User-Agent": "BDC-Research research@example.com"}

# ---------------------------------------------------------------------------
# Statement definitions
# ---------------------------------------------------------------------------

# (concept, display label).  Concepts are looked up in us-gaap first; if a
# concept is missing we simply skip it for that ticker.
STATEMENT_SPEC = {
    "balance_sheet": [
        ("Assets", "Assets"),
        ("Liabilities", "Liabilities"),
        ("LiabilitiesAndStockholdersEquity", "Liabilities + Equity"),
        ("StockholdersEquity", "Stockholders' Equity"),
        ("NetAssets", "Net Assets"),
        ("InvestmentOwnedAtFairValue", "Investments at Fair Value"),
        ("InvestmentsAtFairValue", "Investments at Fair Value"),
        ("CashAndCashEquivalentsAtCarryingValue", "Cash & Equivalents"),
        ("RestrictedCashAndCashEquivalentsAtCarryingValue", "Restricted Cash"),
        ("Cash", "Cash"),
        ("AccountsPayableAndAccruedLiabilities", "Payables & Accrued Liabilities"),
        ("PayablesAndAccruals", "Payables & Accruals"),
        ("LongTermDebt", "Long-Term Debt"),
        ("LongTermDebtNoncurrent", "Long-Term Debt (Noncurrent)"),
        ("DebtInstrumentCarryingAmount", "Debt Carrying Amount"),
        ("ShortTermBorrowings", "Short-Term Borrowings"),
        ("LinesOfCreditCurrent", "Lines of Credit (Current)"),
        ("LongTermLineOfCredit", "Long-Term Line of Credit"),
        ("CommonStockSharesOutstanding", "Shares Outstanding"),
        ("CommonStockValue", "Common Stock Value"),
        ("AdditionalPaidInCapital", "Additional Paid-In Capital"),
        ("RetainedEarningsAccumulatedDeficit", "Retained Earnings / Deficit"),
        ("TreasuryStockValue", "Treasury Stock"),
    ],
    "income_statement": [
        ("GrossInvestmentIncomeOperating", "Total Investment Income"),
        ("InvestmentIncomeNet", "Total Investment Income"),
        ("InterestAndDividendIncomeOperating", "Interest & Dividend Income"),
        ("InterestIncomeOperating", "Interest Income"),
        ("InterestIncomeOperatingPaidInCash", "Interest Income (Cash)"),
        ("InterestIncomeOperatingPaidInKind", "Interest Income (PIK)"),
        ("PaidInKindInterest", "PIK Interest"),
        ("DividendIncomeOperating", "Dividend Income"),
        ("FeeIncome", "Fee Income"),
        ("FeeAndOtherIncome", "Fee & Other Income"),
        ("InterestExpense", "Interest Expense"),
        ("ManagementFeesExpense", "Management Fees"),
        ("ManagementFeeExpense", "Management Fees"),
        ("IncentiveFeeExpense", "Incentive Fees"),
        ("GeneralAndAdministrativeExpense", "G&A Expense"),
        ("OperatingExpenses", "Operating Expenses"),
        ("NetInvestmentIncome", "Net Investment Income"),
        ("InvestmentCompanyInvestmentIncomeLossPerShare", "NII per Share"),
        ("RealizedInvestmentGainsLosses", "Net Realized Gains (Losses)"),
        ("UnrealizedGainLossOnInvestments", "Net Unrealized Gains (Losses)"),
        ("NetIncomeLoss", "Net Income (Loss)"),
        ("EarningsPerShareBasic", "EPS (Basic)"),
        ("EarningsPerShareDiluted", "EPS (Diluted)"),
        ("CommonStockDividendsPerShareDeclared", "Dividends per Share (Declared)"),
        ("CommonStockDividendsPerShareCashPaid", "Dividends per Share (Paid)"),
        ("InvestmentCompanyDividendDistribution", "Dividend Distribution"),
    ],
    "cash_flow": [
        ("NetCashProvidedByUsedInOperatingActivities", "Net Cash from Operations"),
        ("NetCashProvidedByUsedInInvestingActivities", "Net Cash from Investing"),
        ("NetCashProvidedByUsedInFinancingActivities", "Net Cash from Financing"),
        ("PaymentsOfDividends", "Dividends Paid"),
        ("PaymentsOfDividendsCommonStock", "Dividends Paid (Common)"),
        ("DividendsCommonStockCash", "Dividends Paid (Common)"),
        ("ProceedsFromIssuanceOfCommonStock", "Proceeds from Stock Issuance"),
        ("PaymentsForRepurchaseOfCommonStock", "Stock Repurchases"),
        ("ProceedsFromIssuanceOfLongTermDebt", "Proceeds from Debt Issuance"),
        ("RepaymentsOfLongTermDebt", "Debt Repayments"),
        ("RepaymentsOfSeniorDebt", "Senior Debt Repayments"),
        ("ProceedsFromLinesOfCredit", "Proceeds from Credit Lines"),
        ("RepaymentsOfLinesOfCredit", "Repayments of Credit Lines"),
        ("InterestPaidNet", "Interest Paid"),
        ("CashAndCashEquivalentsPeriodIncreaseDecrease", "Net Change in Cash"),
    ],
}

# Concept names that represent total debt (used for derived leverage metrics).
DEBT_CONCEPTS = {
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "DebtInstrumentCarryingAmount",
    "ShortTermBorrowings",
    "LinesOfCreditCurrent",
    "LongTermLineOfCredit",
    "SecuredDebt",
    "UnsecuredDebt",
    "NotesPayable",
    "LoansPayable",
    "CommercialPaper",
}

# Dynamic matching: if a concept label looks like one of these BDC-specific
# line items, include it even if it is in a company-specific namespace.
DYNAMIC_LABEL_PATTERNS = {
    "balance_sheet": [
        (r"\btotal investments\b", "Total Investments"),
        (r"\binvestments,? at fair value\b", "Investments at Fair Value"),
        (r"\binvestment owned", "Investments Owned"),
    ],
    "income_statement": [
        (r"\bnet investment income\b", "Net Investment Income"),
        (r"\btotal investment income\b", "Total Investment Income"),
        (r"\bincentive fee", "Incentive Fees"),
        (r"\bmanagement fee", "Management Fees"),
        (r"\bbase management fee", "Base Management Fees"),
        (r"\bincome incentive fee", "Income Incentive Fees"),
        (r"\bcapital gains incentive fee", "Capital Gains Incentive Fees"),
        (r"\binterest and dividend income", "Interest & Dividend Income"),
        (r"\bpaid.?in.?kind\b", "PIK Income"),
    ],
    "cash_flow": [
        (r"\bpurchases? of investments\b", "Purchases of Investments"),
        (r"\bsales? of investments\b", "Sales of Investments"),
        (r"\bmaturities of investments\b", "Maturities of Investments"),
        (r"\bproceeds from (the )?(sale|sales) of investments\b", "Proceeds from Investments"),
        (r"\bpayments? for investments\b", "Payments for Investments"),
    ],
}

PREFERRED_UNITS = {
    # monetary: use USD if present, else USD/shares (per-share items)
    "USD": 0,
    "USD/shares": 1,
    "shares": 2,
    "USDPerShare": 1,
    "pure": 3,
}

# Only keep facts with period ends on/after this date (keeps files small but
# still gives a long trend).
MIN_PERIOD_END = "2019-01-01"

# Duration windows (days) we keep for duration concepts.  Quarter-length rows
# are preferred for 10-Qs; annual rows for 10-Ks; YTD rows are kept with a
# duration label so the UI can distinguish them.
QUARTER_DURATIONS = (75, 100)
SEMI_DURATIONS = (170, 195)
YTD_DURATIONS = (255, 290)
ANNUAL_DURATIONS = (350, 375)


def load_ticker_ciks() -> dict:
    path = REPO_ROOT / "data" / "company_tickers.json"
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    ciks = {}
    for value in raw.values():
        if isinstance(value, dict) and value.get("ticker"):
            ciks[value["ticker"].upper()] = {
                "cik": str(int(value["cik_str"])).zfill(10),
                "title": value.get("title", ""),
            }
    # Overrides for companies missing from the SEC feed / renamed
    overrides = {
        "BCRED": ("0001803498", "Blackstone Private Credit Fund"),
        "LRFC": ("0001278752", "Logan Ridge Finance Corp"),
        "MRCC": ("0001512931", "Monroe Capital Corporation"),
        "SSSS": ("0001509470", "Neostellar Capital Corp."),
    }
    ciks.update({t: {"cik": c, "title": title} for t, (c, title) in overrides.items()})
    return ciks


def _sec_get(url: str, timeout: int = 180):
    """GET with small retry loop for SEC flakiness."""
    last_exc = None
    for attempt in range(4):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code in (429, 503, 504):
                wait = 5 * (2 ** attempt)
                logger.warning("SEC returned %s for %s; retrying in %.0fs", resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            wait = 5 * (2 ** attempt)
            logger.warning("SEC request failed (%s) for %s; retrying in %.0fs", type(exc).__name__, url, wait)
            time.sleep(wait)
            last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


def fetch_company_facts(cik: str, cache_dir: Path) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"CIK{cik}.json"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning("Cache read failed for %s: %s", cache_path, exc)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    resp = _sec_get(url)
    data = resp.json()
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def concept_key(namespace: str, name: str) -> str:
    return f"{namespace}:{name}"


def lookup_concept(facts: dict, name: str):
    """Find a concept in any namespace, preferring us-gaap."""
    if "us-gaap" in facts and name in facts["us-gaap"]:
        return facts["us-gaap"][name], f"us-gaap:{name}"
    for ns, concepts in facts.items():
        if name in concepts:
            return concepts[name], f"{ns}:{name}"
    return None, None


def unit_rank(unit: str) -> int:
    return PREFERRED_UNITS.get(unit, 10)


def parse_date(text):
    if not text:
        return None
    try:
        return datetime.strptime(str(text), "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_series(concept_node: dict, statement: str) -> list:
    """
    Extract a deduplicated time series of {end, start, val, form, filed, fy, fp, duration_days}
    for one concept.  Balance-sheet rows are instant; income/cash-flow rows are durations.
    """
    out = []
    seen = set()
    for unit, entries in (concept_node.get("units") or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            try:
                val = float(e.get("val"))
            except (TypeError, ValueError):
                continue
            form = str(e.get("form") or "")
            if form not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                continue
            end = parse_date(e.get("end"))
            start = parse_date(e.get("start"))
            if end is None:
                continue
            if end.isoformat() < MIN_PERIOD_END:
                continue
            filed = parse_date(e.get("filed"))
            duration_days = None
            if statement == "balance_sheet":
                if start is not None:
                    continue  # balance sheet rows must be instant
            else:
                if start is None:
                    continue  # income / cash flow rows must be durations
                duration_days = (end - start).days
                if not (
                    QUARTER_DURATIONS[0] <= duration_days <= QUARTER_DURATIONS[1]
                    or SEMI_DURATIONS[0] <= duration_days <= SEMI_DURATIONS[1]
                    or YTD_DURATIONS[0] <= duration_days <= YTD_DURATIONS[1]
                    or ANNUAL_DURATIONS[0] <= duration_days <= ANNUAL_DURATIONS[1]
                ):
                    continue
            # Deduplicate: same period -> keep the most recently filed value
            dedup_key = (start.isoformat() if start else "", end.isoformat(), duration_days, unit)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            out.append({
                "end": end.isoformat(),
                "start": start.isoformat() if start else None,
                "val": val,
                "form": form,
                "filed": filed.isoformat() if filed else None,
                "fy": e.get("fy"),
                "fp": e.get("fp"),
                "duration_days": duration_days,
                "unit": unit,
            })
    return out


def clean_series(series: list) -> list:
    """Sort and de-duplicate series preferring latest filed for identical (end,start,duration)."""
    best = {}
    for row in series:
        key = (row["end"], row["start"], row["duration_days"])
        prev = best.get(key)
        if prev is None or (row["filed"] or "") >= (prev["filed"] or ""):
            best[key] = row
    out = sorted(best.values(), key=lambda r: (r["end"], r["start"] or "", r["duration_days"] or 0))
    return out


def statement_series_map(series: list, statement: str) -> dict:
    """
    Reduce a series to {period_end: {val, filed, form, duration_days}}.
    For duration concepts prefer quarter-length rows over YTD/annual rows when
    both exist for the same end date.
    """
    if statement == "balance_sheet":
        return {
            r["end"]: {
                "val": r["val"], "filed": r["filed"], "form": r["form"],
                "duration_days": None, "unit": r["unit"],
            }
            for r in series
        }
    # Prefer quarter-length, then semi-annual, then YTD, then annual
    priority = []
    for r in series:
        d = r["duration_days"] or 0
        if QUARTER_DURATIONS[0] <= d <= QUARTER_DURATIONS[1]:
            p = 0
        elif SEMI_DURATIONS[0] <= d <= SEMI_DURATIONS[1]:
            p = 1
        elif YTD_DURATIONS[0] <= d <= YTD_DURATIONS[1]:
            p = 2
        else:
            p = 3
        priority.append((r["end"], p, r["filed"] or "", r))
    priority.sort(key=lambda x: (x[0], x[1], x[2]))
    out = {}
    for end, p, filed, r in priority:
        key = (end, p)
        if key in out:
            # keep latest filed within the same priority
            if (filed) < (out[key]["filed"] or ""):
                continue
        out[key] = {
            "val": r["val"], "filed": r["filed"], "form": r["form"],
            "duration_days": r["duration_days"], "unit": r["unit"],
        }
    # Re-key by end only, keeping preferred priority per end
    by_end = {}
    for (end, _p), row in out.items():
        prev = by_end.get(end)
        if prev is None or (prev.get("_priority", 9) > _p):
            row["_priority"] = _p
            by_end[end] = row
    for row in by_end.values():
        row.pop("_priority", None)
    return by_end


def build_statements(facts: dict) -> dict:
    statements = defaultdict(dict)
    dynamic_seen = set()

    for statement, spec in STATEMENT_SPEC.items():
        for name, label in spec:
            node, key = lookup_concept(facts, name)
            if node is None:
                continue
            series = clean_series(extract_series(node, statement))
            if not series:
                continue
            series_map = statement_series_map(series, statement)
            if not series_map:
                continue
            statements[statement][label] = {
                "concept": key,
                "label": label,
                "unit": next(iter(series_map.values()))["unit"],
                "series": series_map,
            }

    # Dynamic BDC-specific concepts from company namespaces
    for ns, concepts in facts.items():
        if ns in ("dei", "us-gaap", "srt"):
            continue
        for name, node in concepts.items():
            label = str(node.get("label") or name)
            label_l = label.lower()
            for statement, patterns in DYNAMIC_LABEL_PATTERNS.items():
                for pattern, display in patterns:
                    if re.search(pattern, label_l):
                        dyn_key = (statement, display)
                        if dyn_key in dynamic_seen:
                            break
                        series = clean_series(extract_series(node, statement))
                        if series:
                            series_map = statement_series_map(series, statement)
                            if series_map:
                                dynamic_seen.add(dyn_key)
                                statements[statement][display] = {
                                    "concept": concept_key(ns, name),
                                    "label": label,
                                    "unit": next(iter(series_map.values()))["unit"],
                                    "series": series_map,
                                }
                        break
    return dict(statements)


def derive_metrics(statements: dict) -> dict:
    """Derive total debt, leverage, NAV/share, NII/share from the statement series."""
    derived = {}

    def series_for(statement, label):
        node = statements.get(statement, {}).get(label)
        return node["series"] if node else {}

    # ---- Total debt: max absolute value among debt concepts per period ----
    debt_candidates = defaultdict(list)
    for label, node in statements.get("balance_sheet", {}).items():
        concept_name = (node.get("concept") or "").split(":")[-1]
        if concept_name in DEBT_CONCEPTS:
            for end, row in node["series"].items():
                debt_candidates[end].append(row["val"])
    total_debt = {}
    for end, values in debt_candidates.items():
        total_debt[end] = {
            "val": max(values, key=abs),
            "filed": None,
            "form": None,
            "duration_days": None,
            "unit": "USD",
        }
    if total_debt:
        derived["total_debt"] = {"label": "Total Debt", "unit": "USD", "series": total_debt}

    assets = series_for("balance_sheet", "Assets")
    liabilities = series_for("balance_sheet", "Liabilities")
    equity = None
    for label in ("Stockholders' Equity", "Net Assets"):
        if label in statements.get("balance_sheet", {}):
            equity = series_for("balance_sheet", label)
            break
    shares = series_for("balance_sheet", "Shares Outstanding")

    if equity and total_debt:
        d2e = {}
        for end, row in total_debt.items():
            if end in equity:
                d2e[end] = {"val": row["val"] / equity[end]["val"] if equity[end]["val"] else None,
                            "filed": None, "form": None, "duration_days": None, "unit": "ratio"}
        derived["debt_to_equity"] = {"label": "Debt / Equity", "unit": "ratio", "series": d2e}

    if assets and total_debt:
        coverage = {}
        for end, row in total_debt.items():
            if end in assets and row["val"]:
                coverage[end] = {"val": assets[end]["val"] / row["val"],
                                 "filed": None, "form": None, "duration_days": None, "unit": "ratio"}
        derived["asset_coverage"] = {"label": "Asset Coverage", "unit": "ratio", "series": coverage}

    if equity and shares:
        nav = {}
        for end in sorted(set(equity) & set(shares)):
            if shares[end]["val"]:
                nav[end] = {"val": equity[end]["val"] / shares[end]["val"],
                            "filed": None, "form": None, "duration_days": None, "unit": "USD/shares"}
        derived["nav_per_share"] = {"label": "NAV per Share", "unit": "USD/shares", "series": nav}

    nii = None
    for label in ("Net Investment Income",):
        if label in statements.get("income_statement", {}):
            nii = series_for("income_statement", label)
            break
    if nii and shares:
        nii_ps = {}
        for end in sorted(set(nii) & set(shares)):
            if shares[end]["val"]:
                nii_ps[end] = {"val": nii[end]["val"] / shares[end]["val"],
                               "filed": None, "form": None, "duration_days": None, "unit": "USD/shares"}
        derived["nii_per_share"] = {"label": "NII per Share", "unit": "USD/shares", "series": nii_ps}

    return derived


def build_periods(statements: dict) -> list:
    """Union of all period end dates across all series, sorted ascending."""
    ends = set()
    for stmt in statements.values():
        for node in stmt.values():
            ends.update(node["series"].keys())
    periods = []
    for end in sorted(ends):
        # Grab form/filed info from the first node that has this end
        form = filed = fy = fp = None
        for stmt in statements.values():
            for node in stmt.values():
                row = node["series"].get(end)
                if row:
                    form = row.get("form") or form
                    filed = row.get("filed") or filed
                    fy = row.get("fy") or fy
                    fp = row.get("fp") or fp
            if form:
                break
        periods.append({"end": end, "form": form, "filed": filed, "fy": fy, "fp": fp})
    return periods


def process_ticker(ticker: str, cik_info: dict, cache_dir: Path) -> Optional[dict]:
    logger.info("Processing %s (CIK %s)", ticker, cik_info["cik"])
    data = fetch_company_facts(cik_info["cik"], cache_dir)
    facts = data.get("facts") or {}
    statements = build_statements(facts)
    if not statements:
        logger.warning("No statements extracted for %s", ticker)
        return None
    derived = derive_metrics(statements)
    periods = build_periods(statements)
    return {
        "ticker": ticker,
        "entity_name": data.get("entityName") or cik_info.get("title") or ticker,
        "cik": cik_info["cik"],
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "periods": periods,
        "statements": statements,
        "derived": derived,
    }


def write_index(results: dict) -> None:
    index = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tickers": {},
    }
    for ticker, payload in results.items():
        periods = payload.get("periods") or []
        index["tickers"][ticker] = {
            "entity_name": payload.get("entity_name"),
            "period_count": len(periods),
            "first_period": periods[0]["end"] if periods else None,
            "latest_period": periods[-1]["end"] if periods else None,
            "statements": sorted(payload.get("statements", {}).keys()),
            "has_cash_flow": bool(payload.get("statements", {}).get("cash_flow")),
        }
    FINANCIALS_DIR.mkdir(parents=True, exist_ok=True)
    with open(FINANCIALS_DIR / "financials_index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract quarterly financial statements from SEC XBRL")
    parser.add_argument("--ticker", help="Single ticker")
    parser.add_argument("--tickers", nargs="+", help="Multiple tickers")
    parser.add_argument("--all", action="store_true", help="Process all tickers in the investments index")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Directory for raw company facts cache")
    args = parser.parse_args()

    ciks = load_ticker_ciks()
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    elif args.all:
        with open(DATA_DIR / "investments_index.json", encoding="utf-8") as fh:
            index = json.load(fh)
        tickers = sorted(index.get("tickers", {}).keys())
    else:
        parser.error("Provide --ticker, --tickers, or --all")

    cache_dir = Path(args.cache_dir)
    FINANCIALS_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    failures = []
    for ticker in tickers:
        if ticker not in ciks:
            logger.warning("No CIK mapping for %s; skipping", ticker)
            failures.append(ticker)
            continue
        try:
            payload = process_ticker(ticker, ciks[ticker], cache_dir)
            if payload:
                out_path = FINANCIALS_DIR / f"{ticker}_financials.json"
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                results[ticker] = payload
                logger.info("Wrote %s (%d periods)", out_path, len(payload.get("periods") or []))
            else:
                failures.append(ticker)
        except Exception as exc:
            logger.error("Failed for %s: %s", ticker, exc)
            failures.append(ticker)
        time.sleep(0.15)  # be polite to SEC

    write_index(results)
    if failures:
        logger.warning("Failures: %s", ", ".join(failures))
    logger.info("Done. %d tickers written, %d failures.", len(results), len(failures))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
