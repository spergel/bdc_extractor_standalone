#!/usr/bin/env python3
"""
Post-processing script to standardize newly extracted investment data.
Run this after llm_table_scraper.py to ensure data quality.

Usage:
    python post_process_extraction.py --file output/ARCC_investments_2025-11-06.csv
    python post_process_extraction.py --directory output/
"""

import csv
import argparse
import logging
import re
from pathlib import Path
from typing import List, Tuple
from standardization_rules import (
    standardize_industry,
    standardize_investment_type,
    create_reference_rate_mapping,
    standardize_reference_rate,
    clean_spread,
    clean_company_name,
    normalize_industry,
    ALLOWED_INDUSTRIES,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

_MONETARY_FIELDS = (
    "principal_amount",
    "amortized_cost",
    "fair_value",
    "cost",
    "commitment_limit",
    "undrawn_commitment",
)


def _parse_float_safe(value: str) -> float:
    if value is None:
        return 0.0
    s = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fmt_numeric_like_existing(value: float) -> str:
    """Preserve integer-looking fields as integers after transformations."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def post_process_csv(input_file: Path, output_file: Path = None) -> Tuple[int, int, int]:
    """
    Post-process a CSV file to standardize industries, investment types, and reference rates.
    
    Args:
        input_file: Path to input CSV
        output_file: Path to output CSV (default: overwrite input)
    
    Returns:
        Tuple of (rows_processed, rows_changed, errors)
    """
    if not output_file:
        output_file = input_file
    
    # Create mapping dictionaries
    ref_rate_mapping = create_reference_rate_mapping()
    
    rows = []
    rows_changed = 0
    errors = 0
    rows_dropped = 0
    
    try:
        # Read the file
        with open(input_file, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            
            if not fieldnames:
                logger.error(f"No headers found in {input_file}")
                return 0, 0, 1

            # Ensure we have a data quality flag column
            if 'data_quality_flags' not in fieldnames:
                fieldnames = fieldnames + ['data_quality_flags']

            # Infer ticker from filename for ticker-specific company name cleanup
            file_ticker = None
            stem = input_file.stem
            if stem.startswith("custom_scraper_"):
                parts = stem.split("_")
                file_ticker = parts[2] if len(parts) >= 3 else None
            elif stem.startswith("_investments_") or "_investments_" in stem:
                file_ticker = stem.split("_investments_")[0].strip("_")
            elif re.match(r"^[A-Z]{2,5}_investments_", stem, re.I):
                file_ticker = stem.split("_")[0]
            
            for row in reader:
                original_row = row.copy()

                # Collect per-row data quality issues
                issues = []
                
                # Standardize industry
                if 'industry' in row:
                    original_industry = row['industry']
                    standardized_industry = standardize_industry(original_industry)
                    row['industry'] = standardized_industry
                    if original_industry != standardized_industry:
                        rows_changed += 1
                
                # Standardize investment_type
                if 'investment_type' in row:
                    original_type = row['investment_type']
                    standardized_type = standardize_investment_type(original_type)
                    row['investment_type'] = standardized_type
                    if original_type != standardized_type:
                        rows_changed += 1
                
                # Standardize reference_rate
                if 'reference_rate' in row:
                    original_rate = row['reference_rate']
                    standardized_rate = standardize_reference_rate(original_rate, ref_rate_mapping)
                    row['reference_rate'] = standardized_rate
                    if original_rate != standardized_rate:
                        rows_changed += 1
                
                # Clean spread
                if 'spread' in row:
                    original_spread = row['spread']
                    cleaned_spread = clean_spread(original_spread)
                    row['spread'] = cleaned_spread
                    if original_spread != cleaned_spread:
                        rows_changed += 1
                
                # Clean company_name (with optional ticker for BDC-specific cleanup)
                if 'company_name' in row:
                    original_name = row['company_name']
                    ticker = (row.get('ticker') or '').strip() or file_ticker
                    cleaned_name, extracted_industry = clean_company_name(original_name, ticker=ticker or None)
                    row['company_name'] = cleaned_name
                    if original_name != cleaned_name:
                        rows_changed += 1
                    # Use industry from stripped title or company name (e.g. Advanced Aircrew → Aerospace & Defense) when industry is empty or Other
                    current_ind = (row.get('industry') or '').strip()
                    if extracted_industry and (not current_ind or current_ind == 'Other'):
                        canonical = standardize_industry(extracted_industry)
                        if canonical:
                            row['industry'] = canonical
                            rows_changed += 1
                    # If still empty/Other, infer from company name keywords (e.g. "Forescout Technologies" → Software & Technology)
                    if 'industry' in row:
                        current_ind = (row.get('industry') or '').strip()
                        if (not current_ind or current_ind == 'Other') and cleaned_name and not extracted_industry:
                            hint = normalize_industry(cleaned_name)
                            if hint and hint in ALLOWED_INDUSTRIES and hint != 'Other':
                                row['industry'] = hint
                                rows_changed += 1

                # --- Simple numeric sanity checks / flags ---
                try:
                    fv = float(row.get('fair_value', '') or 0)
                    principal = float(row.get('principal_amount', '') or 0)
                    cost = float(row.get('cost', '') or 0)
                except ValueError:
                    fv = principal = cost = 0

                ticker = (row.get('ticker') or '').strip().upper() or (file_ticker or '').strip().upper()

                # CGBD: some historical rows are 1000x too large (e.g. 100,000,000 vs expected 100,000 in "thousands" units).
                # If a row has impossible position sizes, scale all monetary fields down by 1000.
                if ticker == "CGBD":
                    money_vals = [_parse_float_safe(row.get(k, "")) for k in _MONETARY_FIELDS]
                    max_money = max(money_vals) if money_vals else 0.0
                    if max_money >= 2_000_000:
                        for k in _MONETARY_FIELDS:
                            v = _parse_float_safe(row.get(k, ""))
                            if v:
                                row[k] = _fmt_numeric_like_existing(v / 1000.0)
                        rows_changed += 1
                        issues.append("scaled_down_1000x")

                    # Drop tiny geometric tail artifacts from the known bad CGBD block.
                    company = (row.get("company_name") or "").strip()
                    filing_date = (row.get("filing_date") or "").strip()
                    inv_type = (row.get("investment_type") or "").strip()
                    fair_v = _parse_float_safe(row.get("fair_value", ""))
                    principal_v = _parse_float_safe(row.get("principal_amount", ""))
                    if (
                        filing_date == "2020-05-05"
                        and company == "Zurn Water Solutions LLC"
                        and inv_type == "Preferred Equity"
                        and principal_v == 0
                        and fair_v > 0
                        and fair_v <= 2000
                    ):
                        rows_dropped += 1
                        continue

                # GECC: some rows carry principal in absolute dollars while cost/FV are in thousands.
                # Example pattern: principal=65000, cost=64.36, fair_value=64.36 (ratio ~1000x).
                if ticker == "GECC":
                    principal_v = _parse_float_safe(row.get("principal_amount", ""))
                    cost_v = _parse_float_safe(row.get("cost", ""))
                    fair_v = _parse_float_safe(row.get("fair_value", ""))
                    cmp_v = max(cost_v, fair_v)
                    if principal_v > 0 and cmp_v > 0:
                        ratio = principal_v / cmp_v
                        if 800 <= ratio <= 1200:
                            row["principal_amount"] = _fmt_numeric_like_existing(principal_v / 1000.0)
                            rows_changed += 1
                            issues.append("principal_scaled_down_1000x")
                            principal = _parse_float_safe(row.get("principal_amount", ""))

                    # Drop GECC placeholder/category rows that carry no position values.
                    inv_type_lower = (row.get("investment_type") or "").strip().lower()
                    has_position_values = any(
                        _parse_float_safe(row.get(k, "")) > 0
                        for k in ("principal_amount", "amortized_cost", "fair_value", "cost", "commitment_limit", "undrawn_commitment")
                    ) or _parse_float_safe(row.get("shares", "")) > 0
                    if inv_type_lower == "other" and not has_position_values:
                        rows_dropped += 1
                        continue

                # Flag obviously tiny cost relative to principal/FV (e.g. 13 vs 13,242)
                if cost > 0 and (principal > 0 or fv > 0):
                    denom = principal if principal > 0 else fv
                    # If cost is less than 5% of principal/FV but principal/FV are reasonably large,
                    # it's almost certainly a bad scrape like HFZ (13 vs 13,242).
                    if denom >= 1_000 and cost / denom < 0.05:
                        issues.append("cost_suspicious_vs_size")

                # Flag obviously tiny fair value relative to principal/cost.
                # This is where we often see equity/warrant commas/scale wrong,
                # e.g. principal 4,040 vs FV 4.
                fv_denom = 0.0
                if principal > 0:
                    fv_denom = principal
                elif cost > 0:
                    fv_denom = cost

                if fv > 0 and fv_denom >= 1_000:
                    ratio = fv / fv_denom
                    # If FV is less than 5% of principal/cost on a reasonably sized position,
                    # it's almost certainly a scaling / comma error rather than a true 95% loss.
                    if ratio < 0.05:
                        issues.append("fair_value_suspicious_vs_size")

                # Flag probable type mismatches hinted by company_name suffixes.
                name_lower = (row.get('company_name') or '').lower()
                type_lower = (row.get('investment_type') or '').lower()

                # Examples:
                #  - "LVF Holdings Inc (Revolver)" but type == "First Lien"
                #  - "MacQueen Equipment LLC (Delayed Draw)" but type == "First Lien"
                #  - "MC Asset Management (Corporate) LLC" but type == "First Lien"
                if "revolver" in name_lower and "revolver" not in type_lower:
                    issues.append("type_hint_revolver_from_name")
                if "delayed draw" in name_lower and "delayed draw" not in type_lower:
                    issues.append("type_hint_delayed_draw_from_name")
                if "preferred equity" in name_lower and "preferred" not in type_lower:
                    issues.append("type_hint_pref_equity_from_name")
                if "corporate" in name_lower and "corporate" not in type_lower:
                    issues.append("type_hint_corporate_from_name")

                # You can extend with more checks later (negative FV, etc.)

                # Persist issues into a pipe-separated flag field
                if issues:
                    existing = row.get('data_quality_flags', '') or ''
                    merged = set(filter(None, [s.strip() for s in existing.split('|')])) | set(issues)
                    row['data_quality_flags'] = '|'.join(sorted(merged))
                else:
                    # Preserve existing value or keep empty
                    row['data_quality_flags'] = row.get('data_quality_flags', '') or ''
                
                # Drop non-holding artifacts after name cleanup (totals/headers/percent-only rows)
                if not (row.get('company_name') or '').strip():
                    rows_dropped += 1
                    continue

                # Drop data-poor summary rows: fair_value populated but no other meaningful field.
                # These are portfolio-total or section-subtotal rows that DSPy sometimes emits.
                _MEANINGFUL = (
                    'principal_amount', 'amortized_cost', 'maturity_date',
                    'reference_rate', 'cash_rate', 'pik_rate', 'spread',
                    'shares', 'cost', 'acquisition_date',
                )
                if (
                    (row.get('fair_value') or '').strip()
                    and not any((row.get(f) or '').strip() for f in _MEANINGFUL)
                    and (row.get('data_quality_flags') or '').startswith('dspy')
                ):
                    rows_dropped += 1
                    continue

                rows.append(row)

        # Cross-period dedup for DSPy-extracted files.
        # 10-Q filings include a comparative prior-year-end SOI table. If table detection
        # didn't filter it, we get ~2× rows.  Apply the same dedup the scraper uses, plus
        # a fair-value-based pass for equity/fund positions that have no maturity date.
        def _field_count(r: dict) -> int:
            return sum(1 for v in r.values() if (v or '').strip())

        is_dspy_file = any(
            (r.get('data_quality_flags') or '').startswith('dspy') for r in rows
        )
        if is_dspy_file and rows:
            from collections import defaultdict as _dd

            def _mat_ym(s: str) -> str:
                s = (s or '').strip()
                return s[:7] if len(s) >= 7 else s

            def _parse_f(s: str) -> float:
                try:
                    return float((s or '').strip() or '0')
                except ValueError:
                    return 0.0

            # Pass 1: dedup by (company, type, maturity_ym) + principal proximity (debt positions)
            _seen_pri: dict = _dd(list)
            deduped_rows = []
            for r in rows:
                co = (r.get('company_name') or '').strip().lower()
                tp = (r.get('investment_type') or '').strip().lower()
                mat = _mat_ym(r.get('maturity_date', ''))
                if not mat:
                    deduped_rows.append(r)
                    continue
                key = (co, tp, mat)
                pri = _parse_f(r.get('principal_amount', ''))
                is_dup = False
                for seen_pri in _seen_pri[key]:
                    if seen_pri == 0 and pri == 0:
                        is_dup = True
                        break
                    if seen_pri > 0 and pri > 0 and min(seen_pri, pri) / max(seen_pri, pri) >= 0.70:
                        is_dup = True
                        break
                if is_dup:
                    rows_dropped += 1
                else:
                    _seen_pri[key].append(pri)
                    deduped_rows.append(r)

            # Pass 2: for no-maturity positions (equity/fund), dedup by (company, type) + FV proximity.
            # Threshold 0.75: if two rows share the same company+type and FVs are within 25%,
            # treat the second occurrence as a prior-period duplicate and drop it.
            _seen_fv: dict = _dd(list)
            final_rows = []
            for r in deduped_rows:
                mat = _mat_ym(r.get('maturity_date', ''))
                if mat:
                    final_rows.append(r)
                    continue
                co = (r.get('company_name') or '').strip().lower()
                tp = (r.get('investment_type') or '').strip().lower()
                key = (co, tp)
                fv = _parse_f(r.get('fair_value', ''))
                is_dup = False
                if fv > 0:
                    for seen_fv in _seen_fv[key]:
                        if min(seen_fv, fv) / max(seen_fv, fv) >= 0.75:
                            is_dup = True
                            break
                if is_dup:
                    rows_dropped += 1
                else:
                    if fv > 0:
                        _seen_fv[key].append(fv)
                    final_rows.append(r)
            rows = final_rows

        # XBRL blank-FV dedup: some XBRL filers (e.g. RAND) emit two contexts per
        # position — one with fair_value populated and one with fair_value blank.
        # Drop blank-FV rows when a non-blank-FV row exists for the same (company, type).
        # Guard genuine blank-FV-only positions (e.g. partnership interests) by only
        # dropping when there is at least one sibling row with a real FV.
        if not is_dspy_file and rows:
            # Build set of (company, type) keys that have at least one non-blank FV
            has_fv: set = set()
            for r in rows:
                if (r.get('fair_value') or '').strip():
                    co = (r.get('company_name') or '').strip().lower()
                    t = (r.get('investment_type') or '').strip().lower()
                    has_fv.add((co, t))
            if has_fv:
                deduped_xbrl = []
                for r in rows:
                    fv = (r.get('fair_value') or '').strip()
                    if fv:
                        deduped_xbrl.append(r)
                    else:
                        co = (r.get('company_name') or '').strip().lower()
                        t = (r.get('investment_type') or '').strip().lower()
                        if (co, t) in has_fv:
                            rows_dropped += 1  # Blank-FV shadow of a richer row
                        else:
                            deduped_xbrl.append(r)  # No richer version — keep it
                rows = deduped_xbrl

        # XBRL hierarchical rollup filter: some BDCs (e.g. OXSQ) emit XBRL member
        # strings that form a hierarchy — individual positions AND their subtotals.
        # E.g. "Senior Secured Notes - Business Services - Access CIG" (individual) and
        # "Senior Secured Notes - Business Services" (subtotal).  Drop any row whose
        # company_name is a strict prefix of another row's name (i.e. name + " - ...")
        # because those are aggregate/subtotal nodes, not individual positions.
        # Also drop rows whose name contains " - Total " which flags rollup total nodes
        # that use a different branch name (e.g. "CLO - Total Structured Finance").
        if not is_dspy_file and rows:
            all_names = {(r.get('company_name') or '').strip() for r in rows}
            _ROLLUP_TOTAL_RE = re.compile(r'\s-\s+Total\b', re.IGNORECASE)
            filtered_rollup = []
            rollup_dropped = 0
            for r in rows:
                name = (r.get('company_name') or '').strip()
                prefix = name + ' - '
                if any(n.startswith(prefix) for n in all_names if n != name):
                    rollup_dropped += 1
                elif _ROLLUP_TOTAL_RE.search(name):
                    rollup_dropped += 1
                else:
                    filtered_rollup.append(r)
            if rollup_dropped:
                logger.debug("Dropped %d XBRL rollup/subtotal rows", rollup_dropped)
                rows_dropped += rollup_dropped
                rows = filtered_rollup

        # FSK XBRL dedup: XBRL emits two contexts per position — one with full
        # financials and one sparse (only fair_value).  Company name cleanup above
        # normalises "Roemanu LLC ABF Equity" → "Roemanu LLC".  The sparse context
        # often maps to a different investment_type than the rich one (e.g. "Other"
        # vs "Other Equity", "Preferred Equity" vs "First Lien"), so we key on
        # (company, fair_value) only and drop any row with score ≤ 5 that duplicates
        # a richer row — guarding against accidentally merging genuine distinct tranches.
        is_fsk_xbrl = (file_ticker == 'FSK') and not is_dspy_file
        if is_fsk_xbrl and rows:
            seen_fsk: dict = {}
            deduped_fsk = []
            for r in rows:
                co = (r.get('company_name') or '').strip().lower()
                fv = (r.get('fair_value') or '').strip()
                key = (co, fv)
                score = _field_count(r)
                existing = seen_fsk.get(key)
                if existing is None:
                    seen_fsk[key] = r
                    deduped_fsk.append(r)
                elif score > _field_count(existing):
                    # Current row is richer — replace sparse shadow row
                    if _field_count(existing) <= 5:
                        idx = deduped_fsk.index(existing)
                        deduped_fsk[idx] = r
                        seen_fsk[key] = r
                        rows_dropped += 1
                    else:
                        # Both rows are rich — likely genuine distinct tranches; keep both
                        deduped_fsk.append(r)
                elif score <= 5:
                    # Current row is sparse and a richer version already exists — drop it
                    rows_dropped += 1
                else:
                    # Both rows are rich — likely genuine distinct tranches; keep both
                    deduped_fsk.append(r)
            rows = deduped_fsk

        # Write back
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        logger.info(
            f"✓ Processed {input_file.name}: {len(rows)} rows, {rows_changed} changes"
            + (f", {rows_dropped} dropped" if rows_dropped else "")
        )
        return len(rows), rows_changed, errors
        
    except Exception as e:
        logger.error(f"✗ Error processing {input_file}: {e}")
        return 0, 0, 1

def process_directory(directory: Path, pattern: str = "*_investments_*.csv") -> None:
    """
    Process all CSV files in a directory.
    When using default pattern, also includes custom_scraper_*.csv (BCIC, etc.).
    Args:
        directory: Directory containing CSV files
        pattern: Glob pattern for files to process (default: *_investments_*.csv)
    """
    csv_files = list(directory.glob(pattern))
    if pattern == "*_investments_*.csv":
        csv_files = list(dict.fromkeys(csv_files + list(directory.glob("custom_scraper_*.csv"))))

    if not csv_files:
        logger.warning(f"No files matching '{pattern}' found in {directory}")
        return
    
    logger.info(f"Found {len(csv_files)} files to process")
    
    total_rows = 0
    total_changes = 0
    total_errors = 0
    
    for csv_file in csv_files:
        rows, changes, errors = post_process_csv(csv_file)
        total_rows += rows
        total_changes += changes
        total_errors += errors
    
    logger.info(f"\n{'='*60}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Files processed: {len(csv_files)}")
    logger.info(f"Total rows: {total_rows:,}")
    logger.info(f"Total changes: {total_changes:,}")
    logger.info(f"Errors: {total_errors}")

def main():
    parser = argparse.ArgumentParser(
        description="Post-process extracted investment data to standardize values"
    )
    parser.add_argument('--file', type=Path, help='Single CSV file to process')
    parser.add_argument('--directory', type=Path, help='Directory of CSV files to process')
    parser.add_argument('--pattern', default='*_investments_*.csv', 
                       help='Glob pattern for files (default: *_investments_*.csv)')
    
    args = parser.parse_args()
    
    if not args.file and not args.directory:
        parser.error("Must specify either --file or --directory")
    
    if args.file:
        if not args.file.exists():
            logger.error(f"File not found: {args.file}")
            return 1
        
        rows, changes, errors = post_process_csv(args.file)
        
        if errors == 0:
            logger.info(f"\n✓ Success: Processed {rows:,} rows with {changes:,} standardizations")
            return 0
        else:
            logger.error(f"\n✗ Failed with {errors} errors")
            return 1
    
    elif args.directory:
        if not args.directory.exists():
            logger.error(f"Directory not found: {args.directory}")
            return 1
        
        process_directory(args.directory, args.pattern)
        return 0

if __name__ == "__main__":
    exit(main())
