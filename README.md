# BDC Data Extractor

Automated extraction and standardization of Business Development Company (BDC) investment and financial data from SEC filings.

## 🎯 What This Does

Extracts, cleans, and standardizes investment portfolio data from BDC 10-Q/10-K filings:
- **Investment holdings** (company-level details, rates, amounts, industries)
- **Financial statements** (balance sheets, income statements, cash flows)
- **Automatic data cleaning** and standardization
- **Ready-to-use CSV files** for analysis and visualization

## 🚀 Quick Start

### Extract Latest Quarterly Data
```bash
# Extract all BDCs (latest quarter) + refresh quarterly financials & analytics
python process_all_bdcs.py --years-back 0

# Done! Clean, standardized data ready in app/public/data/
```

### Extract Historical Data
```bash
# Extract last 2 years
python process_all_bdcs.py --years-back 2

# Extract specific ticker
python process_all_bdcs.py --tickers ARCC --years-back 1
```

## 📊 Data Quality

The extractor automatically:
- ✅ Standardizes **2,319 industries** → **39 categories**
- ✅ Standardizes **3,642 investment types** → **16 types**
- ✅ Standardizes **586 reference rates** → **32 rates**
- ✅ Removes **77,000+ data errors** (dates in wrong columns, etc.)
- ✅ Normalizes company names (consistent legal suffixes)

**Result:** Production-ready, analytics-friendly data with **730,000+ quality fixes** applied automatically.

## 📁 Output Structure

```
app/public/data/
├── investments/
│   ├── ARCC.csv                    # All ARCC holdings (consolidated)
│   ├── ARCC/
│   │   ├── 2025-11-06.csv         # Quarterly snapshot
│   │   └── ...
│   └── investments_index.json      # Metadata index
├── financials/
│   ├── ARCC_financials.json        # Quarterly balance sheet + income + cash flow
│   ├── financials_index.json       # Coverage summary across tickers
│   └── ...
├── analytics/
│   ├── ARCC.json                   # Per-period type/sector/vintage breakdowns + valuation/quality
│   ├── peers.json                  # Cross-BDC comparison (latest periods)
│   └── ...
├── filings/
│   ├── ARCC.json                   # EDGAR accession/URL per 10-Q / 10-K
│   └── filings_index.json
├── credit/
│   ├── ARCC.json                   # Disclosed non-accrual % + risk rating + source snippet
│   └── credit_index.json
├── {TICKER}/profile.json           # Market profile per BDC (price, yield, market cap)
└── company_profiles.json           # Portfolio company profiles
```

## 🛠️ Features

### Automated Pipeline
- **LLM-powered extraction** with GPT-4 (SEC table parsing)
- **Automatic standardization** of all key fields
- **Error correction** (removes data in wrong columns)
- **Consolidation** (per-ticker and cross-ticker)
- **Index generation** for frontend consumption

### Data Standardization
All data is automatically cleaned during extraction:
- **Industry** → 39 standard categories (Software, Healthcare Services, etc.)
- **Investment Type** → 16 types (First Lien, Revolver, Common Equity, etc.)
- **Reference Rate** → 32 rates (SOFR, LIBOR, Euribor, etc.)
- **Spread** → Valid percentages only (dates/errors removed)
- **Company Names** → Consistent legal suffixes (Corp., LLC, Inc., LP)
- **Date/Numeric Columns** → Clean, valid values only

## Documentation

- **[docs/README.md](docs/README.md)** - Documentation map and maintenance rules
- **[docs/QUICK_START.md](docs/QUICK_START.md)** - Quick run commands
- **[docs/PIPELINE_AND_ROADMAP.md](docs/PIPELINE_AND_ROADMAP.md)** - Pipeline architecture and roadmap
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** - Active issue tracker and operational runbook

## 🔧 Setup

### Prerequisites
- Python 3.8+
- OpenAI API key (GPT-4 access)
- SEC API access (optional, for rate limits)

### Installation
```bash
# Clone repository
git clone <repo-url>
cd bdc_extractor_standalone

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your OpenAI API key
```

### Environment Variables
```bash
OPENAI_API_KEY=your_key_here           # Required
SEC_API_KEY=your_sec_key_here          # Optional (increases rate limits)
```

## 🏗️ Architecture

