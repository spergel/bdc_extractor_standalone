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
import time
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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


def skeleton_profile(company_id: str, canonical_name: str) -> Dict[str, Any]:
    return {
        "company_id": company_id,
        "canonical_name": canonical_name,
        "description": "",
        "industry": "",
        "website": "",
        "location": "",
        "employee_range": "",
        "leadership": "",
        "funding": "",
        "recent_news": "",
        "source": "skeleton",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


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
        logger.debug("Tavily or Gemini not available: %s", e)
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
        return None

    genai.configure(api_key=google_key)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    prompt = f"""Using the following web search results about the company "{canonical_name}", extract and summarize. Reply with valid JSON only, no markdown, with these exact keys. Use empty string "" when information is not found (do not use "Unknown" or "Empty" as values).

- "description": what the company does (one sentence)
- "leadership": key executives or who runs it (one sentence, or "" if unknown)
- "funding": funding raised, revenue, or financial scale if mentioned (one sentence, or "" if unknown)
- "recent_news": one recent headline or development if any (one sentence, or "" if none)
- "industry": sector/industry if clear (one phrase, or "" if unknown)
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
                    for key in ("location", "employee_range", "industry", "website"):
                        if abstract.get(key) and not out.get(key):
                            out[key] = abstract[key]
                    if abstract.get("description") and not out.get("description"):
                        out["description"] = abstract["description"]
            return out
    except Exception as e:
        logger.debug("Gemini parse failed for %s: %s", canonical_name, e)
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
    companies_file: Optional[Path] = None,
) -> None:
    """
    Build or update company_profiles.json.
    If companies_file is set, read company_id + canonical_name from that CSV (only rows with company_id starting with co_).
    Otherwise use companies_index.json. limit caps how many to process.
    """
    data_dir = Path(data_dir)
    if companies_file:
        to_process = load_companies_from_csv(Path(companies_file), limit=limit)
        logger.info("Loaded %d companies from %s", len(to_process), companies_file)
    else:
        companies = load_companies_index(data_dir)
        if not companies:
            return
        to_process = [c for c in companies if c.get("company_id")]
        if limit:
            to_process = to_process[:limit]
    if not to_process:
        return

    existing_path = data_dir / "company_profiles.json"
    existing: Dict[str, Dict] = {}
    if skip_existing and existing_path.exists():
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("profiles")
            if isinstance(raw, dict):
                existing = raw
            elif isinstance(raw, list):
                existing = {p.get("company_id"): p for p in raw if isinstance(p, dict) and p.get("company_id")}
        except Exception as e:
            logger.warning("Could not load existing profiles: %s", e)

    profiles: Dict[str, Dict[str, Any]] = dict(existing)
    logger.info("Processing %d companies (Tavily+Gemini if keys set, else OpenAI fallback, else skeleton)", len(to_process))
    for i, ent in enumerate(to_process):
        cid = ent["company_id"]
        canonical = ent.get("canonical_name", "")
        if skip_existing and cid in profiles:
            updated_str = profiles[cid].get("updated_at") or ""
            if updated_str:
                try:
                    dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if (now - dt).days < STALE_DAYS:
                        continue
                except Exception:
                    pass
        prof = skeleton_profile(cid, canonical)
        if use_llm:
            # Prefer Tavily + Gemini for full research (description, leadership, funding, recent_news)
            research = research_company_tavily_gemini(canonical)
            if research:
                for key in ("description", "industry", "website", "location", "employee_range", "leadership", "funding", "recent_news"):
                    if key in research and research[key]:
                        prof[key] = research[key]
                prof["source"] = "tavily_gemini"
            else:
                # Fallback: OpenAI one-line description only
                desc = fetch_description_llm(canonical)
                if desc:
                    prof["description"] = desc
                    prof["source"] = "openai"
            if use_llm:
                time.sleep(1)  # Rate limit between API calls (Tavily/Gemini/OpenAI)
        profiles[cid] = prof
        if (i + 1) % 10 == 0 or (i + 1) == len(to_process):
            logger.info("Processed %d / %d companies", i + 1, len(to_process))

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "profiles": profiles,
    }
    out_path = data_dir / "company_profiles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    logger.info("Wrote %s with %d profiles", out_path, len(profiles))


def main():
    import argparse
    _repo_root = Path(__file__).resolve().parent.parent.parent
    _default_log_dir = str(_repo_root / "logs")
    parser = argparse.ArgumentParser(description="Build company profiles for frontend")
    parser.add_argument("--data-dir", default="frontend/public/data", help="Directory for company_profiles.json output")
    parser.add_argument("--companies-file", default=None, help="CSV with company_id, canonical_name (e.g. output/GSBD_unique_companies.csv); only rows with company_id co_* are used")
    parser.add_argument("--limit", type=int, default=None, help="Max companies to process (with --companies-file: first N rows)")
    parser.add_argument("--no-llm", action="store_true", help="Do not call LLM for descriptions")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch profiles even if existing")
    parser.add_argument("--log-dir", default=_default_log_dir, help="Directory for log files (default: repo root logs/)")
    parser.add_argument("--no-log-file", action="store_true", help="Do not write logs to a file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not args.no_log_file:
        setup_logging(Path(args.log_dir))

    build_profiles(
        Path(args.data_dir),
        limit=args.limit,
        use_llm=not args.no_llm,
        skip_existing=not args.refresh,
        companies_file=Path(args.companies_file) if args.companies_file else None,
    )


if __name__ == "__main__":
    main()
