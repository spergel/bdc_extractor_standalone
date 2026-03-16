#!/usr/bin/env python3
"""
Build company profiles (description, industry, leadership, funding, news) for resolved companies.
Run after resolve_companies.py. Reads companies_index.json, optionally enriches via:
- Tavily (web search) + Gemini: description, leadership, funding, recent_news
- OpenAI: description only (fallback if no Tavily/Gemini)

Uses GOOGLE_API_KEY (Gemini) and TAVILY_API_KEY from environment; loads .env if present.
"""

import csv
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    _repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_repo_root / ".env")
except ImportError:
    pass

# Allow running from repo root
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.processing.standardization_rules import ALLOWED_INDUSTRIES, normalize_industry

logger = logging.getLogger(__name__)


def setup_logging(log_dir: Path) -> Optional[Path]:
    """Add a timestamped log file under log_dir. Call after basicConfig. Returns log file path if set."""
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_file = log_dir / f"build_profiles_{timestamp}.log"
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)
        logger.info("Logging to %s", log_file)
        return log_file
    except Exception as e:
        logger.warning("Could not add file handler: %s", e)
        return None

STALE_DAYS = 30
PROFILE_SCHEMA_KEYS = (
    "company_id",
    "canonical_name",
    "description",
    "industry",
    "industry_initial",
    "website",
    "location",
    "employee_range",
    "leadership",
    "funding",
    "recent_news",
    "source",
    "updated_at",
)


