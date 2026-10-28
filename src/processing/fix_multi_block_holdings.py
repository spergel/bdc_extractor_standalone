#!/usr/bin/env python3
"""
Fix 10-K holdings files that contain two concatenated portfolio schedules.

Some 10-K filings present the current-year and prior-year schedules back to
back. When the scraper flattens both, the period's row count and fair value
roughly double (e.g. ARCC Q4 2025: $56B vs a true ~$29B). This script detects
the repeated company sequence, keeps the block whose fair value matches the
neighbouring quarters, and rewrites both the per-period CSV and the
consolidated ticker CSV.

Usage:
    python src/processing/fix_multi_block_holdings.py --dry-run
    python src/processing/fix_multi_block_holdings.py --ticker ARCC
    python src/processing/fix_multi_block_holdings.py
"""

import argparse
import csv
import json
import logging
import os
from collections import defaultdict
from statistics import median
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
INVESTMENTS_DIR = DATA_DIR / "investments"
INDEX_PATH = DATA_DIR / "investments_index.json"


def fair_value(row) -> float:
    try:
        return float(row.get("fair_value") or 0)
    except (TypeError, ValueError):
        return 0.0


def norm_name(name):
    text = (name or "").lower()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def company_set(rows):
    return {norm_name(r.get("company_name")) for r in rows if norm_name(r.get("company_name"))}


def split_candidates(rows):
    """Indices where the company sequence restarts (a second schedule begins)."""
    names = [(r.get("company_name") or "").strip() for r in rows]
    n = len(names)
    if n < 200:
        return []
    early = {x for x in names[: max(40, n // 10)] if x}
    out = []
    for i in range(max(60, n // 3), n - 100):
        if names[i] not in early:
            continue
        hits = sum(1 for j in range(i, min(n, i + 12)) if names[j] in early)
        if hits >= 4:
            out.append(i)
    collapsed = []
    for i in out:
        if not collapsed or i - collapsed[-1] > 50:
            collapsed.append(i)
    return collapsed


def best_block(rows, target, neighbor_rows=None):
    """Return (block, description) if a two-schedule split matches target."""
    total = sum(fair_value(r) for r in rows)
    if target <= 0 or total > 1e8:
        return None
    ratio = total / target
    if not (1.7 <= ratio <= 2.3):
        return None
    neighbor_names = company_set(neighbor_rows or [])
    best = None
    for i in split_candidates(rows):
        prefix, suffix = rows[:i], rows[i:]
        if min(len(prefix), len(suffix)) < len(rows) * 0.2:
            continue
        prefix_names, suffix_names = company_set(prefix), company_set(suffix)
        overlap = len(prefix_names & suffix_names) / max(1, len(prefix_names | suffix_names))
        if overlap < 0.35:
            continue
        for label, block, block_names in (("prefix", prefix, prefix_names), ("suffix", suffix, suffix_names)):
            block_total = sum(fair_value(r) for r in block)
            err = abs(block_total - target) / target
            if err > 0.10:
                continue
            next_overlap = len(block_names & neighbor_names) / max(1, len(block_names | neighbor_names)) if neighbor_names else 0
            score = (round(next_overlap, 4), -err)
            if best is None or score > best[0]:
                best = (score, block, label, i, block_total, total, overlap, err, next_overlap)
    return best


def load_consolidated(ticker):
    path = INVESTMENTS_DIR / f"{ticker}.csv"
    if not path.exists():
        return None, None
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), reader.fieldnames


def write_consolidated(ticker, rows, fieldnames):
    path = INVESTMENTS_DIR / f"{ticker}.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_period_files(ticker, groups, fieldnames):
    tdir = INVESTMENTS_DIR / ticker
    tdir.mkdir(parents=True, exist_ok=True)
    period_fields = [f for f in fieldnames if f not in ("ticker", "filing_date")]
    for filed, rows in groups.items():
        path = tdir / f"{filed}.csv"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=period_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def process_ticker(ticker, dry_run=False):
    rows, fields = load_consolidated(ticker)
    if not rows:
        logger.warning("%s: no consolidated holdings", ticker)
        return None
    groups = defaultdict(list)
    for row in rows:
        fd = (row.get("filing_date") or "").strip()
        if fd:
            groups[fd].append(row)
    dates = sorted(groups)
    changed = []

    # Iterate: fixing one doubled period can reveal the true level for its
    # neighbours, which in turn exposes the next doubled period.
    for _ in range(8):
        totals = {d: sum(fair_value(r) for r in groups[d]) for d in dates}
        positive = [v for v in totals.values() if v > 0]
        if not positive:
            return changed or None
        global_median = median(positive)
        pass_changed = False
        for idx, fd in enumerate(dates):
            neighbors = []
            for j in range(max(0, idx - 2), min(len(dates), idx + 3)):
                if j == idx:
                    continue
                v = totals[dates[j]]
                if v > 0 and v <= global_median * 1.3:
                    neighbors.append(v)
            target = median(neighbors) if neighbors else global_median
            neighbor_rows = groups[dates[idx + 1]] if idx + 1 < len(dates) else (groups[dates[idx - 1]] if idx > 0 else [])
            found = best_block(groups[fd], target, neighbor_rows)
            if not found:
                continue
            score, block, label, split_at, block_total, total, overlap, err, next_overlap = found
            changed.append((fd, len(groups[fd]), round(total / 1e6, 2), len(block), round(block_total / 1e6, 2), label, round(overlap, 2), round(next_overlap, 2)))
            groups[fd] = block
            pass_changed = True
        if not pass_changed:
            break

    if not changed:
        return []

    logger.info("%s: fixed %d multi-block period(s)", ticker, len(changed))
    for c in changed:
        logger.info("  %s rows %d ($%.2fB) -> %s rows %d ($%.2fB, block-overlap %.2f, next-period overlap %.2f)", c[0], c[1], c[2], c[5], c[3], c[4], c[6], c[7])

    if dry_run:
        return changed

    write_consolidated(ticker, [r for d in dates for r in groups[d]], fields)
    write_period_files(ticker, groups, fields)
    return changed


def update_index():
    if not INDEX_PATH.exists():
        return
    with open(INDEX_PATH, encoding="utf-8") as fh:
        index = json.load(fh)
    for ticker, info in (index.get("tickers") or {}).items():
        tdir = INVESTMENTS_DIR / ticker
        count = 0
        size = 0
        periods = sorted(p.stem for p in tdir.glob("*.csv"))
        for p in periods:
            path = tdir / f"{p}.csv"
            with open(path, encoding="utf-8") as fh:
                count += max(0, sum(1 for _ in fh) - 1)
            size += path.stat().st_size
        info["row_count"] = count
        info["file_size_mb"] = round(size / (1024 * 1024), 2)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fix concatenated annual portfolio schedules")
    parser.add_argument("--ticker", help="Only process one ticker")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = sorted(p.name for p in INVESTMENTS_DIR.iterdir() if p.is_dir())
    total = 0
    for ticker in tickers:
        try:
            changed = process_ticker(ticker, dry_run=args.dry_run)
        except Exception as exc:
            logger.warning("%s: failed: %s", ticker, exc)
            continue
        if changed:
            total += len(changed)
    if not args.dry_run:
        update_index()
    logger.info("Done: %d period(s) fixed%s", total, " (dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
