#!/usr/bin/env python3
"""
Fuzzy entity resolution for portfolio company names across BDCs.
Run after consolidate_investments. Reads consolidated CSVs, assigns stable company_id
to each company, writes companies_index.json and adds company_id to all investment CSVs.
"""

import csv
import hashlib
import json
import logging
import re
from collections import defaultdict, Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any
from datetime import datetime, timezone

from rapidfuzz import fuzz

# Allow running from repo root: add src to path for standardization_rules
import sys
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.processing.standardization_rules import (
    clean_company_name,
    normalize_industry,
    normalize_legal_entity_for_clustering,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

COMPANY_ID_PREFIX = "co_"
SIMILARITY_THRESHOLD = 88  # fuzz.ratio 0-100; merge if >= 88
BLOCK_MIN_LENGTH = 2  # ignore blocks with single name for matching

# Borrowers listed under multiple Opco/Topco (or similar) shells → one canonical for exposure tracking.
_MANUAL_CANONICAL_GROUPS: List[Tuple[Set[str], str]] = [
    (
        {
            "ADS Group Opco LLC",
            "ADS Group Opco",
            "ADS Group Topco LLC",
            "ADS Group Topco",
        },
        "ADS",
    ),
]


def apply_manual_canonical_merges(raw_to_canonical: Dict[str, str]) -> Dict[str, str]:
    """Remap clustered names that should roll up to a single portfolio company."""
    out = dict(raw_to_canonical)
    for variants, target in _MANUAL_CANONICAL_GROUPS:
        for k in list(out.keys()):
            if out[k] in variants or k in variants:
                out[k] = target
    return out


def _block_key(normalized: str) -> str:
    """Block key from normalized company name (first token, lowercased)."""
    if not normalized or not normalized.strip():
        return "__empty__"
    tokens = re.split(r"[\s,]+", normalized.strip().lower(), maxsplit=1)
    return tokens[0] if tokens else "__empty__"


def _stable_id(canonical_name: str) -> str:
    """Stable company_id from canonical name (deterministic hash)."""
    key = canonical_name.strip().lower()
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{COMPANY_ID_PREFIX}{h}"


def _cluster_by_similarity(names: List[str], threshold: int) -> List[Set[str]]:
    """Group a list of normalized names into clusters by string similarity (union-find)."""
    if not names:
        return []
    names = list(set(names))
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if a == b:
                continue
            # Use token_sort_ratio to ignore word order (e.g. "X and Y LLC" vs "Y and X LLC")
            score = fuzz.token_sort_ratio(a, b)
            if score >= threshold:
                union(a, b)

    clusters: Dict[str, Set[str]] = defaultdict(set)
    for n in names:
        clusters[find(n)].add(n)
    return list(clusters.values())


def collect_all_investment_data(
    investments_dir: Path,
) -> Tuple[Dict[str, Tuple[List[str], List[Dict]]], Dict[str, str]]:
    """
    Load all investment rows from consolidated CSVs (TICKER.csv only; they contain all periods).
    Return (path -> (fieldnames, rows), raw_to_normalized).
    """
    path_to_data: Dict[str, Tuple[List[str], List[Dict]]] = {}
    raw_to_normalized: Dict[str, str] = {}
    for path in sorted(investments_dir.glob("*.csv")):
        if path.name.endswith(".BEFORE.csv"):
            continue
        if path.is_file() and path.parent == investments_dir:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames or [])
                    rows = list(reader)
                    for row in rows:
                        raw = (row.get("company_name") or "").strip()
                        if raw and raw not in raw_to_normalized:
                            raw_to_normalized[raw] = clean_company_name(raw)[0]
                    path_to_data[str(path)] = (fieldnames, rows)
            except Exception as e:
                logger.warning("Failed to read %s: %s", path, e)
    return path_to_data, raw_to_normalized


