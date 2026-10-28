#!/usr/bin/env python3
"""
Extract disclosed credit-quality metrics from the latest 10-Q/10-K.

Pulls non-accrual percentages (cost / fair value) and the weighted-average
risk rating when disclosed, with the source sentence and filing link.

Outputs:
    app/public/data/credit/{TICKER}.json
    app/public/data/credit/credit_index.json

Usage:
    python src/processing/extract_credit_quality.py --all
    python src/processing/extract_credit_quality.py --ticker ARCC
"""

import argparse
import html as html_lib
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
FILINGS_DIR = DATA_DIR / "filings"
CREDIT_DIR = DATA_DIR / "credit"
CACHE_DIR = REPO_ROOT / "output" / "credit" / "docs"

HEADERS = {"User-Agent": "BDC-Research research@example.com"}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def fetch_document(url: str, cache_name: str, refresh: bool = False) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / cache_name
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8", errors="ignore")
    resp = requests.get(url, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    text = resp.text
    cache.write_text(text, encoding="utf-8")
    time.sleep(0.12)
    return text


def to_plain_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=\d)\s+\.\s*(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s+%", "%", text)
    return text


def pct_pairs(sentence: str):
    """Return (value, label) percentage pairs where a % is tied to cost/fair value."""
    pairs = []
    for val, label in re.findall(
        r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:of\s+(?:the\s+)?total\s+investments?\s*)?(?:at\s+)?(amortized\s+cost|fair\s+value)",
        sentence, re.I,
    ):
        pairs.append((float(val), label.lower()))
    for label, val in re.findall(
        r"(amortized\s+cost|fair\s+value)[^.;]{0,80}?([0-9]+(?:\.[0-9]+)?)\s*%",
        sentence, re.I,
    ):
        pairs.append((float(val), label.lower()))
    return pairs


WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


PCT_LABEL_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*%\s*"
    r"(?:of\s+[^.]{0,70}?(?:investments?|investment\s+portfolio|portfolio|assets)\s*)?"
    r"(?:at\s+|of\s+the\s+)?(amortized\s+cost|cost\s+basis|cost|fair\s+value)",
    re.I,
)
LABEL_PCT_RE = re.compile(
    r"(amortized\s+cost|cost\s+basis|cost|fair\s+value)\s*[)\s]*"
    r"(?:\([0-9]+\)\s*)?([0-9]+(?:\.[0-9]+)?)\s*%",
    re.I,
)


