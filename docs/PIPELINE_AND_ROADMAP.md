# Data Pipeline and Roadmap

How we get BDC holdings, portfolio company profiles, and keep things updated going forward. Designed for a **slow, review-friendly** pace so you can inspect data at each stage.

---

## Repo cleanup (done)

- **Removed:** All `*.BEFORE.csv` backup files from `frontend/public/data/investments/*/` (no longer needed after company_id rollout).
- **Docs cleanup:** `docs/README.md` now defines active vs reference docs; operations tracking is consolidated in `docs/OPERATIONS.md`.
- **`.gitignore`:** Added `nul` to avoid accidentally committing Windows nul device.
- **Company name cleaning:** Trailing rate/date suffixes (e.g. `L+775 1.50% LIBOR Floor 4/7/2022`) and instrument suffixes are stripped so variants resolve to one company (see `src/processing/standardization_rules.py`).

---

## Current state

- **Holdings (investments):** 30+ BDCs extracted and available. Per-ticker, per-period CSVs under `frontend/public/data/investments/{TICKER}/`.
- **Two extraction paths:**
  - **XBRL path** (`xbrl_investment_extractor.py`): Used for tickers with XBRL-tagged financials (ARCC, GBDC, MAIN, BBDC, BXSL, etc.). Parses `_htm.xml` XBRL instance documents. **HTML enrichment** fills missing `industry` and `maturity_date` from the HTML filing table (87–97% fill rate for industry; 63–79% for maturity).
  - **HTML/LLM path** (`llm_table_scraper.py` / `html_soi_parser.py`): Used for tickers without useful XBRL tagging. Parses the rendered HTML table directly — no LLM needed for most BDCs.
- **Company resolution:** ~8,000+ canonical portfolio companies; `company_id` on every holding row; `companies_index.json` and `company_exposures.csv` up to date.
- **Portfolio company profiles:** `company_profiles.json` has profiles for a subset of companies. We **merge** into this file; we never delete it.
- **BDC fund profiles:** Optional per-ticker `frontend/public/data/{TICKER}/profile.json` (if we add/use them).
- **Pipeline script:** `process_all_bdcs.py` can run extraction → post_process → consolidate → company resolution (and optionally profile build). Profile build is usually run separately with `--companies-file` and `--limit` so we can control volume and review.

---

## Phase 1: Slowly add remaining BDCs and portfolio company profiles

Goal: add the rest of the BDCs from `process_all_bdcs.py`’s list and build portfolio company profiles in batches you can review.

### 1.1 Adding more BDCs (holdings data)

- **One (or a few) BDCs at a time** so you can check extraction quality:
  ```bash
  python process_all_bdcs.py --tickers TICKER1 TICKER2 --years-back 1 --skip-financials --skip-profiles --force
  ```
- After each run: **review** `output/{TICKER}_investments_*.csv` and the consolidated files under `frontend/public/data/investments/{TICKER}/`. If something looks wrong, fix extraction or run with different options before moving on.
- Then run **post_process** (if not already done in the same script), **consolidate**, and **company resolution** so new tickers get `company_id` and appear in `company_exposures` and the index.
- **Order:** You can pick BDCs by priority (e.g. by AUM or coverage gap). No need to do all at once.

### 1.2 Portfolio company profiles (the “cos they hold”)

- Profiles live in **one file**: `frontend/public/data/company_profiles.json`. We **merge**; we never replace the whole file.
- **Per-BDC batch** (recommended for “slow and look at data”):
  1. Generate the company list for that BDC:
     ```bash
     python src/company_resolution/list_unique_companies.py --ticker TICKER
     ```
  2. Build profiles in small batches so you can review:
     ```bash
     python src/company_resolution/build_profiles.py --companies-file output/TICKER_unique_companies.csv --limit 50 --refresh
     ```
  3. Check `company_profiles.json` (and the Companies/Sectors views in the app). If quality is good, run again with a higher `--limit` or no `--limit` for that BDC.
- **Rate limits:** Tavily/Gemini/Abstract have limits; the script already has small delays. For hundreds of companies per BDC, running with `--limit` repeatedly is safer than one huge run.
- **Re-running:** You can re-run the same `--companies-file` with `--refresh` and `--limit` to backfill more; existing profiles for other companies are unchanged.

### 1.3 BDC “fund” profiles (optional)

- If you want a short description/summary **per BDC fund** (not per portfolio company), that’s separate from `company_profiles.json`. Options:
  - Reuse the same Tavily+Gemini flow for the BDC name (e.g. “Goldman Sachs BDC”) and write to something like `frontend/public/data/{TICKER}/profile.json`, or
  - Keep BDC metadata in a single `bdc_profiles.json` keyed by ticker.