def build_name_to_cluster(
    raw_to_normalized: Dict[str, str],
) -> Dict[str, str]:
    """
    Map each raw and normalized name to a canonical key (one per cluster).
    Returns: name -> canonical_key (normalized representative of cluster).
    First merges names that differ only by legal suffix (Inc./Ltd./LLC/Corp. etc.), then fuzz clusters the rest.
    """
    normalized_names = [n for n in set(raw_to_normalized.values()) if n]
    name_to_canonical: Dict[str, str] = {}

    # 1) Same legal base: "Labvantage Solutions Inc." and "Labvantage Solutions Ltd." -> one cluster.
    #    Use lowercased base key so "ArborWorks" and "ARBORWORKS LLC" merge (same company).
    base_to_normals: Dict[str, List[str]] = defaultdict(list)
    for n in normalized_names:
        base = normalize_legal_entity_for_clustering(n).strip().lower() or "__empty__"
        base_to_normals[base].append(n)
    for base, normals in base_to_normals.items():
        if len(normals) > 1:
            canonical = max(normals, key=len)
            for n in normals:
                name_to_canonical[n] = canonical

    # 2) Names not yet assigned (single in their base) go through block + fuzz clustering
    single_normals = [n for n in normalized_names if n not in name_to_canonical]
    block_to_names: Dict[str, List[str]] = defaultdict(list)
    for n in single_normals:
        block_to_names[_block_key(n)].append(n)
    for block, block_names in block_to_names.items():
        if len(block_names) < BLOCK_MIN_LENGTH:
            for n in block_names:
                name_to_canonical[n] = n
            continue
        clusters = _cluster_by_similarity(block_names, SIMILARITY_THRESHOLD)
        for cluster in clusters:
            canonical = max(cluster, key=len)
            for n in cluster:
                name_to_canonical[n] = canonical

    # map raw names to canonical (via normalized)
    raw_to_canonical: Dict[str, str] = {}
    for raw, norm in raw_to_normalized.items():
        if norm and norm in name_to_canonical:
            raw_to_canonical[raw] = name_to_canonical[norm]
        else:
            raw_to_canonical[raw] = norm or raw
    return raw_to_canonical


def build_mapping_and_index(
    raw_to_canonical: Dict[str, str],
) -> Tuple[Dict[str, str], List[Dict]]:
    """
    Build company_id for each canonical name and companies index list.
    Returns (raw_or_canonical -> company_id, companies_index_list).
    """
    canonical_to_id: Dict[str, str] = {}
    canonical_to_variants: Dict[str, Set[str]] = defaultdict(set)
    for raw, canonical in raw_to_canonical.items():
        if canonical not in canonical_to_id:
            canonical_to_id[canonical] = _stable_id(canonical)
        canonical_to_variants[canonical].add(raw)
    name_to_id: Dict[str, str] = {}
    for raw, canonical in raw_to_canonical.items():
        cid = canonical_to_id[canonical]
        name_to_id[raw] = cid
        name_to_id[canonical] = cid
    companies_index = [
        {
            "company_id": canonical_to_id[c],
            "canonical_name": c,
            "name_variants": sorted(canonical_to_variants[c]),
        }
        for c in sorted(canonical_to_id.keys())
    ]
    return name_to_id, companies_index