def load_companies_index(data_dir: Path) -> list:
    path = data_dir / "companies_index.json"
    if not path.exists():
        logger.warning("No companies_index.json at %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("companies", [])


def load_companies_from_csv(csv_path: Path, limit: Optional[int] = None) -> list:
    """
    Load company_id + canonical_name from a CSV (e.g. GSBD_unique_companies.csv).
    Only includes rows with company_id starting with 'co_' to skip junk rows.
    """
    if not csv_path.exists():
        logger.warning("Companies CSV not found: %s", csv_path)
        return []
    out = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            if not cid.startswith("co_"):
                continue
            canonical = (row.get("canonical_name") or row.get("company_name") or "").strip()
            if not canonical:
                canonical = cid
            out.append({"company_id": cid, "canonical_name": canonical})
            if limit and len(out) >= limit:
                break
    return out


# Names that are section headers / subtotals / CLOs, not operating companies (excluded from top-by-exposure)
_TOP_EXPOSURE_SKIP_PATTERNS = (
    "sub total", "subtotal", "total investments", "total portfolio",
    "investment non-affiliated issuer", "non-affiliate", "non-control",
    "senior secured loans", "equity interests", "equity/warrants",
    "equipment financing", "first lien bank debt",
)


def load_companies_from_exposures_top(
    data_dir: Path, limit: int = 10, min_num_bdcs: int = 2
) -> list:
    """
    Load top N companies by total_exposure_millions from company_exposures.csv.
    Skips rows whose company_name looks like a section header or subtotal.
    When min_num_bdcs >= 2, only considers companies with that many BDCs (avoids single-BDC funds/programs).
    Returns list of {company_id, canonical_name} sorted by exposure descending.
    """
    path = data_dir / "company_exposures.csv"
    if not path.exists():
        logger.warning("company_exposures.csv not found at %s", path)
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            if not cid.startswith("co_"):
                continue
            name = (row.get("company_name") or "").strip() or cid
            name_lower = name.lower()
            if any(p in name_lower for p in _TOP_EXPOSURE_SKIP_PATTERNS):
                continue
            try:
                num_bdcs = int((row.get("num_bdcs_invested") or "0").strip())
            except (ValueError, TypeError):
                num_bdcs = 0
            if num_bdcs < min_num_bdcs:
                continue
            try:
                exp = float((row.get("total_exposure_millions") or "0").replace(",", ""))
            except (ValueError, TypeError):
                exp = 0.0
            rows.append({"company_id": cid, "canonical_name": name, "_exposure": exp})
    rows.sort(key=lambda x: -x["_exposure"])
    out = [{"company_id": r["company_id"], "canonical_name": r["canonical_name"]} for r in rows[:limit]]
    logger.info("Top %d by exposure (min %d BDCs): %s", len(out), min_num_bdcs, [r["canonical_name"] for r in out])
    return out


def load_companies_from_ticker(data_dir: Path, ticker: str, limit: Optional[int] = None) -> list:
    """
    Load first N unique companies (by company_id) from a BDC's investment CSV (e.g. CION.csv).
    Order is first appearance in the file. Canonical names come from companies_index.json.
    """
    inv_path = data_dir / "investments" / f"{ticker.strip().upper()}.csv"
    if not inv_path.exists():
        logger.warning("Investment file not found: %s", inv_path)
        return []
    cids_seen: List[str] = []
    seen = set()
    with open(inv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            if not cid.startswith("co_") or cid in seen:
                continue
            seen.add(cid)
            cids_seen.append(cid)
            if limit and len(cids_seen) >= limit:
                break
    cid_to_canonical: Dict[str, str] = {}
    for c in load_companies_index(data_dir):
        cid = (c.get("company_id") or "").strip()
        canonical = (c.get("canonical_name") or "").strip()
        if cid:
            cid_to_canonical[cid] = canonical or cid
    return [
        {"company_id": cid, "canonical_name": cid_to_canonical.get(cid, cid)}
        for cid in cids_seen
    ]


def skeleton_profile(company_id: str, canonical_name: str) -> Dict[str, Any]:
    return {
        "company_id": company_id,
        "canonical_name": canonical_name,
        "description": "",
        "industry": "",
        "industry_initial": "",
        "website": "",
        "location": "",
        "employee_range": "",
        "leadership": "",
        "funding": "",
        "recent_news": "",
        "source": "skeleton",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _load_primary_industry_by_company(data_dir: Path) -> Dict[str, str]:
    """Load company_id -> primary_industry from company_exposures.csv."""
    path = data_dir / "company_exposures.csv"
    out = {}
    if not path.exists():
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("company_id") or "").strip()
                ind = (row.get("primary_industry") or "").strip()
                if cid and ind:
                    out[cid] = ind
    except Exception as e:
        logger.warning("Could not load company_exposures for industry_initial: %s", e)
    return out


def _tavily_results(client, query: str, max_results: int = 10, search_depth: str = "advanced") -> list:
    """Run Tavily search and return list of result objects."""
    try:
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
        )
    except Exception:
        return []
    if hasattr(response, "results"):
        return response.results or []
    if isinstance(response, dict):
        return response.get("results") or []
    return []


def _enrich_with_abstract(domain: str) -> Optional[Dict[str, str]]:
    """
    Optional: enrich with Abstract API (domain required). Returns dict with website, location, employee_range, industry, description.
    Set ABSTRACT_API_KEY in env. Free tier: 100 req/month, 1 req/sec.
    """
    api_key = os.environ.get("ABSTRACT_API_KEY")
    if not api_key or not domain or "." not in domain:
        return None
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    if not domain:
        return None
    try:
        import urllib.request
        url = f"https://companyenrichment.abstractapi.com/v2/?api_key={urllib.parse.quote(api_key)}&domain={urllib.parse.quote(domain)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.debug("Abstract API failed for %s: %s", domain, e)
        return None
    out = {}
    if data.get("company_name"):
        out["description"] = (data.get("description") or "").strip() or None
    loc_parts = [p for p in [data.get("city"), data.get("state"), data.get("country")] if p]
    if loc_parts:
        out["location"] = ", ".join(str(p) for p in loc_parts)
    if data.get("employee_range"):
        out["employee_range"] = str(data.get("employee_range", ""))
    elif data.get("employee_count"):
        out["employee_range"] = str(data.get("employee_count", ""))
    if data.get("industry"):
        out["industry"] = str(data.get("industry", "")).strip()
    if domain and not out.get("website"):
        out["website"] = f"https://{domain}" if not domain.startswith("http") else domain
    return out or None


def research_company_tavily_gemini(canonical_name: str) -> Optional[Dict[str, str]]:
    """
    Use Tavily web search (two targeted queries) + Gemini to build profile with description, leadership,
    funding, recent_news, industry, website, location, employee_range. Optionally fill website/location/
    employee_range via Abstract API if we get a domain and ABSTRACT_API_KEY is set.
    """
    tavily_key = os.environ.get("TAVILY_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")
    if not tavily_key or not google_key:
        return None
    try:
        from tavily import TavilyClient
        import google.generativeai as genai
    except ImportError as e:
        logger.warning("Tavily or Gemini not available (pip install tavily-python google-generativeai): %s", e)
        return None

    client = TavilyClient(api_key=tavily_key)
    # Query 1: general company + leadership + funding + news (advanced depth for better snippets)
    query1 = f'"{canonical_name}" company CEO leadership executives funding revenue recent news'
    results1 = _tavily_results(client, query1, max_results=10, search_depth="advanced")
    # Query 2: website, headquarters, address, employees (so we get location/website/employee_range)
    query2 = f'"{canonical_name}" official website headquarters address contact employees company size'
    results2 = _tavily_results(client, query2, max_results=8, search_depth="advanced")
    # Merge and dedupe by URL
    seen_urls = set()
    results = []
    for r in results1 + results2:
        url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            results.append(r)
    if not results:
        logger.info("Tavily returned no results for %s", canonical_name)
        return None

    chunks = []
    for i, r in enumerate(results[:14]):
        title = getattr(r, "title", None) if hasattr(r, "title") else (r.get("title") if isinstance(r, dict) else "")
        content = getattr(r, "content", None) if hasattr(r, "content") else (r.get("content") if isinstance(r, dict) else "")
        url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else "")
        if content:
            chunks.append(f"[{i+1}] Title: {title}\nURL: {url}\nContent: {content[:900]}")
    context = "\n\n".join(chunks) if chunks else ""
    if not context.strip():
        logger.info("Tavily results had no content for %s", canonical_name)
        return None

    genai.configure(api_key=google_key)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    allowed_list = ", ".join(f'"{x}"' for x in ALLOWED_INDUSTRIES)
    prompt = f"""Using the following web search results about the company "{canonical_name}", extract and summarize. Reply with valid JSON only, no markdown, with these exact keys. Use empty string "" when information is not found (do not use "Unknown" or "Empty" as values).

- "description": what the company does (one sentence)
- "leadership": key executives or who runs it (one sentence, or "" if unknown)
- "funding": funding raised, revenue, or financial scale if mentioned (one sentence, or "" if unknown)
- "recent_news": one recent headline or development if any (one sentence, or "" if none)
- "industry": MUST be exactly one of these allowed sectors, or "" if unknown: {allowed_list}. Do not use any other wording; map the company's business to the closest allowed sector.
- "location": headquarters or main office as "City, State/Region, Country" when possible (or "" if unknown)
- "website": official company website URL only if clearly stated in the results (e.g. company.com or full https URL), otherwise ""
- "employee_range": employee count or range if mentioned (e.g. "50-200", "500+", "1,000 employees", or "" if unknown)

Web search results:
{context[:14000]}

JSON:"""

    try:
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        # Remove markdown code fence if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        data = json.loads(text)
        if isinstance(data, dict):
            empty_placeholders = {"unknown", "empty", "n/a", "none", "-"}
            out = {}
            for k, v in data.items():
                s = (str(v).strip() if v else "")
                if s.lower() in empty_placeholders:
                    s = ""
                out[k] = s
            # Optional: if we have a website and Abstract API key, enrich with location/employee_range
            website = out.get("website") or ""
            domain = website.replace("https://", "").replace("http://", "").strip().split("/")[0] if website else ""
            if domain and os.environ.get("ABSTRACT_API_KEY"):
                time.sleep(1.1)  # Abstract free tier: 1 req/sec
                abstract = _enrich_with_abstract(domain)
                if abstract:
                    for key in ("location", "employee_range", "website"):
                        if abstract.get(key) and not out.get(key):
                            out[key] = abstract[key]
                    if abstract.get("industry") and not out.get("industry"):
                        raw = (abstract.get("industry") or "").strip()
                        out["industry"] = normalize_industry(raw) or ""
                        if out["industry"] and out["industry"] not in ALLOWED_INDUSTRIES:
                            out["industry"] = "Other"
                    if abstract.get("description") and not out.get("description"):
                        out["description"] = abstract["description"]
            # Force industry into allowed list only
            raw_ind = (out.get("industry") or "").strip()
            if raw_ind:
                out["industry"] = normalize_industry(raw_ind) or ""
                if out["industry"] and out["industry"] not in ALLOWED_INDUSTRIES:
                    out["industry"] = "Other"
            return out
    except Exception as e:
        logger.warning("Gemini/parse failed for %s: %s", canonical_name, e)
    return None


