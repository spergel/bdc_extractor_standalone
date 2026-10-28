# Valuation, distribution quality, vintage marks & credit quality

This document covers the valuation/quality additions, the SEC filing-link
builder, and the credit-quality extractor.

## Data sources

| Data | Source | Output |
|---|---|---|
| Share price, market cap, dividend rate/yield, P/NAV vs Yahoo book, beta, 52-week range | Per-ticker Yahoo profile (`app/public/data/{TICKER}/profile.json`) | `analytics/{TICKER}.json → valuation`, `analytics/peers.json` |
| NAV/share, NII, DPS, PIK income, total investment income, equity | SEC company facts (`financials/{TICKER}_financials.json`) | `analytics/{TICKER}.json → quality_periods` |
| Vintage / marks | Consolidated holdings | `analytics/{TICKER}.json → periods[].vintage_years`, `vintage_history` |
| Filing links | SEC submissions API (`data.sec.gov/submissions/CIK##########.json`) | `filings/{TICKER}.json` |
| Non-accrual %, risk rating | Latest 10-Q/10-K primary document (text) | `credit/{TICKER}.json` |

Market profiles are a point-in-time snapshot (visible `price_asof`). 41 of 49
tickers have a profile; 35 produce a usable P/NAV. Missing profiles leave the
fields null rather than guessing.

## Formulas

Flow items are annualized with each XBRL fact's `duration_days`:
`annualized = value × 365 / duration_days` (quarterly ≈ 91 days, YTD values
are handled correctly).

- **P/NAV** = share price / latest reported NAV per share. Premium/discount =
  `P/NAV − 1`. Yahoo's `priceToBook` is shown alongside as a cross-check.
- **Dividend yield** = profile dividend rate / price (falls back to the Yahoo
  `dividendYield` field).
- **NII per share (quarterly)** = annualized NII / shares / 4, where
  `shares = equity / NAV per share` (falls back to profile shares outstanding).
- **NII coverage** = annualized NII per share / annualized DPS. Values below
  1.0× mean the distribution is not covered by net investment income.
- **Payout ratio** = DPS / NII per share = `1 / coverage`.
- **Distribution rate** = annualized DPS / NAV per share.
- **PIK share of income** = annualized PIK interest / annualized total
  investment income.
- **NAV total return (quarter)** = `(NAV_t − NAV_{t−1} + DPS_t) / NAV_{t−1}`.
  Computed only for gaps of 70–110 days; 4-quarter return compounds the last
  four quarterly returns.
- **Vintage mark** = `(fair value − amortized cost) / amortized cost` for all
  positions acquired in that calendar year.
- **Vintage mark migration** = the same mark per acquisition year, repeated for
  the last 8 filings.

## Credit-quality extraction

`src/processing/extract_credit_quality.py` downloads the latest 10-Q/10-K and
parses:

- non-accrual % of total investments **at amortized cost** and **at fair value**
- non-accrual issuer/investment count when stated
- weighted-average **risk rating / grade** (1–5) when disclosed

Rules and safeguards:

- Percentages must be within ~240 characters of the phrase "non-accrual" and
  tied to "cost", "cost basis", or "fair value". The closest candidate wins.
- Label-first table cells (`at amortized cost (5) 3.6%`) and table rows
  (`Non-accrual 428,716 2.8% 126,408 0.8%`) are supported.
- Yields and exclusion phrases (`other than non-accrual`, `excluding income
  from non-accruals`) and count-only table rows are rejected.
- A cost percentage below the fair-value percentage is dropped as a likely
  misparse.
- Each result stores a `confidence` (`high` / `medium` / `low` / `none`), the
  source sentence (`snippets`), the filing link, and notes.

Risk-rating scales are **not standardized across BDCs**. The UI labels the
field "1 = lowest risk, 5 = highest"; always check the source snippet. 16 of 49
BDCs currently have no extractable non-accrual disclosure and are shown as
"— / not disclosed".

## Holdings data quality: concatenated 10-K schedules

Some 10-K filings present the current-year and prior-year schedules of
investments back to back. When a scraper flattens both, a single period ends up
with roughly twice the rows and twice the fair value. ARCC Q4 2025 was the
clearest example: $56.0B extracted vs ~$29.4B reported on the balance sheet.

`src/processing/fix_multi_block_holdings.py`:

1. Detects a restart in the company-name sequence (a second schedule begins).
2. Requires both blocks to be substantial, with overlapping company sets.
3. Compares each candidate block with the reported **Investments at Fair
   Value** (unit-normalized) or the neighbouring quarters.