```
┌─────────────────┐
│  SEC Filings    │  10-Q/10-K HTML documents
└────────┬────────┘
         │
┌────────▼────────┐
│  LLM Extraction │  GPT-4 parses tables → CSV
│  + Prompts with │  (llm_table_scraper.py)
│  Standards      │
└────────┬────────┘
         │
┌────────▼────────┐
│ Post-Processing │  Automatic standardization
│  (Safety Net)   │  (post_process_extraction.py)
└────────┬────────┘
         │
┌────────▼────────┐
│ Consolidation   │  Merge per-ticker + cross-ticker
└────────┬────────┘
         │
┌────────▼────────┐
│  Frontend Data  │  Clean CSVs + JSON index
└─────────────────┘
```

## 📦 Key Components

### Core Scripts
- **`process_all_bdcs.py`** - **Main entry point** (runs everything)
- `src/extraction/financial_statements_extractor.py` - Quarterly 3-statement XBRL extraction (SEC company facts API)
- `src/extraction/sec_api_client.py` - SEC EDGAR API interface
- `src/processing/post_process_extraction.py` - Automatic data cleaning
- `src/processing/build_analytics.py` - Type/sector/vintage/valuation/quality/peers analytics JSON for the UI
- `src/processing/build_filing_links.py` - SEC submissions API → EDGAR accession/URL per filing
- `src/processing/fix_multi_block_holdings.py` - Split concatenated 10-K schedules + normalize dollar/thousand unit errors
- `src/processing/build_data_quality.py` - Reconcile each period's holdings FV against reported Investments at Fair Value
- `src/processing/extract_credit_quality.py` - Non-accrual % and risk rating from the latest 10-Q/10-K
- `src/processing/standardization_rules.py` - Standardization logic
- `src/consolidation/consolidate_investments.py` - Merge investment CSVs
- `src/consolidation/consolidate_financial_statements.py` - Per-ticker financial overviews
- `app/src/index.ts` - Plain HTML frontend + data API (Hono)

## 🎨 Frontend

The frontend is a **plain HTML/CSS/JS app** served by the small Hono server in
`app/` (no React, no build step):

```bash
cd app && npm install && npm run dev
# open http://localhost:8787
```

Views, all powered by the data above:

- **Overview** — portfolio fair value, companies, weighted yield/spread plus NAV, leverage, NII, unrealized G/L, concentration; **market strip** (price, P/NAV, dividend yield, NII coverage, non-accrual); SVG trend charts with hover tooltips (quarter, metric and portfolio fair value); peer-rank chips; data-coverage bars; source-filing links
- **Holdings** — filterable per-filing holdings table
- **Loan Types** — first lien / second lien / equity mix with marks vs cost, current period + mix over time
- **Sectors** — industry mix with peer-median tilt and marks vs cost, current period + mix over time
- **Rates & Structure** — floating vs fixed, reference-rate mix (SOFR/LIBOR/…), spread distribution, PIK share, coverage
- **Vintage & Maturity** — acquisition-year vintage, **marks (FV/cost) by acquisition year**, **quarter-by-quarter vintage mark migration heatmap**, portfolio-age buckets, per-year maturity wall with weighted coupon and floating %, unrealized gain/loss by type and top gainers/losers
- **QoQ Changes** — GitHub-style two-pane diff: pick any before/after filing, browse added/removed/modified companies like a file list, and inspect field-level changes in Unified or Split view; suspicious periods show a **data-check warning**
- **Valuation & Coverage** — P/NAV and premium/discount, dividend yield, **net income per share**, NII coverage of distributions, payout ratio, distribution rate on NAV, NAV total return (1Q/4Q), PIK share of income, market cap; coverage/NAV-return charts; quarterly quality table with **portfolio FV per quarter**; **credit quality** (non-accrual at cost/FV, weighted-average risk rating, source disclosure); market detail and source lineage
- **Financials** — balance sheet, income statement and cash flow (last 8 quarters) + trend charts; **Net Income per Share** (BDC operations per-share concept) and NII/share trends; Q2/Q3/Q4 are derived from H1/Q1, 9M/H1 and FY/9M and marked **†**; every quarter links to its SEC filing

Periods are shown as fiscal quarters with filing dates, e.g. `Q2 2026 (2026-07-29)`; financial statement columns also show the form (`10-Q` / `10-K`).

Top-level sections (header nav):