def research_company_gemini_only(canonical_name: str) -> Optional[Dict[str, str]]:
    """
    Use Gemini's training knowledge alone (no web search) to build a company profile.
    Works well for known private-equity-backed companies. Falls back gracefully for obscure
    holding-company names by noting the holding structure.
    Requires GOOGLE_API_KEY.
    """
    google_key = os.environ.get("GOOGLE_API_KEY")
    if not google_key:
        return None
    try:
        import google.generativeai as genai
    except ImportError:
        return None

    genai.configure(api_key=google_key)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    allowed_list = ", ".join(f'"{x}"' for x in ALLOWED_INDUSTRIES)
    prompt = f"""Using your knowledge, provide a structured profile for the company named "{canonical_name}".
This is a private company (often PE-backed). If the name looks like a holding/acquisition vehicle (e.g. "Buyer, Inc.", "Holdings, LLC", "Topco"), try to identify the underlying operating business it represents.

Reply with valid JSON only, no markdown, with these exact keys. Use empty string "" when information is not available.

- "description": what the company does (one sentence, clear business description)
- "leadership": CEO or key executive if known (one sentence, or "")
- "funding": ownership or PE sponsor if known, e.g. "Private equity-backed by Vista Equity Partners" (or "")
- "recent_news": one notable fact or recent development (or "")
- "industry": MUST be exactly one of: {allowed_list}. Map to closest match, or "".
- "location": headquarters city/state/country if known (or "")
- "website": official website URL if known (or "")
- "employee_range": approximate employee count or range if known (or "")

JSON:"""

    try:
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        data = json.loads(text)
        if isinstance(data, dict):
            empty_placeholders = {"unknown", "empty", "n/a", "none", "-", "not available", "not known"}
            out = {}
            for k, v in data.items():
                s = (str(v).strip() if v else "")
                if s.lower() in empty_placeholders:
                    s = ""
                out[k] = s
            # Validate we got something useful
            if not (out.get("description") or "").strip():
                return None
            raw_ind = (out.get("industry") or "").strip()
            if raw_ind:
                out["industry"] = normalize_industry(raw_ind) or ""
                if out["industry"] and out["industry"] not in ALLOWED_INDUSTRIES:
                    out["industry"] = "Other"
            return out
    except Exception as e:
        logger.warning("Gemini-only failed for %s: %s", canonical_name, e)
    return None


