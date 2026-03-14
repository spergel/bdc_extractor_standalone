#!/usr/bin/env python3
"""
XBRL-based Schedule of Investments Extractor for BDC filings.

Parses XBRL instance documents from SEC 10-Q/10-K filings to extract
investment schedule data using typed dimensions (InvestmentIdentifierAxis).
Falls back to the LLM table scraper when XBRL SOI data is not available.

Output:
    CSV files are written to output_dir as {ticker}_investments_{filing_date}.csv
    (same name pattern as the LLM table scraper). To keep XBRL output separate,
    use e.g. --output-dir output/xbrl.

Usage:
    python xbrl_investment_extractor.py --ticker CSWC --years-back 1
    python xbrl_investment_extractor.py --ticker ARCC --years-back 2 --force
    python xbrl_investment_extractor.py --ticker CSWC --years-back 1 --debug-dir output/xbrl_debug
"""

import os
import logging
import argparse
import io
import csv
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

import requests

from sec_api_client import SECAPIClient, _sec_get

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# CSV columns matching the LLM scraper output format (+ floor_rate when present)
CSV_COLUMNS = [
    'company_name', 'investment_type', 'industry', 'cash_rate', 'pik_rate',
    'reference_rate', 'spread', 'floor_rate', 'acquisition_date', 'maturity_date',
    'principal_amount', 'amortized_cost', 'fair_value', 'percent_of_net_assets',
    'cost', 'commitment_limit', 'undrawn_commitment',
    'rate_cap', 'shares',
]

# XBRL fact tags relevant to Schedule of Investments (matched by local name, namespace-agnostic)
INVESTMENT_FACT_TAGS = {
    'investmentatfairvalue',
    'investmentownedatfairvalue',
    'investmentownedatcost',
    'investmentatcost',
    'investmentatamortizedcost',
    'investmentownedatamortizedcost',
    'investmentinterestrate',
    'investmentinterestratecash',
    'investmentinterestratecontractual',
    'investmentinterestrateeffective',
    'investmentinterestratepikinkinddividend',
    'investmentinterestratepikinkinddividendrate',
    'investmentpikrate',
    'investmentinterestratepaidincash',
    'investmentinterestratepaidinkind',
    'investmentinterestrateduringperiod',
    'investmentinterestrateterms',
    'investmentinterestratecap',
    'investmentinterestratefloor',
    'investmentinterestratespreadovervariablerate',
    'investmentvariableinterestratetype',
    'investmentvariableinterestratetypeextensibleenumeration',
    'investmentbasisspreadvariablerate',
    'investmentinterestratespread',
    'investmentownedpercentofnetassets',
    'investmentownedbalanceprincipalamount',
    'investmentownedbalancesharesorsecurities',
    'investmentownedbalanceshares',
    'investmentacquisitiondate',
    'investmentmaturitydate',
    'investmentcommitment',
    'investmentunfundedcommitment',
    'investmentunfundedcommitments',
    'investmentcommitmentunfunded',
    'investmentcommitmentfairvalue',
    'investmentcompanyfinancialcommitmenttoinvesteefutureamount',
    'investmentowneddividendrate',
    'investmentestimatedyield',
    'investmentcompanyreturnrate',
}

# Additional tags that sometimes carry investment data under different naming conventions
INVESTMENT_FACT_TAGS_EXTRA = {
    'fairvalue',
    'cost',
    'amortizedcost',
    'principalamount',
    'parvalue',
    'interestrate',
    'percentofnetassets',
    'percentageofnetassets',
    'spreadovervariablerate',
}


    # Known GICS / BDC industry names used in dimension strings (FSK, MSDL, etc.)
    # Used to disambiguate industry vs company name in comma-separated formats.
_KNOWN_INDUSTRIES = {
    # GICS-style (FSK, MSDL)
    'aerospace & defense', 'air freight & logistics', 'automobile components',
    'automobiles', 'banks', 'beverages', 'biotechnology', 'broadline retail',
    'building products', 'capital goods', 'chemicals', 'commercial services & supplies',
    'commercial & professional services', 'communications equipment',
    'construction & engineering', 'construction materials',
    'consumer discretionary distribution & retail', 'consumer durables & apparel',
    'consumer finance', 'consumer services', 'consumer staples distribution & retail',
    'containers & packaging', 'distributors', 'diversified consumer services',
    'diversified financials', 'diversified reits', 'diversified telecommunication services',
    'electric utilities', 'electrical equipment', 'electronic equipment instruments & components',
    'energy', 'energy equipment & services', 'entertainment',
    'financial services', 'food & staples retailing', 'food products',
    'food beverage & tobacco', 'gas utilities',
    'ground transportation', 'health care equipment & services',
    'health care equipment & supplies', 'health care providers & services',
    'health care technology', 'hotels restaurants & leisure',
    'household & personal products', 'household durables', 'household products',
    'independent power and renewable electricity producers',
    'industrial conglomerates', 'industrials', 'insurance',
    'interactive media & services', 'internet & direct marketing retail',
    'it services', 'leisure products', 'life sciences tools & services',
    'machinery', 'marine transportation', 'media', 'media & entertainment',
    'metals & mining', 'multi-utilities', 'oil gas & consumable fuels',
    'paper & forest products', 'passenger airlines',
    'personal care products', 'pharmaceuticals',
    'pharmaceuticals biotechnology & life sciences',
    'professional services', 'real estate', 'real estate management & development',
    'retailing', 'semiconductors & semiconductor equipment',
    'software & services', 'specialty retail',
    'tech hardware & equipment', 'technology hardware & equipment',
    'technology hardware storage & peripherals',
    'telecommunication services', 'textiles apparel & luxury goods',
    'trading companies & distributors', 'transportation',
    'transportation infrastructure', 'utilities', 'wireless telecommunication services',
    # Our canonical industries (from standardization_rules)
    'business services', 'chemicals & materials', 'consumer & retail',
    'distribution & wholesale', 'education', 'environmental services',
    'food & beverage', 'healthcare', 'hospitality & leisure',
    'software & technology', 'telecommunications', 'transportation & logistics',
}