def run_resolution(
    investments_dir: Path,
    data_dir: Path,
) -> None:
    """
    Run company resolution: load consolidated CSVs, resolve names, write companies_index.json
    and add company_id to each CSV.
    """
    investments_dir = Path(investments_dir)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Collecting company names from %s", investments_dir)
    path_to_data, raw_to_normalized = collect_all_investment_data(investments_dir)
    total_rows = sum(len(rows) for _, rows in path_to_data.values())
    logger.info("Collected %d rows, %d unique raw company names", total_rows, len(raw_to_normalized))

    raw_to_canonical = build_name_to_cluster(raw_to_normalized)
    raw_to_canonical = apply_manual_canonical_merges(raw_to_canonical)
    name_to_id, companies_index = build_mapping_and_index(raw_to_canonical)
    logger.info("Resolved to %d canonical companies", len(companies_index))

    index_path = data_dir / "companies_index.json"
    index_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "companies": companies_index,
    }
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_payload, f, indent=2)
    logger.info("Wrote %s", index_path)

    def _company_id_for_row(row: Dict, name_to_id: Dict[str, str], raw_to_normalized: Dict[str, str]) -> str:
        raw = (row.get("company_name") or "").strip()
        if not raw:
            return ""
        cid = name_to_id.get(raw)
        if cid:
            return cid
        norm = raw_to_normalized.get(raw, raw)
        return name_to_id.get(norm, _stable_id(norm))

    # Add company_id to each row and rewrite main TICKER.csv files
    for path_str, (fieldnames, path_rows) in path_to_data.items():
        path = Path(path_str)
        if not path_rows:
            continue
        for row in path_rows:
            row["company_id"] = _company_id_for_row(row, name_to_id, raw_to_normalized)
        out_fieldnames = list(fieldnames)
        if "company_id" not in out_fieldnames:
            idx = next((i for i, k in enumerate(out_fieldnames) if k == "company_name"), 0)
            out_fieldnames.insert(idx + 1, "company_id")
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
                writer.writeheader()
                for r in path_rows:
                    writer.writerow({k: r.get(k, "") for k in out_fieldnames})
            logger.info("Updated %s with company_id", path.name)
        except Exception as e:
            logger.warning("Failed to write %s: %s", path, e)

    # Also update per-period CSVs under TICKER/YYYY-MM-DD.csv
    for ticker_dir in investments_dir.iterdir():
        if not ticker_dir.is_dir():
            continue
        for period_path in ticker_dir.glob("*.csv"):
            if period_path.name.endswith(".BEFORE.csv"):
                continue
            try:
                with open(period_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames or [])
                    if "company_id" in fieldnames:
                        continue
                    rows_per = list(reader)
                idx = next((i for i, k in enumerate(fieldnames) if k == "company_name"), 0)
                fieldnames.insert(idx + 1, "company_id")
                for row in rows_per:
                    raw = (row.get("company_name") or "").strip()
                    row["company_id"] = name_to_id.get(raw, "")
                    if not row["company_id"] and raw:
                        row["company_id"] = _stable_id(raw_to_normalized.get(raw, raw))
                with open(period_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows_per)
                logger.info("Updated %s with company_id", period_path.relative_to(investments_dir))
            except Exception as e:
                logger.warning("Failed to update %s: %s", period_path, e)

    # Build company_exposures.csv keyed by company_id (cross-BDC aggregation)
    _write_company_exposures(path_to_data, companies_index, data_dir)
    # Build company_detail.json (per-BDC exposure, maturity buckets, investment type breakdown)
    _write_company_detail(path_to_data, data_dir)


def _parse_fair_value(val: Any) -> float:
    """Parse fair_value from CSV (may be string with commas or number)."""
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _safe_fair_value(row: Dict) -> float:
    """Return the best available fair_value for a row, with unit sanity checks.

    BDC investment CSVs store values in thousands. Occasionally an extraction error
    writes a value in raw dollars (1000x too large). Detect this by comparing
    fair_value to amortized_cost: if the ratio exceeds 500 the value is almost
    certainly in the wrong unit, so fall back to amortized_cost. Also fall back when
    the data_quality_flags field explicitly marks fair_value as suspicious.
    """
    flags = str(row.get("data_quality_flags") or "").lower()
    fv = _parse_fair_value(row.get("fair_value") or row.get("fair_value_thousands"))
    ac = _parse_fair_value(row.get("amortized_cost"))

    if fv <= 0:
        return ac if ac > 0 else 0.0

    # Explicit quality flag: prefer amortized_cost when available
    if "fair_value_suspicious" in flags and ac > 0:
        return ac

    # Implicit ratio check: fair_value >> amortized_cost => likely wrong units
    if ac > 0 and fv / ac > 500:
        return ac

    return fv