def parse_non_accrual_at(text: str, pos: int):
    win_start = max(0, pos - 240)
    win_end = pos + 260
    # Do not let the prior-period sentence leak into the window.
    nxt = re.search(
        r"As of\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+[0-9]{1,2},\s+[0-9]{4}",
        text[pos + 5: win_end], re.I,
    )
    if nxt:
        win_end = pos + 5 + nxt.start()
    window = text[win_start: win_end]

    # Skip exclusions and count-only rows that merely mention non-accruals.
    if re.search(r"(?:other\s+than|excluding|excludes?)\s+(?:income\s+from\s+)?non-accrual", window, re.I):
        return None, None, False, window
    if re.search(r"non-accrual\s+status\s+[0-9]+\s+[0-9]+", window, re.I):
        return None, None, False, window

    cost = fv = None
    table = False

    # "comprised 1.1% and 4.0% of ... at fair value and cost, respectively"
    m = re.search(
        r"(?:comprised|represent(?:ed|ing)?|were)\s+([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:and|,)\s*"
        r"(?:approximately\s+)?([0-9]+(?:\.[0-9]+)?)\s*%\s*of[^.]{0,90}?"
        r"at\s+fair\s+value\s+and\s+(?:at\s+)?cost",
        window, re.I,
    )
    if m:
        fv, cost = float(m.group(1)), float(m.group(2))
    m = re.search(
        r"(?:comprised|represent(?:ed|ing)?|were)\s+([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:and|,)\s*"
        r"(?:approximately\s+)?([0-9]+(?:\.[0-9]+)?)\s*%\s*of[^.]{0,90}?"
        r"at\s+cost\s+and\s+(?:at\s+)?fair\s+value",
        window, re.I,
    )
    if m and cost is None and fv is None:
        cost, fv = float(m.group(1)), float(m.group(2))

    if fv is None and cost is None:
        m = re.search(
            r"non-accrual[^.]{0,90}?(?:represented|comprised|were)\s+([0-9]+(?:\.[0-9]+)?)\s*%\s*"
            r"and\s+[0-9]+(?:\.[0-9]+)?\s*%\s*,?\s*respectively[^.]{0,90}?"
            r"(?:fair\s+value\s+basis|at\s+fair\s+value)",
            window, re.I,
        )
        if m:
            fv = float(m.group(1))

    def close_enough(start_abs: int) -> bool:
        if abs(start_abs - pos) <= 240:
            return True
        if start_abs < pos:
            between = text[start_abs:pos].lower()
            tail = between[-90:]
            return ("were on" in tail) or ("represented" in tail) or ("comprised" in tail)
        return False

    cand = {"cost": [], "fv": []}

    def add(kind, val, start_abs):
        if 0 <= val <= 40 and close_enough(start_abs):
            cand[kind].append((abs(start_abs - pos), val))

    for mm in PCT_LABEL_RE.finditer(window):
        label = mm.group(2).lower()
        add("cost" if "cost" in label else "fv", float(mm.group(1)), win_start + mm.start())
    for mm in LABEL_PCT_RE.finditer(window):
        label = mm.group(1).lower()
        add("cost" if "cost" in label else "fv", float(mm.group(2)), win_start + mm.start())

    if cost is None and cand["cost"]:
        cost = min(cand["cost"])[1]
    if fv is None and cand["fv"]:
        fv = min(cand["fv"])[1]

    if cost is None and fv is None:
        after = re.split(r"\.\s", text[pos: pos + 170])[0]
        row = re.search(
            r"non-accrual\s+\$?\s*[0-9][0-9,.]*\s+([0-9]+(?:\.[0-9]+)?)\s*%?\s+"
            r"\$?\s*[0-9][0-9,.]*\s+([0-9]+(?:\.[0-9]+)?)\s*%",
            after, re.I,
        )
        if row:
            cost, fv = float(row.group(1)), float(row.group(2))
            table = True
        else:
            pcts = [float(x) for x in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", after)]
            pcts = [x for x in pcts if 0 <= x <= 40]
            if len(pcts) >= 2:
                if re.search(r"at\s+fair\s+value\s+and\s+(?:at\s+)?cost", after, re.I):
                    fv, cost = pcts[0], pcts[1]
                else:
                    cost, fv = pcts[0], pcts[1]
                table = True

    return cost, fv, table, window


def extract_credit(text: str):
    cost_pct = fv_pct = risk = count = None
    cost_dollars = fv_dollars = None
    snippets = {}
    method = None
    best_score = -1

    results = []
    for m in re.finditer(r"non-accrual", text, re.I):
        cost, fv, table, window = parse_non_accrual_at(text, m.start())
        if cost is None and fv is None:
            continue
        score = (1 if cost is not None else 0) + (1 if fv is not None else 0) + (0 if table else 1)
        results.append((score, cost, fv, table, window))

    results.sort(key=lambda r: -r[0])
    if results:
        best_score, cost_pct, fv_pct, table_best, window_best = results[0]
        method = "table-row" if table_best else "labeled"
        snippets["non_accrual"] = window_best.strip()[:520]
        # Fill a missing side from another occurrence only when the known value agrees.
        for score, cost, fv, table, window in results[1:]:
            if cost_pct is None and cost is not None and (fv is None or fv_pct is None or abs(fv - fv_pct) < 0.05):
                cost_pct = cost
                method = "table-row" if (table or table_best) else method
            if fv_pct is None and fv is not None and (cost is None or cost_pct is None or abs(cost - cost_pct) < 0.05):
                fv_pct = fv
                method = "table-row" if (table or table_best) else method
            if cost_pct is not None and fv_pct is not None:
                break

    # dollar amounts
    for m in re.finditer(r"non-accrual", text, re.I):
        window = text[max(0, m.start() - 240): m.start() + 260]
        dm = re.search(
            r"non-accrual[^.]{0,220}?(?:amortized\s+cost|cost\s+basis|cost)\s+of\s+\$?\s*([0-9][0-9,.]*)"
            r"[^.]{0,140}?(?:fair\s+value)\s+of\s+\$?\s*([0-9][0-9,.]*)",
            window, re.I,
        )
        if dm:
            try:
                cost_dollars = float(dm.group(1).replace(",", ""))
                fv_dollars = float(dm.group(2).replace(",", ""))
                snippets.setdefault("non_accrual_dollars", window.strip()[:400])
                break
            except ValueError:
                pass

    # risk rating: only weighted-average disclosures
    risk_sentences = [
        sent for sent in re.split(r"(?<=[.;])\s+", text)
        if "risk rating" in sent.lower() or "weighted average grade" in sent.lower()
    ]
    risk_sentences.sort(key=lambda x: 0 if "weighted average" in x.lower() else 1)
    for sent in risk_sentences:
        m = re.search(
            r"(?:weighted\s+average\s+(?:internal\s+)?(?:grade|risk\s+rating)|average\s+(?:internal\s+)?risk\s+rating)"
            r"[^.;]{0,170}?(?:was|is|of|at)\s*([1-5](?:\.[0-9]+)?)\b",
            sent, re.I,
        )
        if m:
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            if 1 <= val <= 5:
                risk = val
                start = max(0, m.start() - 220)
                end = min(len(sent), m.end() + 220)
                snippets["risk_rating"] = sent[start:end].strip()
                break

    # count of non-accrual issuers / companies, including word numbers
    for m in re.finditer(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
        r"[^.]{0,80}?\b(?:were|are)\s+(?:on\s+)?non-accrual",
        text, re.I,
    ):
        phrase = m.group(0).lower()
        if not any(w in phrase for w in ("portfolio compan", "issuer", "investment", "loan")):
            continue
        raw = m.group(1).lower()
        count = int(raw) if raw.isdigit() else WORD_NUMS.get(raw)
        if count:
            break

    inconsistent_cost = False
    if cost_pct is not None and fv_pct is not None and cost_pct < fv_pct:
        cost_pct = None
        inconsistent_cost = True

    if cost_pct is not None and fv_pct is not None and method == "labeled":
        confidence = "high"
    elif cost_pct is not None or fv_pct is not None:
        confidence = "medium"
    elif risk is not None:
        confidence = "low"
    else:
        confidence = "none"

    notes = []
    if fv_pct is None:
        notes.append("No non-accrual fair-value percentage found in the latest filing.")
    if inconsistent_cost:
        notes.append("Cost percentage was below fair value; omitted as a likely parse mismatch.")
    if cost_pct is None:
        notes.append("No non-accrual cost percentage found.")
    if risk is None:
        notes.append("No weighted-average risk rating found (many BDCs do not disclose one).")

    return {
        "non_accrual_cost_pct": cost_pct,
        "non_accrual_fv_pct": fv_pct,
        "non_accrual_cost": cost_dollars,
        "non_accrual_fv": fv_dollars,
        "non_accrual_count": count,
        "risk_rating": risk,
        "risk_rating_scale": "1-5 (1 = lowest risk)" if risk is not None else None,
        "confidence": confidence,
        "method": method,
        "snippets": snippets,
        "notes": notes,
    }


def process_ticker(ticker: str, refresh: bool = False):
    filing_data = load_json(FILINGS_DIR / f"{ticker}.json")
    if not filing_data:
        logger.warning("No filing links for %s", ticker)
        return None
    filings = filing_data.get("filings") or {}
    candidates = [f for f in filings.values() if f.get("form") in ("10-Q", "10-K", "10-KT", "10-Q/A", "10-K/A")]
    if not candidates:
        logger.warning("No 10-Q/10-K filing for %s", ticker)
        return None
    latest = max(candidates, key=lambda f: f.get("filed") or "")
    cache_name = f"{ticker}-{latest.get('filed')}.htm"
    try:
        raw = fetch_document(latest["url"], cache_name, refresh=refresh)
    except Exception as exc:
        logger.warning("Document fetch failed for %s: %s", ticker, exc)
        return None
    text = to_plain_text(raw)
    extracted = extract_credit(text)
    payload = {
        "ticker": ticker,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "filing": {
            "form": latest.get("form"),
            "filed": latest.get("filed"),
            "report_date": latest.get("report_date"),
            "url": latest.get("url"),
            "accession": latest.get("accession"),
        },
        "document_chars": len(text),
        **extracted,
    }
    CREDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CREDIT_DIR / f"{ticker}.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    logger.info(
        "%s: non-accrual cost=%s%% fv=%s%% risk=%s (%s)",
        ticker, extracted["non_accrual_cost_pct"], extracted["non_accrual_fv_pct"],
        extracted["risk_rating"], extracted["confidence"],
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract credit quality from latest 10-Q/10-K")
    parser.add_argument("--ticker", help="Only process one ticker")
    parser.add_argument("--refresh", action="store_true", help="Bypass document cache")
    args = parser.parse_args()

    with open(DATA_DIR / "investments_index.json", encoding="utf-8") as fh:
        tickers = sorted(json.load(fh).get("tickers", {}).keys())
    if args.ticker:
        tickers = [args.ticker.upper()]

    results = {}
    for ticker in tickers:
        payload = process_ticker(ticker, refresh=args.refresh)
        if payload:
            results[ticker] = payload

    summary = {
        t: {
            "confidence": p["confidence"],
            "non_accrual_fv_pct": p["non_accrual_fv_pct"],
            "non_accrual_cost_pct": p["non_accrual_cost_pct"],
            "risk_rating": p["risk_rating"],
            "filed": (p.get("filing") or {}).get("filed"),
        }
        for t, p in results.items()
    }
    CREDIT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CREDIT_DIR / "credit_index.json", "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.utcnow().isoformat() + "Z", "tickers": summary}, fh)
    logger.info("Wrote credit quality for %d tickers", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
