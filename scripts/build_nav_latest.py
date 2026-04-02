#!/usr/bin/env python3
"""Emit frontend/public/data/financials/nav_latest.json from per-ticker balance sheet CSVs.

Uses the same rule as client-csv addStatementRows: for the latest filing_date, take the first
NetAssetValuePerShare row (current period; later rows are often comparative).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parents[1]
FIN = ROOT / "frontend" / "public" / "data" / "financials"
OUT = FIN / "nav_latest.json"


def _try_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def nav_from_balance_csv(path: Path) -> float | None:
    best_date: str | None = None
    first_nav: float | None = None
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            concept = (row.get("concept") or "").replace(":", "").strip()
            if concept != "NetAssetValuePerShare":
                continue
            fd = (row.get("filing_date") or "").strip()
            if not fd:
                continue
            val = _try_float((row.get("value") or "").strip())
            if val is None:
                continue
            if best_date is None or fd > best_date:
                best_date = fd
                first_nav = val
            elif fd == best_date:
                pass
    return first_nav


def main() -> None:
    if not FIN.is_dir():
        print(f"Missing {FIN}")
        return

    nav_per_share: dict[str, float] = {}
    for path in sorted(FIN.glob("*_balance_sheet.csv")):
        stem = path.stem
        if not stem.endswith("_balance_sheet"):
            continue
        ticker = stem[: -len("_balance_sheet")].upper()
        n = nav_from_balance_csv(path)
        if n is not None:
            nav_per_share[ticker] = n

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nav_per_share": nav_per_share,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({len(nav_per_share)} tickers)")


if __name__ == "__main__":
    main()