def _load_profile_industries(data_dir: Path) -> Dict[str, str]:
    """Load company_id -> industry from company_profiles.json (when present)."""
    path = data_dir / "company_profiles.json"
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cid, prof in (data.get("profiles") or {}).items():
            ind = (prof.get("industry") or "").strip()
            if ind:
                out[cid] = ind
    except Exception as e:
        logger.warning("Could not load company_profiles for industry override: %s", e)
    return out


def _write_company_exposures(
    path_to_data: Dict[str, Tuple[List[str], List[Dict]]],
    companies_index: List[Dict],
    data_dir: Path,
) -> None:
    """Aggregate holdings by company_id and write company_exposures.csv.
    Uses latest filing per ticker only so total_exposure_millions matches company_detail breakdown."""
    cid_to_canonical = {c["company_id"]: c["canonical_name"] for c in companies_index}
    profile_industries = _load_profile_industries(data_dir)
    latest_per_ticker = _latest_filing_per_ticker(path_to_data)
    # Aggregate by company_id: set of tickers, list of (fair_value, industry, investment_type, interest)
    agg: Dict[str, Dict] = defaultdict(
        lambda: {
            "tickers": set(),
            "fair_value_sum": 0.0,
            "industries": [],
            "investment_types": [],
            "rates": [],
        }
    )
    for _path_str, (_fieldnames, path_rows) in path_to_data.items():
        for row in path_rows:
            cid = (row.get("company_id") or "").strip()
            if not cid:
                continue
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            # Only count latest period per BDC so total matches company_detail (by_bdc sum)
            if row.get("filing_date") != latest_per_ticker.get(ticker):
                continue
            if ticker:
                agg[cid]["tickers"].add(ticker)
            fv = _safe_fair_value(row)
            agg[cid]["fair_value_sum"] += fv
            raw_ind = (row.get("industry") or row.get("industry_clean") or "").strip()
            ind = normalize_industry(raw_ind)
            if ind:
                agg[cid]["industries"].append(ind)
            it = (row.get("investment_type") or row.get("investment_type_standardized") or "").strip()
            if it:
                agg[cid]["investment_types"].append(it)
            rate_raw = row.get("total_interest_rate") or row.get("interest_rate") or row.get("spread")
            if rate_raw is not None and rate_raw != "":
                try:
                    r = float(str(rate_raw).replace("%", "").replace(",", ""))
                    if 0 <= r <= 100:
                        agg[cid]["rates"].append(r)
                except ValueError:
                    pass

    rows_out = []
    for cid, data in agg.items():
        canonical = cid_to_canonical.get(cid, "")
        num_bdcs = len(data["tickers"])
        bdcs_str = ",".join(sorted(data["tickers"]))
        # fair_value in source is typically in thousands; convert to millions
        total_millions = data["fair_value_sum"] / 1000.0
        primary_industry = ""
        if cid in profile_industries:
            primary_industry = normalize_industry(profile_industries[cid]) or ""
        if not primary_industry and data["industries"]:
            primary_industry = Counter(data["industries"]).most_common(1)[0][0]
        most_common_type = ""
        if data["investment_types"]:
            most_common_type = Counter(data["investment_types"]).most_common(1)[0][0]
        avg_rate = 0.0
        if data["rates"]:
            avg_rate = sum(data["rates"]) / len(data["rates"])
        rows_out.append({
            "company_id": cid,
            "company_name": canonical,
            "num_bdcs_invested": num_bdcs,
            "bdcs_invested": bdcs_str,
            "total_exposure_millions": round(total_millions, 2),
            "primary_industry": primary_industry,
            "avg_interest_rate": round(avg_rate, 2),
            "most_common_investment_type": most_common_type,
        })

    rows_out.sort(key=lambda r: (-r["num_bdcs_invested"], -r["total_exposure_millions"]))
    out_path = data_dir / "company_exposures.csv"
    fieldnames = [
        "company_id", "company_name", "num_bdcs_invested", "bdcs_invested",
        "total_exposure_millions", "primary_industry", "avg_interest_rate",
        "most_common_investment_type",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    logger.info("Wrote %s with %d company exposures", out_path, len(rows_out))


def _latest_filing_per_ticker(
    path_to_data: Dict[str, Tuple[List[str], List[Dict]]],
) -> Dict[str, str]:
    """Return for each ticker the latest filing_date (YYYY-MM-DD) so we use current exposure only."""
    latest: Dict[str, str] = {}
    for _path_str, (_fieldnames, path_rows) in path_to_data.items():
        for row in path_rows:
            t = (row.get("ticker") or "").strip()
            fd = (row.get("filing_date") or "").strip()
            if not t or not fd:
                continue
            if t not in latest or fd > latest[t]:
                latest[t] = fd
    return latest


def _maturity_bucket(maturity_date: Any) -> str:
    """Bucket maturity date for display: 2025, 2026, ..., 2030+, No date."""
    if not maturity_date or not str(maturity_date).strip():
        return "No date"
    s = str(maturity_date).strip()[:10]
    if len(s) >= 4 and s[:4].isdigit():
        year = int(s[:4])
        if year < 2026:
            return "Before 2026"
        if year <= 2030:
            return str(year)
        return "2030+"
    return "No date"


def _write_company_detail(
    path_to_data: Dict[str, Tuple[List[str], List[Dict]]],
    data_dir: Path,
) -> None:
    """Build company_detail.json: per-BDC exposure, maturity buckets, investment type (latest period per BDC)."""
    latest_per_ticker = _latest_filing_per_ticker(path_to_data)
    agg: Dict[str, Dict] = defaultdict(
        lambda: {
            "by_bdc": defaultdict(float),
            "by_maturity": defaultdict(float),
            "by_investment_type": defaultdict(float),
        }
    )
    for _path_str, (_fieldnames, path_rows) in path_to_data.items():
        for row in path_rows:
            cid = (row.get("company_id") or "").strip()
            if not cid:
                continue
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            if row.get("filing_date") != latest_per_ticker.get(ticker):
                continue
            fv = _safe_fair_value(row)
            if fv <= 0:
                continue
            fv_millions = fv / 1000.0
            agg[cid]["by_bdc"][ticker] += fv_millions
            bucket = _maturity_bucket(row.get("maturity_date"))
            agg[cid]["by_maturity"][bucket] += fv_millions
            it = (row.get("investment_type") or row.get("investment_type_standardized") or "").strip() or "Other"
            agg[cid]["by_investment_type"][it] += fv_millions

    out: Dict[str, Any] = {}
    for cid, data in agg.items():
        by_bdc = {t: round(v, 2) for t, v in sorted(data["by_bdc"].items())}
        maturity_order = ["Before 2026", "2026", "2027", "2028", "2029", "2030+", "No date"]
        by_maturity = dict(
            (b, round(data["by_maturity"].get(b, 0), 2))
            for b in maturity_order
            if data["by_maturity"].get(b, 0) > 0
        )
        for b, v in data["by_maturity"].items():
            if b not in maturity_order and v > 0:
                by_maturity[b] = round(v, 2)
        by_investment_type = {t: round(v, 2) for t, v in sorted(data["by_investment_type"].items())}
        out[cid] = {
            "by_bdc": by_bdc,
            "by_maturity": by_maturity,
            "by_investment_type": by_investment_type,
        }

    out_path = data_dir / "company_detail.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": out,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info("Wrote %s with %d company details", out_path, len(out))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Resolve company names to stable IDs across BDCs")
    parser.add_argument(
        "--investments-dir",
        default="frontend/public/data/investments",
        help="Directory containing TICKER.csv and TICKER/ subdirs",
    )
    parser.add_argument(
        "--data-dir",
        default="frontend/public/data",
        help="Directory to write companies_index.json",
    )
    args = parser.parse_args()
    run_resolution(args.investments_dir, args.data_dir)


if __name__ == "__main__":
    main()