- This can be done later; it’s independent of portfolio company profiles.

---

## Phase 2: Repo cleanup

Before locking in “going forward” automation, it helps to tidy the repo so the pipeline is obvious and maintainable.

- **Docs:** Keep pipeline and roadmap in one place (this file). Keep live issue tracking in `docs/OPERATIONS.md` and avoid one-off status files.
- **Scripts:** 
  - Single entry point for “full run for N BDCs”: `process_all_bdcs.py` (already there).
  - Clear split: extraction/consolidation vs. company resolution vs. profile building, so you can run resolution after name-rule changes and profiles in batches.
- **Data layout:** Keep `output/` for scraper output and intermediate CSVs; `frontend/public/data/` for what the app serves. Avoid duplicating the same data in multiple shapes.
- **Env and secrets:** `.env` for API keys; keep `.env` out of git. Document in README or QUICK_START which keys are needed for extraction vs. profiles.
- **Cleanup list (example):**
  - [ ] Consolidate or archive ad-hoc markdown at repo root into `docs/`.
  - [ ] README: “How to add a new BDC” and “How to refresh company profiles.”
  - [ ] Optional: small script that runs “list_unique + build_profiles” for one ticker with a default `--limit` so you don’t have to remember the two commands.

---

## Phase 3: Ongoing quarterly data (going forward)

Once you’re happy with the current BDC set and profile depth, you can add a lightweight “quarterly update” flow.

### 3.1 What “new quarterly data” means

- New **10-Q** (and eventually **10-K**) filings for BDCs you already support.
- Goals: (1) New period in `investments_index.json` and new `{TICKER}/{period}.csv`. (2) New rows get `company_id` from resolution. (3) Optionally refresh or add portfolio company profiles for new names.

### 3.2 Suggested quarterly flow

1. **Extract** new filings only (e.g. last 1 quarter or “since last run”):
   ```bash
   python process_all_bdcs.py --tickers ARCC BBDC ... --years-back 0 --skip-financials --skip-profiles
   ```
   (If “years-back 0” isn’t supported, use `--years-back 1` and rely on “skip if output exists” or a `--since` flag if you add one.)

2. **Post-process** and **consolidate** so new CSVs land in `frontend/public/data/investments/` and the index is updated.

3. **Company resolution** so new holdings get `company_id` and `company_exposures` / `companies_index` stay current:
   ```bash
   python src/company_resolution/resolve_companies.py
   ```

4. **Profiles (optional):** Either:
   - Run build_profiles with a **companies file that lists only companies not yet in `company_profiles.json`**, or
   - Run a small batch (e.g. `--limit 20`) from a “new companies this quarter” list, or
   - Skip and only backfill profiles when you add a new BDC or do a dedicated “profile catch-up” run.

### 3.3 Making it repeatable

- **Cron / scheduled task:** Run the above steps monthly or after quarter-end (when 10-Qs are filed). Keep the same commands; no need to change BDC list if you’re only updating existing tickers.
- **“Since last run”:** For true quarterly-only runs, you could add a `--since YYYY-MM-DD` to the scraper and only fetch filings after that date. That’s a small enhancement to the extraction script.
- **Index regeneration:** Consolidation already rewrites `investments_index.json`; resolution already rewrites `companies_index.json` and `company_exposures.csv`. So “quarterly run” = same pipeline, new data.

### 3.4 What we don’t automate (by design)

- **Which BDCs to support:** You add new BDCs manually (Phase 1) and then include them in the quarterly run.
- **Profile depth:** You choose when to run profile build and with which `--limit` or which company list, so you can keep reviewing data.

---

## Summary

| Phase | Focus | Pace |
|-------|--------|-----|
| **1** | Add remaining BDCs (holdings) + portfolio company profiles in batches | Slow: one or a few BDCs at a time; profiles with `--limit`; review after each batch |
| **2** | Repo cleanup: docs, scripts, data layout, env | One-off; then small tweaks as needed |
| **3** | Quarterly (or monthly) refresh: new filings → consolidate → resolution; optional profile top-up | Repeatable; same pipeline, no need to delete or replace `company_profiles.json` |

Company resolution has been re-run with the improved name cleaning (instrument suffixes and parentheticals stripped), so duplicate “companies” like “Zeus Fire & Security - Delayed Draw” vs “Zeus Fire & Security - Revolver” now share one `company_id`. You can keep adding BDCs and profiles on top of this.