class XBRLInvestmentExtractor:
    """
    Extract Schedule of Investments from SEC filings using XBRL data.

    Each investment is identified by a typed dimension (InvestmentIdentifierAxis)
    in the XBRL context. The dimension string packs structured info like company
    name, industry, rates, dates, etc. XBRL facts reference these contexts to
    provide numeric values (fair value, cost, principal, rates, etc.).
    """

    def __init__(self, data_dir: str = "data", output_dir: str = "output",
                 debug_dir: Optional[str] = None):
        self.data_dir = Path(data_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.debug_dir = Path(debug_dir).resolve() if debug_dir else None
        self.data_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))

    # ------------------------------------------------------------------
    # XBRL instance document discovery (reused from financial_statements_extractor)
    # ------------------------------------------------------------------

    def find_xbrl_instance_document(self, filing_result) -> Optional[str]:
        """Find the XBRL instance document URL from a filing."""
        if not filing_result.documents:
            return None

        for doc in filing_result.documents:
            fn = doc.filename.lower()
            if any(skip in fn for skip in [
                'schema', 'cal.xml', 'def.xml', 'lab.xml', 'pre.xml',
                'calculation', 'definition', 'label', 'presentation',
            ]):
                continue
            if any(pat in fn for pat in [
                '_htm.xml', 'xbrl.htm', 'xbrl.html', 'instance.xml', 'xbrl.xml', '.xml',
            ]):
                if 'schema' not in fn:
                    logger.info(f"Found XBRL instance document: {doc.filename}")
                    return doc.url
        return None

    # ------------------------------------------------------------------
    # XML helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _local_tag(tag: str) -> str:
        """Strip XML namespace prefix to get local name."""
        return tag.split('}')[-1] if '}' in tag else tag

    # ------------------------------------------------------------------
    # Core XBRL parsing
    # ------------------------------------------------------------------

    def parse_xbrl_investments(self, xbrl_content: bytes, period_end_date: str,
                               debug_out: Optional[Dict[str, Any]] = None
                               ) -> List[Dict[str, Any]]:
        """
        Parse XBRL instance document to extract Schedule of Investments.

        Uses two-pass approach:
        1. First pass: parse full XML tree to extract contexts with dimension info.
        2. Second pass: streaming iterparse to collect investment-related facts.
        3. Filter to current period only (instant date = period_end_date).
        4. Group facts by dimension string -> one row per investment.

        If debug_out is provided (e.g. {}), it will be filled with contexts,
        a sample of facts, and a sample of investment_facts_by_dim for inspection.

        Returns list of dicts, each representing one investment row.
        """
        contexts: Dict[str, Dict[str, Any]] = {}
        facts: List[Dict[str, Any]] = []

        # -- Pass 1: parse contexts with full tree (no clearing) --
        # We need the full tree for contexts because iterparse + clear() destroys
        # child elements (entity/segment/typedMember) before the parent context
        # end event fires.
        tree = ET.parse(io.BytesIO(xbrl_content))
        root = tree.getroot()

        for ctx_elem in root.iter():
            if self._local_tag(ctx_elem.tag).lower() != 'context':
                continue
            cid = ctx_elem.get('id', '')
            if not cid:
                continue
            ctx_info: Dict[str, Any] = {}
            # Walk entire subtree to find period and dimension info
            for descendant in ctx_elem.iter():
                d_local = self._local_tag(descendant.tag).lower()
                text = (descendant.text or '').strip()
                if d_local == 'instant' and text:
                    ctx_info['instant'] = text
                elif d_local == 'startdate' and text:
                    ctx_info['start'] = text
                elif d_local == 'enddate' and text:
                    ctx_info['end'] = text
                elif d_local == 'typedmember':
                    dim_attr = descendant.get('dimension', '')
                    dim_name = self._local_tag(dim_attr).lower() if dim_attr else ''
                    # Collect all inner text (SLRC sometimes splits "Category | Type | Company | ..." across elements)
                    inner_texts = []
                    for inner in descendant:
                        t = (inner.text or '').strip()
                        if t:
                            inner_texts.append(t)
                    if inner_texts:
                        combined = ' | '.join(inner_texts) if len(inner_texts) > 1 else inner_texts[0]
                        if 'investmentidentifier' in dim_name or 'investment' in dim_name:
                            ctx_info['dimension_string'] = combined
                            ctx_info['dimension_axis'] = dim_name
                        elif not ctx_info.get('dimension_string'):
                            ctx_info['dimension_string'] = combined
                            ctx_info['dimension_axis'] = dim_name
                elif d_local == 'explicitmember':
                    dim_attr = descendant.get('dimension', '')
                    member_text = text
                    if dim_attr and member_text:
                        dim_name_key = self._local_tag(dim_attr).lower()
                        member_name = self._local_tag(member_text)
                        ctx_info.setdefault('explicit_dimensions', {})[dim_name_key] = member_name
            contexts[cid] = ctx_info

        # Free the full tree - we only need contexts from here
        del root, tree

        logger.info("Parsed %d contexts (%d with dimension strings)",
                     len(contexts),
                     sum(1 for c in contexts.values() if c.get('dimension_string')))

        if debug_out is not None:
            debug_out["contexts"] = {
                cid: {k: v for k, v in c.items()}
                for cid, c in list(contexts.items())[:500]
            }
            debug_out["contexts_total"] = len(contexts)
            debug_out["contexts_with_dimension"] = sum(
                1 for c in contexts.values() if c.get("dimension_string")
            )

        # -- Pass 2: streaming iterparse for facts (memory efficient) --
        skip_tags = {'context', 'unit', 'schemaref', 'linkbaseref', 'roleref', 'arcroleref'}

        for event, elem in ET.iterparse(io.BytesIO(xbrl_content), events=('end',)):
            local = self._local_tag(elem.tag).lower()

            if local in skip_tags or local in (
                'entity', 'segment', 'period', 'instant', 'startdate', 'enddate',
                'identifier', 'typedmember', 'explicitmember',
            ):
                elem.clear()
                continue

            context_ref = elem.get('contextRef', '')
            if context_ref:
                value_text = (elem.text or '').strip()
                concept = self._local_tag(elem.tag)
                if value_text and concept:
                    facts.append({
                        'concept': concept,
                        'concept_lower': concept.lower(),
                        'value': value_text,
                        'contextRef': context_ref,
                        'unitRef': elem.get('unitRef', ''),
                        'decimals': elem.get('decimals', ''),
                    })
            elem.clear()

        if debug_out is not None:
            debug_out["facts_total"] = len(facts)
            debug_out["facts_sample"] = facts[:400]
            # Concept summary
            by_concept: Dict[str, int] = defaultdict(int)
            for f in facts:
                by_concept[f.get("concept", "")] += 1
            debug_out["facts_by_concept"] = dict(sorted(by_concept.items(), key=lambda x: -x[1]))

        # -- Pass 2: filter facts to investment-related, current period, dimensioned contexts --
        investment_facts_by_dim: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        explicit_dims_by_dim: Dict[str, Dict[str, str]] = {}  # explicit dimensions per typed dim string
        period_date_str = period_end_date  # e.g. "2025-09-30"

        for fact in facts:
            ctx = contexts.get(fact['contextRef'], {})

            # Must have a dimension string (typed member of InvestmentIdentifierAxis)
            dim_string = ctx.get('dimension_string', '')
            if not dim_string:
                continue

            # Period filter: instant date must match period_end_date
            instant = ctx.get('instant', '')
            if instant and instant != period_date_str:
                continue
            # For duration contexts (start/end), accept if end matches
            if not instant:
                end = ctx.get('end', '')
                if end and end != period_date_str:
                    continue

            # Check if fact tag is investment-related
            cl = fact['concept_lower']
            is_investment_fact = (
                cl in INVESTMENT_FACT_TAGS
                or cl in INVESTMENT_FACT_TAGS_EXTRA
                or 'investment' in cl
            )
            if is_investment_fact:
                investment_facts_by_dim[dim_string].append(fact)

            # Capture explicit dimensions for this typed dimension string
            if dim_string not in explicit_dims_by_dim:
                ed = ctx.get('explicit_dimensions', {})
                if ed:
                    explicit_dims_by_dim[dim_string] = dict(ed)

        if not investment_facts_by_dim:
            logger.warning("No investment facts found in XBRL for period %s", period_date_str)
            return []

        logger.info("Found %d unique investment dimensions in XBRL", len(investment_facts_by_dim))

        # -- Dedup: remove summary/sparse dimensions that duplicate detailed entries --
        # Some filings (e.g. CSWC) include both detailed line-item contexts (with cost,
        # rates, spreads) and summary/footnote contexts (only FV + principal) for the
        # same investments. The summaries sometimes aggregate multiple line items and
        # use different casing or slightly different names. We detect sparse dims and
        # remove them when a richer dim exists for the same company.
        investment_facts_by_dim = self._deduplicate_dimensions(investment_facts_by_dim)
        logger.info("After dedup: %d investment dimensions", len(investment_facts_by_dim))

        if debug_out is not None:
            sample_dims = list(investment_facts_by_dim.items())[:60]
            debug_out["investment_facts_by_dim_sample"] = {
                dim: facts_list for dim, facts_list in sample_dims
            }
            debug_out["investment_dimensions_total"] = len(investment_facts_by_dim)
            debug_out["period_end_date"] = period_end_date

        # -- Pass 3: build rows --
        rows = []
        for dim_string, dim_facts in investment_facts_by_dim.items():
            parsed_dim = self._parse_dimension_string(dim_string)
            # Merge explicit dimensions (investment type axis, industry axis, etc.)
            ed = explicit_dims_by_dim.get(dim_string, {})
            if ed:
                parsed_dim['explicit_dimensions'] = ed
            row = self._map_investment_to_csv_row(parsed_dim, dim_facts)
            if row:
                rows.append(row)

        # Forward-fill empty company_name (ARCC and others often omit name on continuation lines)
        for i in range(1, len(rows)):
            if not (rows[i].get('company_name') or '').strip() and rows[i].get('fair_value'):
                prev = (rows[i - 1].get('company_name') or '').strip()
                if prev:
                    rows[i]['company_name'] = rows[i - 1]['company_name']

        # Drop rows that still have no company name (e.g. leading "Debt" context with no previous to fill from)
        rows = [r for r in rows if (r.get('company_name') or '').strip()]
        before_dedup_rows = len(rows)
        rows = self._deduplicate_output_rows(rows)
        if len(rows) != before_dedup_rows:
            logger.info("Row dedup: removed %d exact duplicate rows", before_dedup_rows - len(rows))

        logger.info("Built %d investment rows from XBRL data", len(rows))
        return rows

    # ------------------------------------------------------------------
    # Deduplication of summary vs detail dimensions
    # ------------------------------------------------------------------

    # Concepts that indicate a "detailed" entry (rates, costs, spreads)
    _DETAIL_CONCEPTS = {
        'investmentownedatcost', 'investmentatcost', 'investmentatamortizedcost',
        'investmentownedatamortizedcost',
        'investmentinterestrate', 'investmentinterestratecash',
        'investmentinterestratecontractual', 'investmentinterestrateeffective',
        'investmentinterestratepikinkinddividend', 'investmentinterestratepikinkinddividendrate',
        'investmentpikrate', 'investmentinterestratepaidinkind',
        'investmentbasisspreadvariablerate', 'investmentinterestratespread',
        'investmentinterestratespreadovervariablerate',
        'investmentinterestratefloor',
        'investmentinterestratepaidincash', 'investmentinterestrateduringperiod',
        'investmentinterestrateterms', 'investmentinterestratecap',
        'investmentvariableinterestratetypeextensibleenumeration',
        'investmentvariableinterestratetype',
    }

    _SUMMARY_COMPANY_LABELS = frozenset({
        'Debt Investments',
        'Equity Investments',
        'Total Debt Investments',
        'Total Equity Investments',
        'Total Unsecured Debt',
        'Total Secured Debt',
        'Total Common Stock',
        'Total Preferred Stock',
        'Total Warrants',
        'Total Affiliates',
        'Total Non-Control/Non-Affiliates',
        'Total Non-Controlled/Non-Affiliated',
        'Total Non-Controlled/Non-Affiliated Investments',
    })
    _LEGAL_ENTITY_SUFFIX_RE = re.compile(
        r'\b(?:LLC|L\.?L\.?C\.?|Inc\.?|Corp\.?|Corporation|Ltd\.?|Limited|LP|L\.?P\.?|'
        r'LLP|L\.?L\.?P\.?|PLC|Group|Holdings?|Partners?|Co\.?)\b',
        re.IGNORECASE,
    )

    def _deduplicate_dimensions(
        self, facts_by_dim: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Remove summary/sparse dimensions that duplicate richer detailed entries.

        Some filings tag the same investments twice in XBRL: once in the main
        Schedule of Investments (detailed, with cost/rates/spreads) and again in
        footnotes or summary tables (sparse, only FV + principal/shares). The
        summary entries may use different casing or slightly different names, and
        sometimes aggregate multiple line items.

        Strategy:
        1. Classify each dimension as "detailed" (has cost OR rate facts) vs "sparse".
        2. Build a set of company names that have at least one detailed dimension.
        3. Remove sparse dimensions whose company name matches a detailed one.
        """
        # Classify each dimension
        detailed_dims: Dict[str, List[Dict[str, Any]]] = {}
        sparse_dims: Dict[str, List[Dict[str, Any]]] = {}

        for dim_string, dim_facts in facts_by_dim.items():
            concepts = {f['concept_lower'] for f in dim_facts}
            has_detail = bool(concepts & self._DETAIL_CONCEPTS)
            if has_detail:
                detailed_dims[dim_string] = dim_facts
            else:
                sparse_dims[dim_string] = dim_facts

        if not sparse_dims:
            return facts_by_dim  # nothing to dedup

        # Extract normalized company names from detailed dimensions
        detailed_company_names: set = set()
        for dim_string in detailed_dims:
            name = self._extract_company_name_from_dim(dim_string)
            if name:
                detailed_company_names.add(name.upper().strip())

        if not detailed_company_names:
            return facts_by_dim  # no detailed dims to compare against

        # Filter sparse dims: keep only those whose company is NOT in detailed set
        kept_sparse = 0
        removed_sparse = 0
        result = dict(detailed_dims)
        for dim_string, dim_facts in sparse_dims.items():
            name = self._extract_company_name_from_dim(dim_string)
            name_upper = name.upper().strip() if name else ''
            if name_upper and name_upper in detailed_company_names:
                removed_sparse += 1
            else:
                result[dim_string] = dim_facts
                kept_sparse += 1

        logger.info("Dedup: %d detailed, %d sparse removed, %d sparse kept (no detailed match)",
                     len(detailed_dims), removed_sparse, kept_sparse)
        return result

    @staticmethod
    def _extract_company_name_from_dim(dim_string: str) -> str:
        """Quick extraction of company name from dimension string for dedup purposes."""
        name = ''
        if '|' in dim_string:
            parts = [p.strip() for p in dim_string.split('|')]
            first = parts[0] if parts else ''
            if first in ('Common Equity/Equity Interests/Warrants', 'Equipment Financing'):
                name = parts[1] if len(parts) >= 2 else first
            elif first == 'Senior Secured Loans' and len(parts) >= 3:
                name = parts[2]
            else:
                name = first
        elif ',' in dim_string:
            # Comma format: first segment is usually the company name or type prefix
            parts = dim_string.split(',', 3)
            first = parts[0].strip()
            # Skip type prefixes like "DEBT Investment"
            if re.match(r'^(DEBT|EQUITY|PREFERRED|WARRANT|COMMON)\s+Investment$', first, re.I):
                if len(parts) >= 3:
                    name = parts[2].strip()
                else:
                    name = first
            else:
                name = first
        else:
            name = dim_string.strip()
        # Strip trailing line-item numbers for matching (e.g., "Company LLC 1" → "Company LLC")
        name = re.sub(r'\s+\d+\s*$', '', name)
        return name

    # ------------------------------------------------------------------
    # Dimension string parsing
    # ------------------------------------------------------------------

    def _parse_dimension_string(self, dim_string: str) -> Dict[str, str]:
        """
        Parse the XBRL typed dimension string to extract structured fields.

        Different BDCs use different formats:

        CSWC style (pipe-separated):
          "BRANDNER DESIGN, LLC | Revolving Loan 1"
          "AAC NEW HOLDCO INC. | First Lien - Term Loan A"
          "AAC NEW HOLDCO INC. | Warrants (Expiration - December 11, 2025)"

        ARCC style (comma-separated):
          "3 Step Sports LLC, First lien senior secured loan"
          "ACP Avenu Midco LLC, First lien senior secured loan 1"

        Rich style (comma-separated with metadata):
          "DEBT Investment, Automobile Components, Fenix Intermediate LLC,
           Acquisition Date - 03/28/24, Investment - Term Loan B - 10.83%
           (SOFR + 6.50%, 1.75% Floor), Net Assets - 4.0%, Maturity Date - 03/28/29"

        Uses independent regexes for each field so partial failures don't prevent
        extracting other fields.
        """
        result: Dict[str, str] = {'raw_dimension': dim_string}

        # Detect format: TSLX (space-separated "Debt/Equity and Other Investments Industry Company Instrument..."),
        # then and-separated (HTGC), blob (MSDL-style), pipe-separated, or comma-separated
        if self._is_tslx_dimension_format(dim_string):
            return self._parse_tslx_format(dim_string, result)
        if dim_string.startswith('Portfolio Company ') and ' United States ' in dim_string:
            return self._parse_trin_format(dim_string, result)
        if self._INV_AFFILIATION_RE.match(dim_string):
            return self._parse_inv_affiliation_format(dim_string, result)
        if self._INV_PIPE_AFFILIATION_RE.match(dim_string):
            return self._parse_pipe_affiliation_format(dim_string, result)
        if re.match(r'^(?:Debt|Equity)\s+Investments?\s+', dim_string, re.I) and ' and ' in dim_string:
            return self._parse_and_format(dim_string, result)
        elif re.match(r'^Investments?[-\s]', dim_string) or re.match(r'^(?:Debt|Equity)\s+Investments?\s*-', dim_string, re.I):
            return self._parse_blob_format(dim_string, result)
        elif '|' in dim_string:
            return self._parse_pipe_format(dim_string, result)
        else:
            return self._parse_comma_format(dim_string, result)

    # TSLX: "Debt Investments {Industry} {Company} First-lien loan ..." or "Equity and Other Investments {Industry} {Company} ..."
    _TSLX_PREFIX_DEBT = 'Debt Investments '
    _TSLX_PREFIX_EQUITY = 'Equity and Other Investments '
    _TSLX_INSTRUMENT_ANCHOR_RE = re.compile(
        r'\s+(First-lien\s+loan|First-lien\s+revolving|Second-lien\s+note|Partnership\s+Interest|Warrants?\s+\()'
        r'|\s+(Series\s+[A-Z]\s+Preferred\s+Shares|Preferred\s+Shares|Class\s+[AB][-\s]?[12]?\s+Units?)',
        re.IGNORECASE,
    )
    # Known TSLX industries (longest first so "Retail and Consumer Products" matches before "Retail")
    _TSLX_INDUSTRIES = (
        'Human Resource Support Services',
        'Retail and Consumer Products',
        'Oil Gas and Consumable Fuels',
        'Chemicals & Materials',
        'Chemicals',
        'Business Services',
        'Financial Services',
        'Communications',
        'Pharmaceuticals',
        'Transportation',
        'Electronics',
        'Internet Services',
        'Manufacturing',
        'Media & Entertainment',
        'Healthcare',
        'Education',
    )

    def _is_tslx_dimension_format(self, dim_string: str) -> bool:
        """True if dimension string is TSLX space-separated format (no " and " between segments)."""
        if dim_string.startswith(self._TSLX_PREFIX_DEBT) or dim_string.startswith(self._TSLX_PREFIX_EQUITY):
            return bool(self._TSLX_INSTRUMENT_ANCHOR_RE.search(dim_string))
        return False

    def _parse_tslx_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """
        Parse TSLX format: "Debt Investments {Industry} {Company} First-lien loan (...)"
        or "Equity and Other Investments {Industry} {Company} Partnership Interest (...)".
        No " and " between segments; industry and company are space-separated.
        """
        if dim_string.startswith(self._TSLX_PREFIX_DEBT):
            result['investment_type_raw'] = 'DEBT'
            rest = dim_string[len(self._TSLX_PREFIX_DEBT):]
        elif dim_string.startswith(self._TSLX_PREFIX_EQUITY):
            result['investment_type_raw'] = 'EQUITY'
            rest = dim_string[len(self._TSLX_PREFIX_EQUITY):]
        else:
            return result

        # Find where instrument description starts (e.g. "First-lien loan", "Partnership Interest")
        anchor = self._TSLX_INSTRUMENT_ANCHOR_RE.search(rest)
        if not anchor:
            return result
        industry_company = rest[:anchor.start()].strip()
        tail = rest[anchor.start():]

        # Split "Industry CompanyName" using known industries (longest match first)
        company_name = industry_company
        result['industry'] = ''
        for ind in self._TSLX_INDUSTRIES:
            if industry_company.startswith(ind + ' '):
                result['industry'] = ind
                company_name = industry_company[len(ind):].strip()
                break

        if company_name:
            result['company_name'] = company_name

        # Acquisition date
        acq_match = re.search(r'Initial\s+Acquisition\s+Date\s+(\d{1,2}/\d{1,2}/\d{4}|\d{1,2}/\d{4})', tail, re.I)
        if acq_match:
            result['acquisition_date'] = self._normalize_date(acq_match.group(1))

        # Reference Rate and Spread / Interest Rate from tail or full string
        ref_match = re.search(
            r'Reference\s+Rate\s+and\s+Spread\s+'
            r'(S|SOFR|L|LIBOR|P|Prime|E|Euribor|SONIA|STIBOR|AMERIBOR|CDOR|BKBM|B)\s*\+\s*([\d.]+)%',
            dim_string, re.I,
        )
        if ref_match:
            result['reference_rate'] = ref_match.group(1).upper()
            if result['reference_rate'] == 'B':
                result['reference_rate'] = 'SOFR'  # or keep B if you have a mapping
            result['spread'] = ref_match.group(2) + '%'
        rate_match = re.search(r'Interest\s+Rate\s+([\d.]+)%', dim_string, re.I)
        if rate_match:
            result['cash_rate_from_dim'] = rate_match.group(1) + '%'
        pik_match = re.search(r'(\d+\.?\d*)\s*%\s*PIK', dim_string, re.I)
        if pik_match:
            result['pik_rate'] = pik_match.group(1) + '%'

        return result

    # TRIN: "Portfolio Company Debt/Warrant/Equity Investments- United States {Industry} {Company} Type of Investment ..."
    _TRIN_PREFIX_RE = re.compile(
        r'^Portfolio\s+Company\s+(Debt\s+Securities|Warrant\s+Investments|Equity\s+Investments)\s*-\s*United\s+States\s+',
        re.I,
    )
    _TRIN_TAIL_RE = re.compile(
        r'\s+Type\s+of\s+Investment\s+|\s+Investment\s+Date\s+|\s+Expiration\s+Date\s+',
        re.I,
    )
    _TRIN_INDUSTRIES = (
        'Food and Agriculture Technologies', 'Consumer Products & Services', 'Supply Chain Technology',
        'Real Estate Technology', 'Transportation Technology', 'Education Technology', 'Human Resource Technology',
        'Medical Devices', 'Construction Technology', 'Finance and Insurance', 'Green Technology', 'Space Technology',
        'Artificial Intelligence & Automation', 'Biotechnology', 'Connectivity', 'Healthcare', 'Industrials', 'SaaS',
    )

    def _parse_trin_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """
        Parse TRIN format: "Portfolio Company X- United States Industry Company Name Type of Investment ..."
        or "Portfolio Company X- United States Industry" (category-only, no company).
        """
        prefix_m = self._TRIN_PREFIX_RE.match(dim_string)
        if not prefix_m:
            return result
        cat = prefix_m.group(1)
        result['investment_type_raw'] = 'EQUITY' if 'Equity' in cat else 'DEBT'
        rest = dim_string[prefix_m.end():]

        # Find where investment-detail tail starts (Type of Investment, Investment Date, Expiration Date)
        tail_m = self._TRIN_TAIL_RE.search(rest)
        industry_company = rest[:tail_m.start()].strip() if tail_m else rest.strip()

        # Split "Industry CompanyName" using known TRIN industries (longest first)
        company_name = industry_company
        result['industry'] = ''
        for ind in self._TRIN_INDUSTRIES:
            if industry_company.startswith(ind + ' '):
                result['industry'] = ind
                company_name = industry_company[len(ind):].strip()
                break
        if not result['industry'] and industry_company:
            # No known industry matched; treat whole segment as company if it looks like a name (has comma or Inc/LLC)
            if re.search(r'\b(Inc\.?|LLC|Ltd\.?|Corp\.?|LP)\b', industry_company, re.I):
                company_name = industry_company
            else:
                result['industry'] = industry_company  # single-token or unknown industry

        if company_name:
            result['company_name'] = company_name

        # Optional: parse Investment Date, Maturity Date, Interest Rate from tail
        if tail_m:
            tail = dim_string[prefix_m.end() + tail_m.start():]
            acq_match = re.search(r'Investment\s+Date\s+(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', tail, re.I)
            if acq_match:
                result['acquisition_date'] = self._normalize_date(acq_match.group(1).replace(',', '').strip())
            mat_match = re.search(r'Maturity\s+Date\s+(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})', tail, re.I)
            if mat_match:
                result['maturity_date'] = self._normalize_date(mat_match.group(1).replace(',', '').strip())
            prime_floor = re.search(r'Prime\s*\+\s*([\d.]+)%\s*or\s*Floor\s+rate\s+([\d.]+)%', tail, re.I)
            if prime_floor:
                result['reference_rate'] = 'Prime'
                result['spread'] = prime_floor.group(1) + '%'
                result['floor_rate'] = prime_floor.group(2) + '%'
            fixed_rate = re.search(r'Fixed\s+interest\s+rate\s+([\d.]+)%', tail, re.I)
            if fixed_rate:
                result['cash_rate_from_dim'] = fixed_rate.group(1) + '%'

        return result

    # SLRC: dimension is "Category | Company | Industry | ..." or "Senior Secured Loans | Loan type | Company | Industry | ..."
    _PIPE_CATEGORY_FIRST = (
        'Common Equity/Equity Interests/Warrants',
        'Equipment Financing',
        'Senior Secured Loans',
    )

    def _parse_pipe_affiliation_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """Parse CGBD pipe format: 'Investment | Affiliation | InvType | Company | Industry: Subcategory'.

        The blob parser incorrectly uses the industry subcategory (last segment after the last |)
        as the company name. This parser correctly extracts the 4th pipe-segment as company name.

        Example:
          "Investment | Non-Affiliated Issuer | First Lien Debt | Alpine Acquisition Corp II | Transportation: Cargo"
          → company_name = "Alpine Acquisition Corp II", industry = "Transportation"
        """
        parts = [p.strip() for p in dim_string.split('|')]
        # parts[0] = "Investment", parts[1] = affiliation, parts[2] = inv_type, parts[3] = company, parts[4] = industry:sub
        if len(parts) >= 4:
            result['company_name'] = parts[3]
        if len(parts) >= 3:
            inv_type = parts[2].lower()
            if 'equity' in inv_type:
                result['investment_type_raw'] = 'EQUITY'
            else:
                result['investment_type_raw'] = 'DEBT'
        if len(parts) >= 5:
            # "Transportation: Cargo" → use base category as industry
            industry_seg = parts[4]
            if ':' in industry_seg:
                result['industry'] = industry_seg.split(':')[0].strip()
            else:
                result['industry'] = industry_seg
        affil = (parts[1] if len(parts) >= 2 else '').lower()
        if 'non' in affil:
            result['investment_affiliation'] = 'non-affiliated'
        elif 'affiliated' in affil:
            result['investment_affiliation'] = 'affiliated'
        return result

    def _parse_pipe_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """Parse 'Company Name | Investment Description' format (CSWC style).
        Also handles SLRC style: 'Category | Company | Industry | ...' where first segment is not the company."""
        all_parts = [p.strip() for p in dim_string.split('|')]
        if all_parts and all_parts[0] in self._PIPE_CATEGORY_FIRST:
            # SLRC-style: first segment is category, then company, then industry
            first = all_parts[0]
            if first == 'Senior Secured Loans':
                if len(all_parts) >= 4:
                    result['company_name'] = all_parts[2]
                    result['industry'] = all_parts[3]
                    result['investment_type_raw'] = 'DEBT'
                    result['investment_description'] = all_parts[1]
                    return result
                # Category-only (e.g. "Senior Secured Loans" with no company) → skip row
                result['company_name'] = ''
                return result
            if first in ('Common Equity/Equity Interests/Warrants', 'Equipment Financing'):
                if len(all_parts) >= 2:
                    result['company_name'] = all_parts[1]
                    if len(all_parts) >= 3:
                        result['industry'] = all_parts[2]
                    result['investment_type_raw'] = 'EQUITY' if first == 'Common Equity/Equity Interests/Warrants' else 'DEBT'
                    return result
                # Category-only → skip row
                result['company_name'] = ''
                return result

        parts = dim_string.split('|', 1)
        if len(parts) >= 1:
            result['company_name'] = parts[0].strip()
        if len(parts) >= 2:
            inv_desc = parts[1].strip()
            result['investment_description'] = inv_desc

            # Extract investment type from description
            desc_lower = inv_desc.lower()
            if 'revolv' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
                result['investment_description'] = inv_desc
            elif 'delayed draw' in desc_lower or 'ddtl' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
            elif 'first lien' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
            elif 'second lien' in desc_lower or '2nd lien' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
            elif 'subordinated' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
            elif 'term loan' in desc_lower:
                result['investment_type_raw'] = 'DEBT'
            elif 'warrant' in desc_lower:
                result['investment_type_raw'] = 'WARRANT'
            elif any(kw in desc_lower for kw in ('common unit', 'common stock', 'shares of common', 'class a', 'class b')):
                result['investment_type_raw'] = 'COMMON'
            elif any(kw in desc_lower for kw in ('preferred unit', 'preferred stock', 'shares of preferred')):
                result['investment_type_raw'] = 'PREFERRED'
            elif any(kw in desc_lower for kw in ('equity', 'unit', 'membership interest', 'member interest')):
                result['investment_type_raw'] = 'EQUITY'

        # Extract parenthetical info (e.g., "Warrants (Expiration - December 11, 2025)")
        paren_match = re.search(r'\(([^)]+)\)', dim_string)
        if paren_match:
            paren_content = paren_match.group(1)
            # Rate info
            rate_match = re.search(
                r'(SOFR|LIBOR|Prime|SONIA)\s*\+\s*([\d.]+)%?', paren_content, re.I,
            )
            if rate_match:
                result['reference_rate'] = rate_match.group(1).upper()
                result['spread'] = rate_match.group(2) + '%'

        return result

    def _parse_and_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """
        Parse 'and'-separated format (HTGC style).

        Examples:
          "Debt Investments and Application Software and Alchemer LLC and Senior Secured
           and Maturity Date May 2028 and 1-month SOFR + 8.14%, Floor rate 9.14%"
          "Equity Investments and Application Software and Behavox Limited and Preferred Stock"
          "Debt Investments and Application Software and Total Annex Cloud"
        """
        segments = [s.strip() for s in dim_string.split(' and ')]
        if not segments:
            return result

        # First segment: "Debt Investments [Industry]" or "Equity Investments [Industry]"
        first = segments[0].strip()
        first_lower = first.lower()
        if first_lower.startswith('debt investments'):
            result['investment_type_raw'] = 'DEBT'
            industry_part = first[len('Debt Investments'):].strip()
        elif first_lower.startswith('equity investments'):
            result['investment_type_raw'] = 'EQUITY'
            industry_part = first[len('Equity Investments'):].strip()
        elif first_lower.startswith('debt investment'):
            result['investment_type_raw'] = 'DEBT'
            industry_part = first[len('Debt Investment'):].strip()
        elif first_lower.startswith('equity investment'):
            result['investment_type_raw'] = 'EQUITY'
            industry_part = first[len('Equity Investment'):].strip()
        else:
            industry_part = ''

        if industry_part:
            result['industry'] = industry_part

        # Keep a marker for "Total ..." summary entries so row-level mapper can drop them.
        if any(s.strip().lower().startswith('total ') for s in segments[1:3]):
            result['company_name'] = ' '.join(segments[1:]).strip()
            result['is_summary_entry'] = '1'
            return result

        # Second segment: Company name
        if len(segments) >= 2:
            result['company_name'] = segments[1].strip()

        # Remaining segments (starting from 3rd): investment type details, maturity, rates
        for seg in segments[2:]:
            seg = seg.strip()
            seg_lower = seg.lower()

            # Investment type / description
            if any(kw in seg_lower for kw in ('senior secured', 'first lien', 'second lien',
                                               'subordinated', 'unsecured', 'mezzanine')):
                result['investment_description'] = seg

            # Preferred/common stock, warrants
            elif any(kw in seg_lower for kw in ('preferred stock', 'common stock', 'warrant',
                                                 'preferred unit', 'common unit', 'membership')):
                result['investment_description'] = seg

            # Maturity date
            elif 'maturity date' in seg_lower or 'maturity' in seg_lower:
                mat = re.sub(r'^maturity\s+date\s*', '', seg, flags=re.I).strip()
                # Try to parse "May 2028" or "12/2028" or "12/15/2028"
                parsed_mat = self._parse_text_maturity(mat)
                if parsed_mat:
                    result['maturity_date'] = parsed_mat

            # Rate info
            elif any(kw in seg_lower for kw in ('sofr', 'libor', 'prime', 'fixed rate', 'floor rate', 'pik')):
                # Reference rate + spread
                ref_match = re.search(
                    r'(?:\d+-month\s+)?(SOFR|LIBOR|Prime|SONIA|Euribor|AMERIBOR)\s*([+-])\s*([\d.]+)%',
                    seg, re.I,
                )
                if ref_match:
                    result['reference_rate'] = ref_match.group(1).upper()
                    sign = ref_match.group(2)
                    spread_val = ref_match.group(3)
                    result['spread'] = ('' if sign == '+' else '-') + spread_val + '%'

                # Floor rate
                floor_match = re.search(r'[Ff]loor\s+rate\s+([\d.]+)%', seg)
                if floor_match:
                    result['floor_rate'] = floor_match.group(1) + '%'

                # PIK
                pik_match = re.search(r'PIK\s+Interest\s+([\d.]+)%', seg, re.I)
                if not pik_match:
                    pik_match = re.search(r'PIK\s+([\d.]+)%', seg, re.I)
                if pik_match:
                    result['pik_rate'] = pik_match.group(1) + '%'

                # Fixed rate
                fixed_match = re.search(r'[Ff]ixed\s+[Rr]ate\s+([\d.]+)%', seg)
                if fixed_match:
                    result['cash_rate_from_dim'] = fixed_match.group(1) + '%'

        return result

    @staticmethod
    def _parse_text_maturity(text: str) -> str:
        """Parse maturity dates like 'May 2028', '12/2028', '12/15/2028' to YYYY-MM-DD."""
        text = text.strip()
        # MM/DD/YYYY or MM/YYYY
        m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', text)
        if m:
            month, day, year = m.group(1), m.group(2), m.group(3)
            if len(year) == 2:
                year = '20' + year
            return f"{year}-{int(month):02d}-{int(day):02d}"
        m = re.match(r'^(\d{1,2})/(\d{4})$', text)
        if m:
            return f"{m.group(2)}-{int(m.group(1)):02d}-01"
        # Month Year (e.g., "May 2028")
        month_names = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
        }
        m = re.match(r'^(\w+)\s+(\d{4})$', text)
        if m:
            month_name = m.group(1).lower()
            if month_name in month_names:
                return f"{m.group(2)}-{month_names[month_name]:02d}-01"
        return ''

    # Detection for new "Investment, {Affiliation}, {Type}, {Company...}, {Industry}" format
    # (CGBD 2025+ and similar filers that switched to this comma structure)
    _INV_AFFILIATION_RE = re.compile(
        r'^Investment,\s*(?:Non-?Affiliated\s+Issuer|Affiliated\s+Issuer|Non-?Controlled|Controlled)\s*,',
        re.IGNORECASE,
    )
    # Detection for CGBD pipe format: "Investment | Affiliation | Type | Company | Industry: Sub"
    # The blob parser incorrectly takes the industry subcategory (after the last |) as company name.
    _INV_PIPE_AFFILIATION_RE = re.compile(
        r'^Investment\s*\|\s*(?:Non-?Affiliated|Affiliated)(?:\s+Issuer)?\s*\|',
        re.IGNORECASE,
    )
    # Legal entity suffix to detect end of company name in comma-separated segments
    _INV_AFF_LEGAL_SUFFIX_RE = re.compile(
        r'\b(?:LLC|L\.?L\.?C\.?|L\.?P\.?|Inc\.?|Corp\.?|Ltd\.?|LTD\.?|GmbH|Pty\s+LTD?|'
        r'Limited|Co\.|N\.V\.|S\.A\.|Company|Corporation|L\.L\.P\.?|LLP)\s*(?:\([^)]*\))?\s*$',
        re.IGNORECASE,
    )
    _INV_AFF_INSTRUMENTS = frozenset(['revolver', 'delayed draw', 'delayed draw term loan', 'term loan'])

    def _parse_inv_affiliation_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """
        Parse new CGBD 2025+ format:
          "Investment, {Affiliation}, {InvType}, {Company parts...}, {Industry/Instrument}"

        Examples:
          "Investment, Non-Affiliated Issuer, First Lien Debt, ACR Group Borrower, LLC, Aerospace & Defense"
          "Investment, Non-Affiliated Issuer, First Lien Debt, AP Plastics, LLC, Chemicals, Plastics & Rubber"
          "Investment, Non-Affiliated Issuer, First Lien Debt, AAH Topco., LLC, Delayed Draw"
          "Investment, Affiliated Issuer, Investment Funds, Middle Market Credit Fund, LLC, Mezzanine Loan, Investment Funds"
        """
        segments = [s.strip() for s in self._split_segments(dim_string, ',')]
        # [0]="Investment", [1]=affiliation, [2]=investment_type, [3:]=company+industry

        if len(segments) >= 3:
            inv_type_raw = segments[2]
            result['investment_description'] = inv_type_raw
            if any(kw in inv_type_raw.lower() for kw in ('equity', 'warrant', 'preferred')):
                result['investment_type_raw'] = 'EQUITY'
            else:
                result['investment_type_raw'] = 'DEBT'

        if len(segments) < 4:
            return result

        remaining = segments[3:]

        # Find LAST segment with a legal entity suffix → marks end of company name
        company_end_idx = None
        for i, seg in enumerate(remaining):
            if self._INV_AFF_LEGAL_SUFFIX_RE.search(seg):
                company_end_idx = i

        if company_end_idx is not None:
            company_parts = remaining[:company_end_idx + 1]
            rest = remaining[company_end_idx + 1:]
            result['company_name'] = ', '.join(company_parts)
            if rest:
                rest_str = ', '.join(rest)
                if rest_str.lower() in self._INV_AFF_INSTRUMENTS:
                    result['investment_description'] = rest_str
                else:
                    result['industry'] = rest_str
        else:
            # No legal suffix found
            if not remaining:
                return result
            last = remaining[-1]
            # Check if last segment is a known instrument type
            if last.lower() in self._INV_AFF_INSTRUMENTS:
                result['investment_description'] = last
                result['company_name'] = ', '.join(remaining[:-1])
            elif len(remaining) == 1:
                result['company_name'] = remaining[0]
            elif len(remaining) >= 2:
                # Heuristic: last segment with '&' or ':' is industry (or part of it)
                if '&' in last or ':' in last or last.lower() in _KNOWN_INDUSTRIES:
                    # Check if second-to-last is also part of the industry (no '&'/':' but not a company suffix)
                    penultimate = remaining[-2]
                    if len(remaining) >= 3 and not self._INV_AFF_LEGAL_SUFFIX_RE.search(penultimate) and '&' not in penultimate and ':' not in penultimate:
                        result['company_name'] = ', '.join(remaining[:-2])
                        result['industry'] = ', '.join(remaining[-2:])
                    else:
                        result['company_name'] = ', '.join(remaining[:-1])
                        result['industry'] = last
                else:
                    result['company_name'] = ', '.join(remaining)

        return result

    # Regex for the blob format investment type anchor
    _BLOB_INV_TYPE_RE = re.compile(
        r'\bInvestments?\s+'
        r'(First\s+Lien(?:\s+(?:Senior\s+Secured|Secured))?\s+Debt'
        r'|Second\s+Lien(?:\s+(?:Senior\s+Secured|Secured))?\s+Debt'
        r'|Subordinated\s+Debt|Unsecured\s+Debt|Mezzanine\s+Debt'
        r'|Senior\s+Secured\s+(?:First\s+Lien\s+)?(?:Debt|Loan|Term\s+Loan|Bond)'
        r'|(?:First\s+Lien|Second\s+Lien)\s+(?:Term\s+Loan|Revolver|Delayed\s+Draw)'
        r'|(?:Preferred|Common)\s+(?:Equity|Stock)'
        r'|Equity|Warrants?)\b',
        re.IGNORECASE,
    )

    # Regex for the blob affiliation + category prefix (two variants)
    # Variant 1: "Investments-non-controlled/non-affiliated Debt Investments ..."
    _BLOB_PREFIX_RE = re.compile(
        r'^Investments?[-\s]+(?:non[-\s]+controlled/non[-\s]+affiliated'
        r'|controlled'
        r'|non[-\s]+controlled/affiliated'
        r'|affiliated'
        r'|non[-\s]+controlled)\s+'
        r'(Debt|Equity)\s+Investments?\s*',
        re.IGNORECASE,
    )
    # Variant 2: "Investments - non-controlled/non-affiliated First Lien Debt Company ..."
    # (commitment entries without industry, investment type right after affiliation)
    _BLOB_COMMIT_PREFIX_RE = re.compile(
        r'^Investments?\s*[-\s]+\s*(?:non[-\s]+controlled/non[-\s]+affiliated'
        r'|controlled'
        r'|non[-\s]+controlled/affiliated'
        r'|affiliated'
        r'|non[-\s]+controlled)\s+'
        r'(First\s+Lien\s+Debt|Second\s+Lien\s+Debt|Subordinated\s+Debt'
        r'|Unsecured\s+Debt|Preferred\s+Equity|Common\s+Equity|Equity|Debt)\s+',
        re.IGNORECASE,
    )
    # Variant 3: "Debt Investments - non-controlled/affiliated ..."
    _BLOB_ALT_PREFIX_RE = re.compile(
        r'^(Debt|Equity)\s+Investments?\s*[-\s]+\s*(?:non[-\s]+controlled/non[-\s]+affiliated'
        r'|controlled'
        r'|non[-\s]+controlled/affiliated'
        r'|affiliated'
        r'|non[-\s]+controlled)\s+',
        re.IGNORECASE,
    )

    def _parse_blob_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """
        Parse undelimited blob format (MSDL style).

        Example:
          "Investments-non-controlled/non-affiliated Debt Investments
           Aerospace & Defense Jonathan Acquisition Company
           Investment First Lien Debt
           Reference Rate and Spread S + 5.00%
           Interest Rate 9.10%
           Maturity Date 12/22/2026"
        """
        remaining = dim_string

        # 1. Strip affiliation + category prefix (try all variants)
        prefix_m = self._BLOB_PREFIX_RE.match(remaining)
        alt_m = self._BLOB_ALT_PREFIX_RE.match(remaining) if not prefix_m else None
        commit_m = self._BLOB_COMMIT_PREFIX_RE.match(remaining) if not prefix_m and not alt_m else None

        if prefix_m:
            broad_type = prefix_m.group(1).upper()  # DEBT or EQUITY
            result['investment_type_raw'] = broad_type
            remaining = remaining[prefix_m.end():]
        elif alt_m:
            broad_type = alt_m.group(1).upper()
            result['investment_type_raw'] = broad_type
            remaining = remaining[alt_m.end():]
        elif commit_m:
            # Commitment variant: type is right in the prefix
            inv_type_str = commit_m.group(1).strip()
            result['investment_description'] = inv_type_str
            result['investment_type_raw'] = 'DEBT' if 'debt' in inv_type_str.lower() else 'EQUITY'
            remaining = remaining[commit_m.end():]
            # Parse commitment-specific fields
            commit_type_m = re.search(r'Commitment\s+Type\s+([\w\s]+?)(?:\s+Commitment)', remaining, re.I)
            if commit_type_m:
                ct = commit_type_m.group(1).strip()
                if 'revolver' in ct.lower() or 'revolving' in ct.lower():
                    result['investment_description'] = 'Revolver'
                elif 'delayed draw' in ct.lower() or 'ddtl' in ct.lower():
                    result['investment_description'] = 'Delayed Draw Term Loan'
            exp_m = re.search(r'Commitment\s+Expiration\s+Date\s+(\d{1,2}/\d{1,2}/\d{2,4})', remaining, re.I)
            if exp_m:
                result['maturity_date'] = self._normalize_date(exp_m.group(1))
            # Company name is everything before "Commitment Type"
            comp_end = re.search(r'\s+Commitment\s+Type', remaining, re.I)
            if comp_end:
                result['company_name'] = remaining[:comp_end.start()].strip()
            else:
                result['company_name'] = remaining.strip()
            return result
        else:
            # Fallback: just strip "Investments-..." prefix
            remaining = re.sub(r'^Investments?\S*\s+', '', remaining, count=1)

        # 2. Extract tail metadata: Reference Rate, Interest Rate, Maturity Date, PIK
        # Reference Rate and Spread
        ref_match = re.search(
            r'Reference\s+Rate\s+and\s+Spread\s+'
            r'(S|SOFR|L|LIBOR|P|Prime|E|Euribor|SONIA|AMERIBOR|CDOR|BKBM|Base\s*Rate)'
            r'\s*\+\s*([\d.]+)%',
            remaining, re.IGNORECASE,
        )
        if ref_match:
            ref_abbrev = ref_match.group(1).strip().upper()
            ref_map = {'S': 'SOFR', 'L': 'LIBOR', 'P': 'PRIME', 'E': 'EURIBOR'}
            result['reference_rate'] = ref_map.get(ref_abbrev, ref_abbrev)
            result['spread'] = ref_match.group(2) + '%'

        # PIK in spread
        pik_match = re.search(r'(\d+\.?\d*)\s*%\s*PIK', remaining, re.IGNORECASE)
        if pik_match:
            result['pik_rate'] = pik_match.group(1) + '%'

        # Interest Rate (total)
        rate_match = re.search(r'Interest\s+Rate\s+([\d.]+)%', remaining, re.IGNORECASE)
        if rate_match:
            result['cash_rate_from_dim'] = rate_match.group(1) + '%'

        # Maturity Date
        mat_match = re.search(r'Maturity\s+Date\s+(\d{1,2}/\d{1,2}/\d{2,4})', remaining, re.IGNORECASE)
        if mat_match:
            result['maturity_date'] = self._normalize_date(mat_match.group(1))

        # Strip tail metadata from remaining to isolate industry + company + type
        tail_start = len(remaining)
        for pattern in [r'Reference\s+Rate', r'Interest\s+Rate\s+\d', r'Maturity\s+Date',
                        r'Fixed\s+Rate', r'Floating\s+Rate']:
            m = re.search(pattern, remaining, re.IGNORECASE)
            if m and m.start() < tail_start:
                tail_start = m.start()
        middle = remaining[:tail_start].strip()

        # 3. Find investment type anchor in the middle: "Investment First Lien Debt"
        inv_type_match = self._BLOB_INV_TYPE_RE.search(middle)
        if inv_type_match:
            result['investment_description'] = inv_type_match.group(1).strip()
            # Everything before "Investment(s) Type" is industry + company
            before_type = middle[:inv_type_match.start()].strip()
        else:
            before_type = middle.strip()

        # Also strip trailing line-item identifiers like "One", "Two", "Three", numbers
        before_type = re.sub(r'\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)\s*$',
                             '', before_type, flags=re.IGNORECASE).strip()

        # 4. Split industry from company in `before_type`
        # Try matching known industry phrases (longest first)
        industry_found = ''
        company_found = before_type
        before_lower = before_type.lower()
        # Sort by length descending for longest match first
        for ind in sorted(_KNOWN_INDUSTRIES, key=len, reverse=True):
            idx = before_lower.find(ind)
            if idx != -1:
                industry_found = before_type[idx:idx + len(ind)].strip()
                company_found = before_type[idx + len(ind):].strip()
                break

        if industry_found:
            result['industry'] = industry_found
        if company_found:
            result['company_name'] = company_found

        return result

    def _parse_comma_format(self, dim_string: str, result: Dict[str, str]) -> Dict[str, str]:
        """Parse comma-separated format (ARCC style or rich metadata style)."""
        # Check for leading investment type token
        inv_type_match = re.match(
            r'^\s*(DEBT|EQUITY|PREFERRED|WARRANT|COMMON|SUBORDINATED|CONVERTIBLE)\s+Investment\b',
            dim_string, re.IGNORECASE,
        )
        if inv_type_match:
            result['investment_type_raw'] = inv_type_match.group(1).upper()

        # Split on commas (but not inside parentheses)
        segments = self._split_segments(dim_string, ',')

        if inv_type_match:
            # Rich format: "DEBT Investment, Industry, Company, ..."
            if len(segments) >= 2:
                industry_candidate = segments[1].strip()
                if not re.match(r'^(Acquisition|Maturity|Investment|Net Assets|Reference)', industry_candidate, re.I):
                    result['industry'] = industry_candidate
            if len(segments) >= 3:
                name_candidate = segments[2].strip()
                if not re.match(r'^(Acquisition|Maturity|Investment|Net Assets|Reference)', name_candidate, re.I):
                    result['company_name'] = name_candidate
        else:
            # Simple format: "Company Name, Investment Type"
            # or "Company Name, Investment Type N"
            # or FSK-style: "Company Name, Industry N"
            if len(segments) >= 1:
                inv_type_keywords = [
                    'loan', 'lien', 'secured', 'subordinat', 'revolv', 'draw',
                    'stock', 'equity', 'unit', 'warrant', 'note', 'bond',
                    'preferred', 'common', 'membership', 'interest',
                    'debt', 'member',
                ]
                # Find the last segment that looks like an investment type
                inv_idx = None
                industry_idx = None
                for i in range(len(segments) - 1, 0, -1):
                    seg_clean = re.sub(r'\s+\d+$', '', segments[i].strip())
                    seg_lower = seg_clean.lower()
                    if any(kw in seg_lower for kw in inv_type_keywords):
                        inv_idx = i
                        break
                    # Check if this segment is a known industry name
                    if seg_lower in _KNOWN_INDUSTRIES:
                        industry_idx = i
                        break

                if inv_idx is not None:
                    result['company_name'] = ', '.join(s.strip() for s in segments[:inv_idx]).strip()
                    result['investment_description'] = re.sub(r'\s+\d+$', '', segments[inv_idx].strip())
                elif industry_idx is not None:
                    # FSK-style: last segment is industry, not investment type
                    result['company_name'] = ', '.join(s.strip() for s in segments[:industry_idx]).strip()
                    result['industry'] = re.sub(r'\s+\d+$', '', segments[industry_idx].strip())
                else:
                    # No clear investment type or industry; treat entire string as company name
                    result['company_name'] = dim_string.strip()

        # If company name not found yet, search for entity suffixes
        if 'company_name' not in result:
            for seg in segments[1:]:
                seg = seg.strip()
                if re.search(r'\b(LLC|Inc\.?|Corp\.?|LP|Ltd\.?|Holdings|Group|Partners)\b', seg, re.I):
                    if not re.match(r'^(Acquisition|Maturity|Investment|Net Assets)', seg, re.I):
                        result['company_name'] = seg
                        break

        # Acquisition date
        acq_match = re.search(r'Acquisition\s+Date\s*[-:]\s*(\d{1,2}/\d{1,2}/\d{2,4})', dim_string, re.I)
        if acq_match:
            result['acquisition_date'] = self._normalize_date(acq_match.group(1))

        # Maturity date
        mat_match = re.search(r'Maturity\s+Date\s*[-:]\s*(\d{1,2}/\d{1,2}/\d{2,4})', dim_string, re.I)
        if mat_match:
            result['maturity_date'] = self._normalize_date(mat_match.group(1))

        # Rate info from parenthetical
        paren_match = re.search(r'\(([^)]+)\)', dim_string)
        if paren_match:
            paren_content = paren_match.group(1)
            rate_spread_match = re.search(
                r'(SOFR|LIBOR|Prime|SONIA|Euribor|CDOR|BKBM|Base\s*Rate)\s*\+\s*([\d.]+)%?',
                paren_content, re.I,
            )
            if rate_spread_match:
                result['reference_rate'] = rate_spread_match.group(1).upper()
                result['spread'] = rate_spread_match.group(2) + '%'

        # Cash rate from description
        cash_rate_match = re.search(
            r'(?:Investment\s*[-:]\s*[^,]*?)\s*[-]\s*([\d.]+)%',
            dim_string, re.I,
        )
        if cash_rate_match:
            result['cash_rate_from_dim'] = cash_rate_match.group(1) + '%'

        # Net Assets percentage
        net_assets_match = re.search(r'Net\s+Assets?\s*[-:]\s*([\d.]+)%', dim_string, re.I)
        if net_assets_match:
            result['percent_of_net_assets'] = net_assets_match.group(1) + '%'

        # Investment sub-type from description (if not already set)
        if 'investment_description' not in result:
            inv_desc_match = re.search(r'Investment\s*[-:]\s*([^,]+)', dim_string, re.I)
            if inv_desc_match:
                inv_desc = inv_desc_match.group(1).strip()
                inv_desc = re.sub(r'\s*[-]\s*[\d.]+%.*$', '', inv_desc).strip()
                result['investment_description'] = inv_desc

        # PIK rate
        pik_match = re.search(r'(\d+\.?\d*)\s*%\s*PIK', dim_string, re.I)
        if pik_match:
            result['pik_rate'] = pik_match.group(1) + '%'

        return result

    @staticmethod
    def _split_segments(text: str, delimiter: str) -> List[str]:
        """Split text on delimiter, but not inside parentheses."""
        segments = []
        depth = 0
        current = []
        for ch in text:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == delimiter and depth == 0:
                segments.append(''.join(current))
                current = []
            else:
                current.append(ch)
        if current:
            segments.append(''.join(current))
        return segments

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """Convert MM/DD/YY or MM/DD/YYYY to YYYY-MM-DD."""
        try:
            parts = date_str.split('/')
            if len(parts) == 3:
                month, day, year = parts
                if len(year) == 2:
                    year_int = int(year)
                    year = str(2000 + year_int) if year_int < 50 else str(1900 + year_int)
                return f"{year}-{int(month):02d}-{int(day):02d}"
        except (ValueError, IndexError):
            pass
        return date_str

    # ------------------------------------------------------------------
    # Map XBRL facts + parsed dimension to CSV row
    # ------------------------------------------------------------------

    def _map_investment_to_csv_row(self, parsed_dim: Dict[str, str],
                                    xbrl_facts: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        """
        Merge parsed dimension string with explicit XBRL facts into a CSV row.
        XBRL facts take precedence over dimension-string-derived values.
        """
        row: Dict[str, str] = {col: '' for col in CSV_COLUMNS}

        # Start with dimension-derived values
        row['company_name'] = parsed_dim.get('company_name', '')
        row['industry'] = parsed_dim.get('industry', '')
        row['acquisition_date'] = parsed_dim.get('acquisition_date', '')
        row['maturity_date'] = parsed_dim.get('maturity_date', '')
        row['percent_of_net_assets'] = parsed_dim.get('percent_of_net_assets', '')
        row['pik_rate'] = parsed_dim.get('pik_rate', '')
        row['floor_rate'] = ''

        # Investment type from dimension (with explicit dimensions for fallback)
        inv_type_raw = parsed_dim.get('investment_type_raw', '')
        inv_desc = parsed_dim.get('investment_description', '')
        explicit_dims = parsed_dim.get('explicit_dimensions', {})
        row['investment_type'] = self._determine_investment_type(inv_type_raw, inv_desc, explicit_dims)

        # Industry from explicit dimensions (if not already in parsed_dim)
        if not row['industry'] and explicit_dims:
            for dim_name, member in explicit_dims.items():
                if 'industry' in dim_name.lower():
                    # Convert CamelCase member name to readable form
                    industry = self._member_to_label(member)
                    if industry:
                        row['industry'] = industry
                        break

        # Reference rate and spread from dimension
        row['reference_rate'] = parsed_dim.get('reference_rate', '')
        row['spread'] = parsed_dim.get('spread', '')
        row['cash_rate'] = parsed_dim.get('cash_rate_from_dim', '')

        # Detect scale from decimals attribute
        scale_divisor = self._detect_scale(xbrl_facts)

        # Override with explicit XBRL fact values
        for fact in xbrl_facts:
            cl = fact['concept_lower']
            val = fact['value']

            # Fair value
            if 'fairvalue' in cl and 'investment' in cl:
                row['fair_value'] = self._format_monetary(val, scale_divisor)
            elif cl in ('investmentatfairvalue', 'investmentownedatfairvalue'):
                row['fair_value'] = self._format_monetary(val, scale_divisor)

            # Cost / amortized cost
            elif 'amortizedcost' in cl:
                row['amortized_cost'] = self._format_monetary(val, scale_divisor)
            elif cl in ('investmentatcost', 'investmentownedatcost'):
                row['cost'] = self._format_monetary(val, scale_divisor)

            # Principal amount
            elif 'principalamount' in cl or 'balanceprincipal' in cl:
                row['principal_amount'] = self._format_monetary(val, scale_divisor)

            # PIK rate (check before generic interest rate so PaidInKind maps here)
            elif ('pik' in cl or 'paidinkind' in cl) and 'rate' in cl:
                row['pik_rate'] = self._format_rate(val)

            # Interest rate (cash rate) — exclude PIK/floor so they map to their own columns
            elif cl in ('investmentinterestrate', 'investmentinterestratecash',
                        'investmentinterestratecontractual', 'investmentinterestrateeffective',
                        'investmentinterestratepaidincash'):
                row['cash_rate'] = self._format_rate(val)

            # Rate during period — only use if non-zero (some BDCs tag 0 for floating rate loans)
            elif cl == 'investmentinterestrateduringperiod':
                try:
                    if float(val) != 0:
                        row['cash_rate'] = self._format_rate(val)
                except (ValueError, TypeError):
                    pass

            # Dividend rate / estimated yield → cash_rate fallback (equity investments)
            elif cl in ('investmentowneddividendrate', 'investmentestimatedyield',
                        'investmentcompanyreturnrate') and not row['cash_rate']:
                row['cash_rate'] = self._format_rate(val)

            # Floor rate (e.g. InvestmentInterestRateFloor)
            elif 'interestratefloor' in cl:
                row['floor_rate'] = self._format_rate(val)

            # Spread
            elif 'basisspread' in cl or 'spreadovervariable' in cl or cl == 'investmentinterestratespread':
                row['spread'] = self._format_rate(val)

            # Variable rate type (reference rate as URL or text) — e.g. SOFR, LIBOR
            elif cl == 'investmentvariableinterestratetypeextensibleenumeration':
                row['reference_rate'] = self._parse_ref_rate_from_url(val)
            elif cl == 'investmentvariableinterestratetype' and not row['reference_rate']:
                row['reference_rate'] = val.strip()

            # Interest rate terms (text description) — parse for rate if no cash_rate yet
            elif cl == 'investmentinterestrateterms':
                if not row['cash_rate']:
                    parsed_rate = self._parse_rate_from_terms(val)
                    if parsed_rate:
                        row['cash_rate'] = parsed_rate

            # Interest rate cap
            elif cl == 'investmentinterestratecap':
                row['rate_cap'] = self._format_rate(val)

            # Shares owned
            elif cl == 'investmentownedbalanceshares':
                row['shares'] = val

            # Percent of net assets
            elif 'percentofnetassets' in cl or 'percentageofnetassets' in cl:
                row['percent_of_net_assets'] = self._format_rate(val)

            # Acquisition date
            elif cl == 'investmentacquisitiondate':
                row['acquisition_date'] = val

            # Maturity date
            elif cl == 'investmentmaturitydate':
                row['maturity_date'] = val

            # Commitment / unfunded
            elif 'unfunded' in cl and 'commitment' in cl:
                row['undrawn_commitment'] = self._format_monetary(val, scale_divisor)
            elif 'commitment' in cl and 'unfunded' not in cl and 'fairvalue' not in cl:
                row['commitment_limit'] = self._format_monetary(val, scale_divisor)

        # If we got amortized cost but not cost, copy it
        if row['amortized_cost'] and not row['cost']:
            row['cost'] = row['amortized_cost']
        elif row['cost'] and not row['amortized_cost']:
            row['amortized_cost'] = row['cost']

        # Infer variable rate from spread: if we have a spread but no reference rate,
        # it's almost certainly a floating rate loan (SOFR since LIBOR sunset)
        if row['spread'] and not row['reference_rate']:
            row['reference_rate'] = 'SOFR'

        # Fact-based investment type inference when type is still 'Other'
        if row['investment_type'] == 'Other':
            has_principal = bool(row.get('principal_amount'))
            has_spread = bool(row.get('spread'))
            has_shares = bool(row.get('shares'))
            if has_principal or has_spread:
                row['investment_type'] = 'First Lien'  # Most BDC debt is first lien
            elif has_shares:
                row['investment_type'] = 'Other Equity'

        # Normalize and validate company name to avoid XBRL dimension leakage
        cleaned_company, extracted_industry = self._sanitize_company_name(
            row.get('company_name', ''),
            parsed_dim.get('is_summary_entry') == '1',
            parsed_dim.get('raw_dimension', ''),
        )
        row['company_name'] = cleaned_company
        if extracted_industry and not row.get('industry'):
            row['industry'] = extracted_industry

        # Skip rows with no company name and no fair value (likely metadata/totals)
        if not row['company_name'] and not row['fair_value']:
            return None
        # Blocklist: investment-type / category strings that must not be used as company name (ARCC and others)
        _NOT_COMPANY_NAMES = frozenset([
            'Debt', 'Subordinated Debt', 'First Lien', 'Second Lien', 'Senior Secured', 'Unsecured Debt',
            'Preferred Equity', 'Common Equity', 'Partnership Interest', 'Revolver', 'Delayed Draw',
            'Senior Secured Loans', 'Equipment Financing', 'Common Equity/Equity Interests/Warrants',
        ])
        cn_stripped = (row['company_name'] or '').strip()
        if cn_stripped in _NOT_COMPANY_NAMES:
            row['company_name'] = ''
        # After clearing, skip if we still have no company and no fair value
        if not row['company_name'] and not row['fair_value']:
            return None
        # SLRC / category-only labels (skip entire row when it's only a header/category)
        _SLRC_CATEGORY_ONLY = frozenset([
            'Senior Secured Loans', 'Equipment Financing', 'Common Equity/Equity Interests/Warrants',
        ])
        if cn_stripped in _SLRC_CATEGORY_ONLY:
            return None
        # Skip rate-only text that leaked as company name (e.g. TSLX "Spread P + 5.25% Interest Rate 12.50%")
        cn = (row['company_name'] or '').strip()
        if cn and re.match(r'^Spread\s+[^\s]+\s*\+\s*[\d.]+%\s+Interest\s+Rate', cn, re.I):
            return None

        # Clean company name: remove commas for CSV compatibility
        row['company_name'] = row['company_name'].replace(',', '')
        if not row['company_name']:
            return None

        return row

    def _sanitize_company_name(
        self,
        company_name: str,
        is_summary_entry: bool = False,
        raw_dimension: str = '',
    ) -> Tuple[str, str]:
        """
        Normalize company names parsed from typed dimensions.
        Returns: (cleaned_company_name, extracted_industry_if_any)
        """
        if not company_name:
            return '', ''

        name = company_name.strip()
        if not name:
            return '', ''

        if is_summary_entry:
            return '', ''

        # Fix missing separator in malformed strings like "Acquia IncIndustry Software"
        name = re.sub(r'(?<=[a-z])Industry\b', ' Industry', name)

        # Strip common leading type/category prefixes:
        # "Unsecured Debt - 1.60% CivicPlus LLC" -> "CivicPlus LLC"
        # "Common Stock - 0.01% Prairie Provident Resources Inc." -> "Prairie Provident Resources Inc."
        prefix_re = (
            r'^\s*(?:'
            r'(?:first|1st|second|2nd)\s+lien'
            r'|senior\s+secured(?:\s+debt)?'
            r'|senior\s+unsecured(?:\s+debt)?'
            r'|subordinated\s+debt'
            r'|unsecured\s+debt'
            r'|debt\s+investments?'
            r'|equity\s+investments?'
            r'|common\s+stock'
            r'|preferred\s+stock'
            r'|warrants?'
            r')\s*-\s*(?:\d+(?:\.\d+)?%?)?\s*'
        )
        name = re.sub(prefix_re, '', name, flags=re.I).strip()

        # Extract trailing "Industry X" leakage and optionally retain X as industry.
        extracted_industry = ''
        m = re.search(r'\bIndustry\s+([A-Za-z&/\-\s]+)$', name, re.I)
        if m:
            candidate = ' '.join(m.group(1).split()).strip()
            if candidate:
                extracted_industry = candidate
            name = name[:m.start()].strip()

        # Try to recover from raw typed dimension when parsed company looks truncated/generic.
        if self._looks_truncated_company_name(name):
            recovered = self._recover_company_name_from_raw_dimension(raw_dimension)
            if recovered:
                name = recovered

        # Reject obvious non-company summary labels and percent-only artifacts.
        if not name:
            return '', extracted_industry
        if name in self._SUMMARY_COMPANY_LABELS:
            return '', extracted_industry
        if name.lower().startswith('total '):
            return '', extracted_industry
        if name.lower().startswith('subtotal '):
            return '', extracted_industry
        if re.match(r'^(?:Control\s+and\s+)?Affiliate\s+Investments?$', name, re.I):
            return '', extracted_industry
        if re.match(r'^-?\s*\d+(?:\.\d+)?%$', name):
            return '', extracted_industry
        if re.match(r'^-\s*\d+(?:\.\d+)?%$', name):
            return '', extracted_industry

        return name, extracted_industry

    @staticmethod
    def _looks_truncated_company_name(name: str) -> bool:
        """
        Detect obviously incomplete/generic names that should be recovered from raw dimension text.
        Examples: "Group LLC", "te Holdings)", "Mgmt & Development".
        """
        if not name:
            return True
        n = name.strip()
        if not n:
            return True

        if n[0].islower():
            return True
        if n.endswith(')') and '(' not in n:
            return True
        if re.match(r'^(?:Group|Holdings?|Management|Mgmt\.?\s*&\s*Development)\b', n, re.I):
            return True
        if len(n) <= 14 and re.search(r'\b(?:Group|Holdings?|Development)\b', n, re.I):
            return True
        return False

    def _recover_company_name_from_raw_dimension(self, raw_dimension: str) -> str:
        """Recover a likely full company name from raw dimension text."""
        if not raw_dimension:
            return ''

        segments = [s.strip() for s in self._split_segments(raw_dimension, ',') if s and s.strip()]
        if not segments:
            return ''

        # Find best contiguous segment window ending in a legal entity suffix.
        best = ''
        stop_prefix_re = re.compile(
            r'^(?:Acquisition|Maturity|Investment|Net Assets|Reference|Total|'
            r'Debt Investments?|Equity Investments?|Common Stock|Preferred Stock|'
            r'Unsecured Debt)\b',
            re.IGNORECASE,
        )
        metadata_segment_re = re.compile(
            r'^(?:Investment|Non-?Affiliated Issuer|Affiliated Issuer|Non-?Controlled|Controlled|'
            r'First Lien Debt|Second Lien Debt|Subordinated Debt|Unsecured Debt|'
            r'Debt Investments?|Equity Investments?|Investment Funds)\b',
            re.IGNORECASE,
        )

        for end in range(len(segments)):
            if not self._LEGAL_ENTITY_SUFFIX_RE.search(segments[end]):
                continue
            for start in range(max(0, end - 4), end + 1):
                effective_start = start
                while effective_start <= end and metadata_segment_re.match(segments[effective_start]):
                    effective_start += 1
                if effective_start > end:
                    continue
                candidate = ', '.join(segments[effective_start:end + 1]).strip()
                if not candidate or stop_prefix_re.match(candidate):
                    continue
                # Avoid grabbing the leading "Investment, Non-Affiliated Issuer, ..."
                if candidate.lower().startswith('investment, '):
                    continue
                # Prefer longer but not absurdly long candidates.
                if 4 <= len(candidate) <= 140 and len(candidate) > len(best):
                    best = candidate

        if not best:
            return ''

        # Final cleanup
        best = re.sub(r'\s+', ' ', best).strip()
        best = re.sub(r'\s+\d+\s*$', '', best)  # trailing item indices
        return best

    @staticmethod
    def _row_identity_key(row: Dict[str, str]) -> Tuple[str, str, str, str, str, str, str]:
        """Stable key for exact/super-close duplicate row removal."""
        company_key = re.sub(r'[^a-z0-9]+', ' ', (row.get('company_name') or '').lower()).strip()
        inv_type = (row.get('investment_type') or '').strip().lower()
        principal = (row.get('principal_amount') or '').strip()
        fair = (row.get('fair_value') or '').strip()
        cost = (row.get('cost') or '').strip()
        amortized = (row.get('amortized_cost') or '').strip()
        maturity = (row.get('maturity_date') or '').strip()
        return (company_key, inv_type, principal, fair, cost, amortized, maturity)

    @staticmethod
    def _row_score(row: Dict[str, str]) -> int:
        """Prefer rows with more populated fields when resolving duplicates."""
        return sum(1 for v in row.values() if str(v).strip())

    def _deduplicate_output_rows(self, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Remove exact duplicate position rows emitted from repeated XBRL contexts."""
        best_by_key: Dict[Tuple[str, str, str, str, str, str, str], Dict[str, str]] = {}
        for row in rows:
            key = self._row_identity_key(row)
            if not key[0]:
                continue
            existing = best_by_key.get(key)
            if existing is None or self._row_score(row) > self._row_score(existing):
                best_by_key[key] = row
        return list(best_by_key.values())

    @staticmethod
    def _member_to_label(member_name: str) -> str:
        """
        Convert XBRL CamelCase member name to a readable label.
        E.g. 'SoftwareAndServicesMember' → 'Software And Services'
             'HealthCareEquipmentAndServicesSectorMember' → 'Health Care Equipment And Services'
        """
        # Strip common suffixes
        name = re.sub(r'(?:Sector)?Member$', '', member_name)
        # Strip ticker prefix (e.g., 'arcc:', 'fsk:')
        if ':' in name:
            name = name.rsplit(':', 1)[-1]
        # Insert spaces before capitals (CamelCase → spaced)
        name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
        name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
        return name.strip()

    def _determine_investment_type(self, inv_type_raw: str, inv_desc: str,
                                    explicit_dims: Optional[Dict[str, str]] = None) -> str:
        """Determine investment type from XBRL dimension info."""
        desc_lower = inv_desc.lower() if inv_desc else ''
        type_raw_lower = inv_type_raw.lower() if inv_type_raw else ''

        # Check description first for specifics
        if 'delayed draw' in desc_lower or 'ddtl' in desc_lower:
            return 'Delayed Draw'
        if 'revolv' in desc_lower:
            return 'Revolver'
        if 'second lien' in desc_lower or '2nd lien' in desc_lower:
            return 'Second Lien'
        if 'first lien' in desc_lower or '1st lien' in desc_lower or 'senior secured' in desc_lower:
            return 'First Lien'
        if 'subordinated' in desc_lower or 'mezzanine' in desc_lower:
            return 'Subordinated Debt'
        if 'unsecured' in desc_lower:
            return 'Unsecured Debt'
        if 'term loan' in desc_lower:
            return 'First Lien'
        if 'unitranche' in desc_lower:
            return 'First Lien'
        if 'secured debt' in desc_lower or 'secured loan' in desc_lower:
            return 'First Lien'  # Most BDC secured debt is first lien
        if 'partnership interest' in desc_lower or 'lp interest' in desc_lower:
            return 'Partnership Interest'
        if 'preferred member' in desc_lower or 'preferred unit' in desc_lower or 'preferred equity' in desc_lower or 'preferred stock' in desc_lower:
            return 'Preferred Equity'
        if 'common equity' in desc_lower or 'common stock' in desc_lower or 'common unit' in desc_lower:
            return 'Common Equity'
        if 'member unit' in desc_lower or 'membership interest' in desc_lower or 'member interest' in desc_lower:
            return 'Other Equity'
        if 'warrant' in desc_lower:
            return 'Warrants'

        # Check explicit dimensions for investment type axis
        if explicit_dims:
            for dim_name, member in explicit_dims.items():
                if 'investmenttype' in dim_name.lower() or 'type' in dim_name.lower():
                    member_lower = member.lower()
                    if 'firstlien' in member_lower or 'seniorsecured' in member_lower:
                        return 'First Lien'
                    if 'secondlien' in member_lower:
                        return 'Second Lien'
                    if 'subordinated' in member_lower or 'mezzanine' in member_lower:
                        return 'Subordinated Debt'
                    if 'unsecured' in member_lower:
                        return 'Unsecured Debt'
                    if 'equity' in member_lower and 'preferred' in member_lower:
                        return 'Preferred Equity'
                    if 'equity' in member_lower:
                        return 'Other Equity'
                    if 'warrant' in member_lower:
                        return 'Warrants'

        # Check raw type
        if type_raw_lower == 'debt':
            return 'First Lien'  # Default debt type for BDCs
        if type_raw_lower == 'equity':
            if 'preferred' in desc_lower:
                return 'Preferred Equity'
            if 'common' in desc_lower:
                return 'Common Equity'
            if 'warrant' in desc_lower:
                return 'Warrants'
            return 'Other Equity'
        if type_raw_lower == 'preferred':
            return 'Preferred Equity'
        if type_raw_lower == 'warrant':
            return 'Warrants'
        if type_raw_lower == 'common':
            return 'Common Equity'

        # Broader keyword fallbacks before giving up
        if desc_lower:
            if 'preferred' in desc_lower:
                return 'Preferred Equity'
            if 'warrant' in desc_lower:
                return 'Warrants'
            if any(kw in desc_lower for kw in ('unit', 'interest', 'share', 'equity', 'stock', 'common', 'class ')):
                return 'Other Equity'
            if any(kw in desc_lower for kw in ('loan', 'lien', 'secured', 'note', 'bond', 'debt')):
                return 'First Lien'
        return 'Other'

    def _detect_scale(self, facts: List[Dict[str, Any]]) -> float:
        """
        Detect whether monetary values need scaling.

        XBRL monetary values are in absolute dollars. Existing CSVs use thousands.
        If values look like absolute dollars (> 1M), divide by 1000.

        Returns divisor (1000 if values are in absolute dollars, 1 if already scaled).
        """
        monetary_values = []
        for fact in facts:
            cl = fact['concept_lower']
            if any(kw in cl for kw in ('fairvalue', 'cost', 'amortizedcost', 'principalamount', 'balanceprincipal')):
                try:
                    val = float(fact['value'])
                    if abs(val) > 0:
                        monetary_values.append(abs(val))
                except (ValueError, TypeError):
                    pass

        if not monetary_values:
            return 1.0

        # Check decimals attribute for hints
        for fact in facts:
            decimals = fact.get('decimals', '')
            if decimals and decimals not in ('INF', 'inf'):
                try:
                    dec_val = int(decimals)
                    # decimals=-3 means "rounded to nearest 1000" - value is still in full dollars (e.g. 7099000 = $7,099,000)
                    if dec_val == -3:
                        return 1000.0
                    # decimals=0 or positive means absolute dollars
                    if dec_val >= 0:
                        return 1000.0
                except (ValueError, TypeError):
                    pass

        # Heuristic: if median value > 100,000, likely absolute dollars
        median_val = sorted(monetary_values)[len(monetary_values) // 2]
        if median_val > 100_000:
            return 1000.0

        return 1.0

    @staticmethod
    def _format_monetary(value: str, scale_divisor: float) -> str:
        """Format a monetary XBRL value, applying scale conversion."""
        try:
            val = float(value)
            if scale_divisor != 1.0:
                val = val / scale_divisor
            # Round to nearest integer (thousands)
            return str(round(val))
        except (ValueError, TypeError):
            return value

    @staticmethod
    def _format_rate(value: str) -> str:
        """
        Format a rate value from XBRL.
        XBRL rates are typically decimals (0.1083 = 10.83%).
        """
        try:
            val = float(value)
            if abs(val) < 1.0:
                # Decimal -> percentage
                pct = val * 100
                # Format cleanly: remove trailing zeros
                formatted = f"{pct:.2f}".rstrip('0').rstrip('.')
                return formatted + '%'
            elif abs(val) < 100:
                # Already a percentage
                formatted = f"{val:.2f}".rstrip('0').rstrip('.')
                return formatted + '%'
            else:
                return value
        except (ValueError, TypeError):
            return value

    @staticmethod
    def _parse_ref_rate_from_url(value: str) -> str:
        """
        Parse reference rate from XBRL extensible enumeration URL.
        E.g. 'http://.../2024/InvestmentVariableRateType/SOFR' → 'SOFR'
        Also handles plain text values like 'SOFR' or 'Prime'.
        """
        val = value.strip()
        if '/' in val:
            # URL format — take last path segment
            ref = val.rsplit('/', 1)[-1].upper()
        else:
            ref = val.upper()
        # Normalize common variants
        for name in ('SOFR', 'LIBOR', 'PRIME', 'EURIBOR', 'SONIA', 'AMERIBOR', 'CDOR'):
            if name in ref:
                return name
        return ref if ref else ''

    @staticmethod
    def _parse_rate_from_terms(value: str) -> str:
        """
        Parse a rate from free-text interest rate terms.
        E.g. 'Fixed interest rate 19.0%; EOT 0.0%' → '19%'
             'S + 5.25% cash, 2.00% PIK' → '5.25%'
             'SOFR + 6.50%' → '' (spread, not total rate)
        Returns formatted rate string or empty string.
        """
        val = value.strip()
        if not val:
            return ''

        # Look for "Fixed interest rate X%" or "Fixed Rate: X%"
        m = re.search(r'[Ff]ixed\s+(?:interest\s+)?rate[:\s]+(\d+\.?\d*)%?', val)
        if m:
            rate = float(m.group(1))
            formatted = f"{rate:.2f}".rstrip('0').rstrip('.')
            return formatted + '%'

        # Look for explicit cash rate: "X% cash"
        m = re.search(r'(\d+\.?\d*)%?\s+cash', val, re.IGNORECASE)
        if m:
            rate = float(m.group(1))
            formatted = f"{rate:.2f}".rstrip('0').rstrip('.')
            return formatted + '%'

        # If it's just a plain number with % and no SOFR/spread context, use it
        if 'sofr' not in val.lower() and 'libor' not in val.lower() and '+' not in val:
            m = re.search(r'(\d+\.?\d*)%', val)
            if m:
                rate = float(m.group(1))
                if 0 < rate < 50:  # sanity check
                    formatted = f"{rate:.2f}".rstrip('0').rstrip('.')
                    return formatted + '%'

        return ''

    # ------------------------------------------------------------------
    # Filing processing
    # ------------------------------------------------------------------

    def process_filing(self, ticker: str, filing_type: str = "10-Q",
                       year: Optional[int] = None,
                       index_url: Optional[str] = None) -> Optional[str]:
        """
        Process a single SEC filing to extract XBRL Schedule of Investments.

        Args:
            ticker: Company ticker symbol
            filing_type: "10-Q" or "10-K"
            year: Year to fetch filing for (ignored if index_url provided)
            index_url: Specific filing index URL to process

        Returns:
            Path to generated CSV file, or None if no XBRL SOI data
        """
        logger.info(f"Processing {ticker} {filing_type} via XBRL (year={year or 'latest'})")

        try:
            if not index_url:
                index_url = self.sec_client.get_filing_index_url(
                    ticker=ticker, filing_type=filing_type, year=year,
                )
            if not index_url:
                logger.warning(f"No {filing_type} filing found for {ticker}")
                return None

            # Fetch filing documents (only XML and HTM to find XBRL)
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=index_url, ticker=ticker, filing_type=filing_type,
                save_to_file=False, document_types=['.xml', '.htm', '.html'],
            )
            if not filing_result:
                logger.warning(f"Could not fetch filing documents from {index_url}")
                return None

            # Find XBRL instance document
            xbrl_url = self.find_xbrl_instance_document(filing_result)
            if not xbrl_url:
                logger.warning("No XBRL instance document found")
                return None

            # Determine period end date
            period_end_date = getattr(filing_result, 'period_end_date', None)
            if not period_end_date:
                # Try to get from cached data
                period_end_date = getattr(self.sec_client, '_cached_period_date', {}).get(index_url)
            if not period_end_date:
                logger.warning("No period_end_date available; using filing_date as fallback")
                period_end_date = filing_result.filing_date

            # Download and parse XBRL
            logger.info(f"Downloading XBRL instance document: {xbrl_url}")
            response = _sec_get(xbrl_url, headers=self.sec_client.headers)
            response.raise_for_status()
            xbrl_content = response.content
            logger.info(f"XBRL document size: {len(xbrl_content) / (1024*1024):.2f} MB")

            debug_out: Optional[Dict[str, Any]] = None
            if self.debug_dir:
                debug_out = {}
                # Save raw XBRL (cap at 3MB for huge filings so the file is inspectable)
                xbrl_debug_path = self.debug_dir / f"{ticker}_{filing_result.filing_date}_xbrl_instance.xml"
                max_save = 3 * 1024 * 1024
                if len(xbrl_content) <= max_save:
                    xbrl_debug_path.write_bytes(xbrl_content)
                    logger.info(f"Saved full XBRL instance ({len(xbrl_content)/1024:.1f} KB) to {xbrl_debug_path}")
                else:
                    xbrl_debug_path.write_bytes(xbrl_content[:max_save])
                    (self.debug_dir / f"{ticker}_{filing_result.filing_date}_xbrl_truncated_note.txt").write_text(
                        f"Full XBRL size: {len(xbrl_content)/1024/1024:.2f} MB. Only first 3 MB saved for inspection.\n",
                        encoding="utf-8",
                    )
                    logger.info(f"Saved first 3 MB of XBRL to {xbrl_debug_path} (full size {len(xbrl_content)/1024/1024:.2f} MB)")

            rows = self.parse_xbrl_investments(xbrl_content, period_end_date, debug_out=debug_out)

            if self.debug_dir and debug_out:
                debug_json_path = self.debug_dir / f"{ticker}_{filing_result.filing_date}_parsed_debug.json"
                with open(debug_json_path, "w", encoding="utf-8") as f:
                    json.dump(debug_out, f, indent=2)
                logger.info(f"Saved parsed contexts/facts sample to {debug_json_path}")
                readme = self.debug_dir / "README.txt"
                if not readme.exists():
                    readme.write_text(
                        "XBRL debug artifacts (from --debug-dir).\n"
                        "- *_xbrl_instance.xml: raw XBRL instance document (up to 3 MB).\n"
                        "- *_parsed_debug.json: contexts, facts_sample, facts_by_concept, "
                        "investment_facts_by_dim_sample, period_end_date.\n",
                        encoding="utf-8",
                    )

            if not rows:
                logger.info(f"No XBRL Schedule of Investments data found for {ticker} {filing_type}")
                return None

            # Enrich missing industry / maturity_date from the HTML table
            rows = self._html_enrich_rows(rows, filing_result, ticker, period_end_date)

            # FSK-specific: XBRL emits two contexts for the same position — one with
            # full financials (cost, amortized_cost, shares) and one sparse (fair_value only).
            # The standard dedup key includes cost/amortized_cost so they survive as separate
            # rows. Re-dedup using only (company, type, principal, fair, maturity) and keep
            # the richer row.
            if ticker == 'FSK':
                before = len(rows)
                soft_key: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
                for r in rows:
                    ck = re.sub(r'[^a-z0-9]+', ' ', (r.get('company_name') or '').lower()).strip()
                    key = (
                        ck,
                        (r.get('investment_type') or '').strip().lower(),
                        (r.get('principal_amount') or '').strip(),
                        (r.get('fair_value') or '').strip(),
                        (r.get('maturity_date') or '').strip(),
                    )
                    existing = soft_key.get(key)
                    if existing is None or self._row_score(r) > self._row_score(existing):
                        soft_key[key] = r
                rows = list(soft_key.values())
                if len(rows) != before:
                    logger.info("FSK soft dedup: removed %d sparse-context duplicate rows", before - len(rows))

            # Write CSV
            output_path = self._save_csv(rows, ticker, filing_result.filing_date)
            logger.info(f"Saved {len(rows)} investment rows to {output_path}")
            return str(output_path)

        except Exception as e:
            logger.error(f"Error processing XBRL filing for {ticker}: {e}", exc_info=True)
            return None

    def process_historical_filings(self, ticker: str, years_back: int = 1,
                                    skip_existing: bool = True) -> List[str]:
        """
        Process multiple historical filings for a ticker using XBRL.
        Fetches both 10-Q and 10-K filings and processes in parallel by year.

        Returns list of paths to generated CSV files.
        """
        logger.info(f"Processing historical XBRL filings for {ticker} (last {years_back} years)")

        filings_10q = self.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
        filings_10k = self.sec_client.get_historical_10k_filings(ticker, years_back=years_back)
        filings = list(filings_10q) + list(filings_10k)
        filings.sort(key=lambda f: f['date'])

        if not filings:
            logger.warning(f"No filings found for {ticker}")
            return []

        # Group by year
        by_year: Dict[int, List[Dict[str, Any]]] = {}
        for f in filings:
            year = int(f['date'][:4])
            by_year.setdefault(year, []).append(f)
        for year in by_year:
            by_year[year].sort(key=lambda x: x['date'])
        years_sorted = sorted(by_year.keys())

        def process_one_year(year: int) -> List[str]:
            year_results = []
            for f in by_year[year]:
                filing_date = f['date']
                if skip_existing:
                    existing = self.output_dir / f"{ticker}_investments_{filing_date}.csv"
                    if existing.exists() and existing.stat().st_size > 100:
                        logger.info(f"SKIPPING {ticker} {filing_date} - already exists")
                        year_results.append(str(existing))
                        continue
                logger.info(f"[{year}] Processing {f.get('form', '10-Q')} from {filing_date}")
                csv_path = self.process_filing(
                    ticker, f.get('form', '10-Q'), index_url=f['index_url'],
                )
                if csv_path:
                    year_results.append(csv_path)
            return year_results

        num_workers = len(years_sorted)
        logger.info(f"Running {num_workers} year-workers (years {years_sorted[0]}-{years_sorted[-1]})")
        results = []
        with ThreadPoolExecutor(max_workers=max(num_workers, 1)) as executor:
            future_to_year = {executor.submit(process_one_year, y): y for y in years_sorted}
            for future in as_completed(future_to_year):
                y = future_to_year[future]
                try:
                    year_results = future.result()
                    results.extend(year_results)
                except Exception as e:
                    logger.exception(f"Year {y} failed: {e}")

        results.sort(key=lambda p: Path(p).stem.replace(f"{ticker}_investments_", ""))
        return results

    # ------------------------------------------------------------------
    # HTML enrichment: fill missing industry / maturity_date
    # ------------------------------------------------------------------

    def _get_main_html_content(self, filing_result) -> Optional[str]:
        """
        Return the main HTML filing document (no exhibits) for SOI enrichment.
        """
        text_map = getattr(filing_result, 'text_map', {}) or {}
        for fn, content in text_map.items():
            fn_lower = fn.lower()
            if not (fn_lower.endswith('.htm') or fn_lower.endswith('.html')):
                continue
            if re.search(r'ex\d{2,}', fn_lower):
                continue
            if any(x in fn_lower for x in ('_htm.xml', 'xbrl.htm', 'xbrl.html', 'instance')):
                continue
            if content and len(content) > 5000:
                logger.info('HTML enrichment: using %s from text_map (%d KB)', fn, len(content) // 1024)
                return content
        return None

    def _get_all_html_contents_for_enrichment(self, filing_result) -> List[Tuple[str, str]]:
        """
        Return list of (filename, html_content) for SOI enrichment: main doc first,
        then exhibits by size (largest first). Caller can try each until industry_map is large enough.
        """
        text_map = getattr(filing_result, 'text_map', {}) or {}
        main_doc = None
        exhibits = []
        for fn, content in text_map.items():
            if not content or len(content) < 3000:
                continue
            fn_lower = fn.lower()
            if not (fn_lower.endswith('.htm') or fn_lower.endswith('.html')):
                continue
            if any(x in fn_lower for x in ('_htm.xml', 'xbrl.htm', 'xbrl.html', 'instance')):
                continue
            # Exhibit detection: "ex\d{2,}" OR "exhibit" anywhere in name
            is_exhibit = bool(re.search(r'ex\d{2,}|exhibit', fn_lower))
            if is_exhibit:
                exhibits.append((fn, content))
            else:
                # Main doc: prefer the largest non-exhibit (typical SOI filing HTML is huge)
                if main_doc is None or len(content) > len(main_doc[1]):
                    main_doc = (fn, content)
        # Main first, then exhibits by size (SOI exhibits are often large)
        result = []
        if main_doc:
            result.append(main_doc)
        exhibits.sort(key=lambda x: -len(x[1]))
        result.extend(exhibits)
        return result

    def _html_enrich_rows(
        self,
        rows: List[Dict[str, str]],
        filing_result,
        ticker: str,
        period_end: str,
    ) -> List[Dict[str, str]]:
        """
        Fill missing *industry* and *maturity_date* fields in XBRL-extracted rows
        by parsing the HTML SOI table from the same filing.

        Triggered only when a meaningful fraction of rows are missing these fields
        (>5 % industry missing OR >20 % maturity_date missing).  Uses the HTML
        already cached in filing_result.text_map — no extra network requests.
        """
        if not rows:
            return rows

        n = len(rows)
        n_miss_ind = sum(1 for r in rows if not r.get('industry'))
        n_miss_mat = sum(1 for r in rows if not r.get('maturity_date'))

        # Only enrich when a meaningful fraction of rows are missing data
        if n_miss_ind < max(1, n // 20) and n_miss_mat < max(1, n // 5):
            return rows

        logger.info(
            'HTML enrichment triggered: %d/%d rows missing industry, %d/%d missing maturity_date',
            n_miss_ind, n, n_miss_mat, n,
        )

        from html_soi_parser import extract_soi_enrichment, _company_key, _principal_key, _is_numeric_industry

        # Try main doc first, then exhibits (ARCC and others often put SOI in an exhibit)
        html_candidates = self._get_all_html_contents_for_enrichment(filing_result)
        if not html_candidates:
            logger.warning('HTML enrichment: no HTML documents in filing_result.text_map (only XML or empty)')
        else:
            logger.info('HTML enrichment: trying %d HTML document(s): %s', len(html_candidates), [fn for fn, _ in html_candidates])
        industry_map: Dict[str, str] = {}
        maturity_by_principal: Dict[Tuple[str, str], str] = {}
        maturity_default: Dict[str, str] = {}

        for fn, html_content in html_candidates:
            try:
                ind_map, mat_by_prin, mat_default = extract_soi_enrichment(
                    html_content, ticker, period_end,
                )
                # Prefer the document that gives the most industry coverage
                if ind_map and len(ind_map) > len(industry_map):
                    industry_map = ind_map
                    logger.info('HTML enrichment: using %s (%d companies with industry)', fn, len(ind_map))
                if mat_by_prin and (not maturity_by_principal or len(mat_by_prin) > len(maturity_by_principal)):
                    maturity_by_principal = mat_by_prin
                if mat_default:
                    for k, v in mat_default.items():
                        if k not in maturity_default:
                            maturity_default[k] = v
                if not ind_map and not mat_by_prin and not mat_default:
                    logger.debug('HTML enrichment: %s produced no lookup data (no SOI tables or no industry column)', fn)
            except Exception as exc:
                logger.warning('HTML enrichment failed for %s: %s', fn, exc)
                continue

        # Guardrail for ARCC: if one industry label dominates the map, treat it as unreliable
        if industry_map and ticker.upper() == 'ARCC':
            counts = Counter(industry_map.values())
            total = sum(counts.values())
            if total:
                top_label, top_count = counts.most_common(1)[0]
                if top_count / total > 0.7:
                    logger.warning(
                        'HTML enrichment: ARCC industry map dominated by "%s" (%d/%d); ignoring industry enrichment',
                        top_label, top_count, total,
                    )
                    industry_map = {}

        if not industry_map and not maturity_by_principal and not maturity_default:
            logger.info('HTML enrichment: no lookup data could be extracted from any HTML document')
            return rows

        # Debug: show sample keys from each side to diagnose mismatches
        if industry_map:
            xbrl_keys = set(_company_key((r.get('company_name') or '').strip()) for r in rows if r.get('company_name'))
            html_keys = set(industry_map.keys())
            overlap = xbrl_keys & html_keys
            if not overlap:
                sample_xbrl = sorted(xbrl_keys)[:5]
                sample_html = sorted(html_keys)[:5]
                logger.warning(
                    'HTML enrichment: 0 key overlap! XBRL samples: %s | HTML samples: %s',
                    sample_xbrl, sample_html,
                )

        enriched_ind = enriched_mat = 0
        for row in rows:
            cn = (row.get('company_name') or '').strip()
            if not cn:
                continue
            ck = _company_key(cn)

            # Fill missing industry (skip numeric leakage e.g. % of net assets)
            if not row.get('industry') and ck in industry_map:
                ind_val = industry_map[ck]
                if not _is_numeric_industry(ind_val):
                    row['industry'] = ind_val
                    enriched_ind += 1

            # Fill missing maturity_date
            if not row.get('maturity_date'):
                pk = _principal_key(row.get('principal_amount') or '')
                mat = maturity_by_principal.get((ck, pk))
                if not mat and not pk and row.get('fair_value'):
                    # Fallback: try matching on fair_value when principal is absent
                    mat = maturity_by_principal.get(
                        (ck, _principal_key(row.get('fair_value') or ''))
                    )
                if not mat:
                    mat = maturity_default.get(ck)
                if mat:
                    row['maturity_date'] = mat
                    enriched_mat += 1

        logger.info(
            'HTML enrichment filled: %d industry, %d maturity_date (out of %d rows)',
            enriched_ind, enriched_mat, n,
        )
        return rows

    # ------------------------------------------------------------------
    # CSV output
    # ------------------------------------------------------------------

    def _save_csv(self, rows: List[Dict[str, str]], ticker: str, filing_date: str) -> Path:
        """Save investment rows to CSV in the standard 16-column format."""
        filename = f"{ticker}_investments_{filing_date}.csv"
        output_path = self.output_dir / filename

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        return output_path


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    _script_dir = Path(__file__).resolve().parent
    _repo_root = _script_dir.parent.parent
    _project_output = _repo_root / "output"
    _project_data = _repo_root / "data"

    parser = argparse.ArgumentParser(
        description="Extract Schedule of Investments from SEC filings using XBRL data"
    )
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--years-back', type=int, default=1,
                        help='Number of years to look back (default: 1)')
    parser.add_argument('--force', action='store_true',
                        help='Re-process filings even if output CSV exists')
    parser.add_argument('--output-dir', default=str(_project_output),
                        help='Output directory for CSV files')
    parser.add_argument('--data-dir', default=str(_project_data),
                        help='SEC download cache directory')
    parser.add_argument('--debug-dir', default=None,
                        help='If set, save raw XBRL instance and parsed contexts/facts sample for inspection')
    parser.add_argument('--year', type=int, default=None,
                        help='Process a single year (instead of years-back range)')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K'],
                        help='Filing type for single-year processing')

    args = parser.parse_args()

    try:
        extractor = XBRLInvestmentExtractor(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            debug_dir=args.debug_dir,
        )

        if args.year:
            result = extractor.process_filing(
                ticker=args.ticker, filing_type=args.filing_type, year=args.year,
            )
            if result:
                print(f"XBRL extraction succeeded: {result}")
            else:
                print(f"No XBRL Schedule of Investments data found for {args.ticker}")
                exit(1)
        else:
            results = extractor.process_historical_filings(
                ticker=args.ticker,
                years_back=args.years_back,
                skip_existing=not args.force,
            )
            if results:
                print(f"Successfully processed {len(results)} filings via XBRL for {args.ticker}")
                for r in results:
                    print(f"  - {r}")
            else:
                print(f"No XBRL SOI data found for {args.ticker} in the last {args.years_back} years")
                exit(1)

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        exit(1)


if __name__ == "__main__":
    main()