4. Keeps the block that matches and rewrites both `{TICKER}.csv` and the
   per-period CSV; iterates until stable (fixing one period can reveal the true
   level for its neighbours).

It is intentionally conservative: periods whose total is not ~1.7–2.3× the
target, or where no block matches within 10%, are left alone.

`build_analytics.py` then adds `quality_flags` to each period when:

- fair value is more than **1.35×** the trailing 4-filing median;
- fair value is less than **0.70×** the trailing median (units/gap);
- row count falls more than **35%** versus the prior filing.

The UI shows these as a “Data check” warning on Overview, Holdings and QoQ,
and QoQ additionally warns when the net change exceeds 35% of the opening
portfolio. This does not replace source verification, but it prevents an
extraction problem from looking like a real portfolio move.

## Holdings reconciliation & trust badges

`src/processing/build_data_quality.py` compares each period's holdings total
(`fair_value`, stored in thousands) against the XBRL
`Investments at Fair Value` fact for the same quarter end. Reported values may
be in dollars, thousands or millions, so a per-ticker scale is inferred before
comparison.

- **match** — within 5% of reported
- **near** — within 10%
- **mismatch** — more than 10% off (candidate for re-extraction)
- **trust** — good (80%+ match and no mismatches), warn (50%+ match), bad, or
  unknown when no reported value exists

Outputs `quality/{TICKER}.json` and `quality/quality_index.json`; the
**Data Quality** section shows the scorecard, trust badges, filing links and a
per-period drill-down. ARCC and BXSL currently reconcile at 100%; OBDC's
2022–2024 10-Ks and a few small-BDC periods remain flagged for re-extraction.

## Unit normalization

`fix_multi_block_holdings.py` also handles files extracted in dollars instead of
thousands: if the median position value in a filing is above $1B
(`fair_value > 1,000,000` in thousands), monetary columns are divided by 1000.
This repairs early PFLT/PNNT/FSK files while leaving normal filings untouched.
It only scales **down**; a file that is 1000x too small is treated as a partial
extraction and left flagged rather than inflated.

## Financial statement normalization

The extractor now converts flow facts to discrete quarters at extraction time:

- Q2 = H1 − Q1
- Q3 = 9M − H1
- Q4 = FY − 9M

Explicit quarter-length facts always win; derived values are tagged with
`derived: "H1-Q1"`, `"9M-H1"` or `"FY-9M"` and marked **†** in the UI.
Balance-sheet facts are never modified. The same duration-aware annualization
is used for valuation and coverage metrics in `build_analytics.py`.

**Net Income per Share** is sourced from
`InvestmentCompanyInvestmentIncomeLossFromOperationsPerShare` (the BDC
"net increase in net assets from operations per share" concept), with
`EarningsPerShareBasic` as a fallback and `Net Income (Loss) / shares` as a
final fallback. This fills the three BDCs (PNNT, PFLT, CION) that do not tag
`EarningsPerShareBasic` at all. Per-share facts above $50 are rejected as
split/unit artifacts.

## Refresh commands

```bash
# Split concatenated current/prior-year 10-K schedules (idempotent)
python src/processing/fix_multi_block_holdings.py

# Filing links (cached under output/filings; use --refresh to bypass cache)
python src/processing/build_filing_links.py

# Credit-quality extraction (cached under output/credit/docs)
python src/processing/extract_credit_quality.py

# Rebuild analytics/peers (reads filings + credit if present)
python src/processing/build_analytics.py

# Reconcile holdings against reported Investments at Fair Value
python src/processing/build_data_quality.py
```

`process_all_bdcs.py` runs the block fixer after consolidation, then
financials → filings → credit → analytics → quality. Use `--skip-block-fix`,
`--skip-filings`, `--skip-credit`, `--skip-analytics`, or `--skip-quality` to
skip a step.

## Known limitations

- Market data coverage depends on the profile files; 8 tickers have no profile
  and several lack a reported NAV, leaving P/NAV null.
- Prices are not split/dividend-adjusted by this pipeline; the profile's own
  as-of date is displayed. Extreme P/NAV (<0.35×) or yield (>20%) values raise
  a UI flag for review.
- Credit-quality parsing is text-based and best-effort. Some BDCs disclose only
  per-investment footnote markers (no aggregate percentage); those remain null.
- Quarter labels use calendar quarter-ends derived from the period end/filing
  date.