- **BDC View** — the per-BDC tabs above
- **Peer Evaluation** — pick a focus BDC and a cohort (All / Large $5B+ / Mid $1–5B / Small <$1B). **Peer positioning**: 2D or 3D-bubble scatter (bubble size = portfolio, color = a fourth metric) with **nine presets** including P/NAV vs NII coverage, P/NAV vs dividend yield and non-accrual vs P/NAV; axis pickers, median crosshairs and a closest-peers table. **Percentile profile**: SVG radar + focus-vs-median table. **Scorecard heatmap**: all cohort BDCs × 18 metrics (including P/NAV, yield, coverage, non-accrual, risk rating), green/red by cohort percentile, sortable, click a row to focus. Plus six bar leaderboards, sector tilt vs peer median, and the raw table on demand
- **Companies** — cross-BDC borrower view: which BDCs hold a company, exposure by BDC/maturity/type, profile
- **Data Quality** — holdings-vs-reported fair value reconciliation for every period: match %, mismatches, trust badge per BDC, filing links, and a per-period drill-down

Extras: CSV export of the visible tables, shareable deep links (`#/peers/ARCC/large`, `#/bdc/ARCC/valuation`, `#/bdc/ARCC/vintage/2026-07-29`, `#/bdc/ARCC/holdings/2026-07-29/sector:Healthcare`, `#/quality/ARCC`, `#/companies/co_…`), and data-freshness indicators (holdings quarter vs financials quarter, balance check).

**Data caveats:** market data comes from the per-ticker Yahoo profile (41 of 49 tickers; 35 have a usable P/NAV). It is a point-in-time snapshot with a visible as-of date, and extreme P/NAV / yield values are flagged in the UI. Credit-quality figures are parsed from the latest 10-Q/10-K text with a confidence score and the source sentence shown; 16 BDCs have no extractable disclosure and are marked accordingly. Some 10-K filings concatenate the current-year and prior-year portfolio schedules; those are detected and split automatically, and any period that still fails a fair-value/row-count sanity check is flagged in the UI. The **Data Quality** section reconciles every period against the XBRL `Investments at Fair Value` fact, shows match/mismatch status, and identifies periods that need re-extraction. OBDC's 2021–2025 10-K portfolios were re-extracted after fixing a case-sensitive "As of ..." heading comparison and excluding its Senior Loan Fund schedule; the annual periods now reconcile within 5%. Some OBDC 10-Qs still overstate fair value and remain flagged. CION, HRZN and SAR were also re-extracted: CION now reconciles at 82%, HRZN at 94%, and SAR's 2025+ periods are exact (older SAR filings remain flagged). MRCC was ported to a custom scraper and now reconciles exactly from 2024 onward (71% overall; four 2023 periods remain flagged). MFIC was ported next and now reconciles at 100% across all 16 periods. WHF now reconciles at 100% across all 17 periods. KBDC’s 2026 periods reconcile within 0.2% (2024 periods still flagged). SCM now reconciles at 100% across all 16 reported periods. SSSS, TPVG and BCRED now reconcile at 100%; EQS reconciles on current schedules but several historical periods remain flagged. PFX and RWAY now reconcile on recent periods; PSBD and RAND have current-period coverage but older filings remain flagged. GECC now reconciles exactly on 2025–2026 periods; 2022–2024 filings remain under review. See `docs/VALUATION_CREDIT.md` for formulas and extraction rules.