def fetch_description_llm(canonical_name: str) -> Optional[str]:
    """Optional: use OpenAI to generate a one-line company description. Requires OPENAI_API_KEY."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"One sentence: what does the company \"{canonical_name}\" do? "
                    "If it's a holding company or subsidiary, say so. Reply with that sentence only, no quotes.",
                }
            ],
            max_tokens=80,
        )
        text = (r.choices[0].message.content or "").strip()
        return text if text else None
    except Exception as e:
        logger.debug("LLM description failed for %s: %s", canonical_name, e)
        return None


def build_profiles(
    data_dir: Path,
    limit: Optional[int] = None,
    use_llm: bool = True,
    skip_existing: bool = True,
    enrich_empty_only: bool = False,
    companies_file: Optional[Path] = None,
    companies_filter: Optional[List[str]] = None,
    from_ticker: Optional[str] = None,
    top_by_exposure: Optional[int] = None,
    top_min_bdcs: int = 2,
) -> None:
    """
    Build or update company_profiles.json.
    If top_by_exposure is set (e.g. 10), load top N companies by total_exposure_millions from company_exposures.csv.
    If from_ticker is set (e.g. CION), load first N unique companies from that BDC's investment CSV; limit caps how many.
    Else if companies_file is set, read company_id + canonical_name from that CSV (only rows with company_id starting with co_).
    Otherwise use companies_index.json. limit caps how many to process.
    If companies_filter is set, only process companies whose canonical_name or company_id matches (case-insensitive).
    """
    data_dir = Path(data_dir)
    if top_by_exposure is not None:
        to_process = load_companies_from_exposures_top(
            data_dir, limit=top_by_exposure, min_num_bdcs=top_min_bdcs
        )
        logger.info("Loaded top %d companies by exposure", len(to_process))
    elif from_ticker:
        to_process = load_companies_from_ticker(data_dir, from_ticker, limit=limit)
        logger.info("Loaded %d companies (first from %s)", len(to_process), from_ticker.upper())
    elif companies_file:
        to_process = load_companies_from_csv(Path(companies_file), limit=limit)
        logger.info("Loaded %d companies from %s", len(to_process), companies_file)
    else:
        companies = load_companies_index(data_dir)
        if not companies:
            return
        to_process = [c for c in companies if c.get("company_id")]
        if limit:
            to_process = to_process[:limit]
    if companies_filter:
        want = [s.strip() for s in companies_filter if s and s.strip()]
        if want:
            filtered = []
            for c in to_process:
                cid = (c.get("company_id") or "").strip()
                canonical = (c.get("canonical_name") or "").strip()
                for w in want:
                    if w.lower() == cid.lower() or w.lower() == canonical.lower():
                        filtered.append(c)
                        break
                    if w.lower() in canonical.lower() or w.lower() in cid.lower():
                        filtered.append(c)
                        break
            to_process = filtered
            logger.info("Filtered to %d companies matching --companies", len(to_process))
    if not to_process:
        return

    # Load existing profiles from CSV first, then JSON for backward compatibility
    existing: Dict[str, Dict] = {}
    csv_path = data_dir / "company_profiles.csv"
    json_path = data_dir / "company_profiles.json"
    if skip_existing and csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cid = (row.get("company_id") or "").strip()
                    if cid:
                        existing[cid] = {k: (v or "").strip() for k, v in row.items()}
        except Exception as e:
            logger.warning("Could not load existing profiles from CSV: %s", e)
    if skip_existing and not existing and json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("profiles")
            if isinstance(raw, dict):
                existing = raw
            elif isinstance(raw, list):
                existing = {p.get("company_id"): p for p in raw if isinstance(p, dict) and p.get("company_id")}
        except Exception as e:
            logger.warning("Could not load existing profiles from JSON: %s", e)

    # Industry from resolution/filings (kept as industry_initial); we overwrite industry from allowed list only
    primary_by_cid = _load_primary_industry_by_company(data_dir)
    profiles: Dict[str, Dict[str, Any]] = dict(existing)
    tavily_set = bool(os.environ.get("TAVILY_API_KEY"))
    google_set = bool(os.environ.get("GOOGLE_API_KEY"))
    logger.info("API keys: TAVILY_API_KEY=%s, GOOGLE_API_KEY=%s", "set" if tavily_set else "not set", "set" if google_set else "not set")
    logger.info("Processing %d companies (Tavily+Gemini if keys set, else OpenAI fallback, else skeleton)", len(to_process))
    for i, ent in enumerate(to_process):
        cid = ent["company_id"]
        canonical = ent.get("canonical_name", "")
        if skip_existing and cid in profiles:
            existing_prof = profiles[cid]
            # When enriching empty only, skip only if already has LLM content (description)
            if enrich_empty_only:
                if (existing_prof.get("description") or "").strip():
                    continue
            else:
                updated_str = existing_prof.get("updated_at") or ""
                if updated_str:
                    try:
                        dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if (now - dt).days < STALE_DAYS:
                            continue
                    except Exception:
                        pass
        prof = skeleton_profile(cid, canonical)
        # Keep initially stored sector (from resolution/filings) as industry_initial
        industry_initial = primary_by_cid.get(cid) or (profiles.get(cid) or {}).get("industry_initial") or ""
        prof["industry_initial"] = (industry_initial or "").strip()
        if use_llm:
            # Prefer Tavily + Gemini for full research (description, leadership, funding, recent_news)
            research = research_company_tavily_gemini(canonical)
            if research:
                for key in ("description", "website", "location", "employee_range", "leadership", "funding", "recent_news"):
                    if key in research and research[key]:
                        prof[key] = research[key]
                # Industry: only from allowed list; LLM already constrained, normalize to be sure
                raw_ind = (research.get("industry") or "").strip() or prof["industry_initial"]
                if raw_ind:
                    prof["industry"] = normalize_industry(raw_ind) or ""
                    if prof["industry"] and prof["industry"] not in ALLOWED_INDUSTRIES:
                        prof["industry"] = "Other"
                prof["source"] = "tavily_gemini"
            else:
                # Fallback 1: Gemini-only (training knowledge, no web search)
                research = research_company_gemini_only(canonical)
                if research:
                    for key in ("description", "website", "location", "employee_range", "leadership", "funding", "recent_news"):
                        if key in research and research[key]:
                            prof[key] = research[key]
                    raw_ind = (research.get("industry") or "").strip() or prof["industry_initial"]
                    if raw_ind:
                        prof["industry"] = normalize_industry(raw_ind) or ""
                        if prof["industry"] and prof["industry"] not in ALLOWED_INDUSTRIES:
                            prof["industry"] = "Other"
                    prof["source"] = "gemini"
                else:
                    # Fallback 2: OpenAI one-line description only
                    desc = fetch_description_llm(canonical)
                    if desc:
                        prof["description"] = desc
                        prof["source"] = "openai"
                # No LLM industry: set industry from resolution/filings (normalized to allowed list)
                if prof["industry_initial"]:
                    prof["industry"] = normalize_industry(prof["industry_initial"]) or ""
                    if prof["industry"] and prof["industry"] not in ALLOWED_INDUSTRIES:
                        prof["industry"] = "Other"
            if use_llm:
                time.sleep(1)  # Rate limit between API calls (Tavily/Gemini/OpenAI)
        else:
            # Skeleton: industry from resolution only (normalized to allowed list)
            if prof["industry_initial"]:
                prof["industry"] = normalize_industry(prof["industry_initial"]) or ""
                if prof["industry"] and prof["industry"] not in ALLOWED_INDUSTRIES:
                    prof["industry"] = "Other"
        profiles[cid] = prof
        if (i + 1) % 10 == 0 or (i + 1) == len(to_process):
            logger.info("Processed %d / %d companies", i + 1, len(to_process))

    # Write CSV (primary: flat, editable, consistent with other data)
    profile_fieldnames = [
        "company_id", "canonical_name", "description", "industry", "industry_initial", "website", "location",
        "employee_range", "leadership", "funding", "recent_news", "source", "updated_at",
    ]
    csv_path = data_dir / "company_profiles.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=profile_fieldnames, quoting=csv.QUOTE_NONNUMERIC)
        writer.writeheader()
        for cid, p in profiles.items():
            row = {k: (p.get(k) or "") for k in profile_fieldnames}
            writer.writerow(row)
    logger.info("Wrote %s with %d profiles", csv_path, len(profiles))

    # Also write JSON for backward compatibility (e.g. tools that expect it)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "profiles": profiles,
    }
    json_path = data_dir / "company_profiles.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote %s with %d profiles", json_path, len(profiles))


def main():
    import argparse
    _repo_root = Path(__file__).resolve().parent.parent.parent
    _default_log_dir = str(_repo_root / "logs")
    parser = argparse.ArgumentParser(description="Build company profiles for frontend")
    parser.add_argument("--data-dir", default="frontend/public/data", help="Directory for company_profiles.json output")
    parser.add_argument("--companies-file", default=None, help="CSV with company_id, canonical_name (e.g. output/GSBD_unique_companies.csv); only rows with company_id co_* are used")
    parser.add_argument("--from-ticker", type=str, default=None, help="Use first N companies from this BDC's investment CSV (e.g. CION). Use with --limit (e.g. --limit 100).")
    parser.add_argument("--top-by-exposure", type=int, default=None, metavar="N", help="Build profiles for top N companies by total_exposure_millions (from company_exposures.csv)")
    parser.add_argument("--top-min-bdcs", type=int, default=2, metavar="K", help="With --top-by-exposure: only consider companies with at least K BDCs (default 2)")
    parser.add_argument("--limit", type=int, default=None, help="Max companies to process (with --companies-file: first N rows; with --from-ticker: first N from that BDC)")
    parser.add_argument("--companies", type=str, default=None, help="Comma-separated company names or IDs to process (e.g. 'ArborWorks LLC,CION'). Only these will be Tavily/Gemini-researched.")
    parser.add_argument("--no-llm", action="store_true", help="Do not call LLM for descriptions")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch profiles even if existing")
    parser.add_argument("--enrich-empty-only", action="store_true", help="Only call LLM for profiles that have no description yet (gradual fill)")
    parser.add_argument("--log-dir", default=_default_log_dir, help="Directory for log files (default: repo root logs/)")
    parser.add_argument("--no-log-file", action="store_true", help="Do not write logs to a file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not args.no_log_file:
        setup_logging(Path(args.log_dir))

    companies_filter = [s.strip() for s in args.companies.split(",")] if args.companies else None
    build_profiles(
        Path(args.data_dir),
        limit=args.limit,
        use_llm=not args.no_llm,
        skip_existing=not args.refresh,
        enrich_empty_only=args.enrich_empty_only,
        companies_file=Path(args.companies_file) if args.companies_file else None,
        companies_filter=companies_filter,
        from_ticker=args.from_ticker,
        top_by_exposure=args.top_by_exposure,
        top_min_bdcs=args.top_min_bdcs,
    )


if __name__ == "__main__":
    main()