**Interactive drill-down:** click any bar, card or table row in Loan Types, Sectors, Rates & Structure or Vintage & Maturity to preview that slice (positions, fair value, % of portfolio, top holdings) and jump straight to Holdings filtered to it. The filter appears as a removable chip and is encoded in the URL. The QoQ company list and the Companies sidebar are sortable (Δ fair value, current fair value, positions, name, fields changed / exposure, # BDCs, name).

API endpoints used by the UI:

```
/api/tickers                          index of BDCs + periods
/api/tickers/:ticker/periods/:period  holdings rows for one filing
/api/tickers/:ticker/analytics        per-period breakdowns + quality/freshness meta
/api/tickers/:ticker/financials       quarterly 3-statement data
/api/tickers/:ticker/filings          EDGAR links per filing period
/api/tickers/:ticker/credit           disclosed non-accrual / risk rating
/api/peers                            cross-BDC comparison + medians
/api/profiles                         compact BDC name/price summaries
/api/companies                        cross-BDC company exposure index
/api/companies/:id                    company detail + profile + BDC exposures
/api/quality                          reconciliation index + trust badges
/api/tickers/:ticker/quality          per-period holdings-vs-reported reconciliation
```

## 📈 Supported BDCs

Currently configured for **49 BDCs** (see `app/public/data/investments_index.json`), including:
- ARCC (Ares Capital)
- MAIN (Main Street Capital)
- BBDC (Barings BDC)
- BCSF (Bain Capital)
- BXSL (Blackstone Secured)
- CCAP (Crescent Capital)
- CGBD (Carlyle)
- CION (CION)
- GSBD (Goldman Sachs)
- MRCC (Monroe Capital)
- …and 39 more (BXSL, HTGC, OBDC, TSLX, PSEC, FSK, CSWC, etc.)

Easy to add more in `process_all_bdcs.py`.

## 🔄 Workflow for New Quarterly Data

### Local Extraction
```bash
# Extract latest quarter
python process_all_bdcs.py --years-back 0

# Extract specific tickers
python process_all_bdcs.py --tickers ARCC MAIN --years-back 0
```

### GitHub Actions (Automated)
The repository includes a GitHub Actions workflow that can:
- Run manually via workflow_dispatch
- Run automatically monthly (15th of each month)
- Extract latest or historical data
- Auto-commit results

**To use:**
1. Add secrets to your GitHub repo:
   - `OPENAI_API_KEY` (required for GPT-4)
   - `SEC_API_KEY` (optional, increases rate limits)
2. Go to Actions tab → "Extract BDC Data" → Run workflow
3. Choose years back (0 = latest, 1 = past year, etc.)
4. Optionally specify tickers

**Monthly auto-run:** Workflow runs on the 15th of each month to catch new quarterly filings.

**Pushes from Actions (`403` / “Permission denied … github-actions[bot]”):** Workflows use `permissions: contents: write` and `actions/checkout@v6` / `actions/setup-python@v6` (Node 24 runtime). You still need **Settings → Actions → General → Workflow permissions → Read and write** for this repository (orgs may default to read-only). If `master` is protected, allow **GitHub Actions** or `github-actions[bot]` to push, or add a repository secret **`REPO_PUSH_TOKEN`**: a personal access token or fine-grained token with **Contents: Read and write** on this repo (workflows pass `secrets.REPO_PUSH_TOKEN || github.token` to checkout).

## 🧪 Testing

```bash
# Dry run (no files written)
python post_process_extraction.py --file output/test.csv

# Test single ticker
python process_all_bdcs.py --tickers ARCC --years-back 0
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `python process_all_bdcs.py --tickers ARCC --years-back 0`
5. Submit a pull request

## 📝 License

[Your License Here]

## 🐛 Troubleshooting

### Common Issues

**"Invalid API key"**
- Check `.env` file has correct `OPENAI_API_KEY`

**"Rate limit exceeded"**
- Add `SEC_API_KEY` to `.env` for higher limits
- Or add delays between requests

**"No data extracted"**
- Check ticker symbol is correct
- Verify filing exists for the period
- Check SEC EDGAR availability

**GitHub Actions push fails with 403 after commit**
- See **Pushes from Actions** under [GitHub Actions (Automated)](#github-actions-automated) above (workflow permissions, branch protection, or `REPO_PUSH_TOKEN`).

### Getting Help

See [docs/OPERATIONS.md](docs/OPERATIONS.md) and [docs/PIPELINE_AND_ROADMAP.md](docs/PIPELINE_AND_ROADMAP.md) for troubleshooting and workflow guidance.

## Public deployment & filing monitoring

The app is a Node/Hono server over ~1.2 GB of static data, so it needs a
container/VM (or object storage) rather than Cloudflare Workers' small asset
limit.

### Quick public URL

```bash
# terminal 1
cd app && PORT=8787 node src/index.ts

# terminal 2
cloudflared tunnel --url http://localhost:8787 --protocol http2
```

Use `--protocol http2` on networks that block outbound QUIC. The generated
`https://*.trycloudflare.com` URL stays live only while both processes run.

### Durable container deploy

```bash
docker build -t bdc-app .
docker run -p 8787:8787 bdc-app
```

`render.yaml` is included for a Render Docker service. Set the health check to
`/api/freshness`.

### New filing checks

```bash
# report new 10-Q / 10-K filings
python scripts/check_new_filings.py

# re-extract affected tickers and rebuild all derived data
python scripts/check_new_filings.py --run
```

The first run records the current latest accession per ticker; later runs
report only newly seen filings. State is stored in
`output/filing_monitor_state.json`. Example daily cron entry:

```cron
0 6 * * * cd /path/to/bdc_extractor_standalone && \
  python3 scripts/check_new_filings.py --run >> output/filing_monitor.log 2>&1
```

`GET /api/freshness` returns the latest filing date, quality generation time,
ticker count and match summary.

## 🎯 Roadmap

- [ ] Add more BDCs
- [ ] Historical data backfill automation
- [ ] Real-time monitoring for new filings
- [ ] API endpoint for data access
- [ ] Interactive data quality dashboard

---

**Last Updated:** February 2026  
**Version:** 2.0 (Full Standardization)
