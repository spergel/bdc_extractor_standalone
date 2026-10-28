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

## Refresh commands

```bash
# Filing links (cached under output/filings; use --refresh to bypass cache)
python src/processing/build_filing_links.py

# Credit-quality extraction (cached under output/credit/docs)
python src/processing/extract_credit_quality.py

# Rebuild analytics/peers (reads filings + credit if present)
python src/processing/build_analytics.py
```

`process_all_bdcs.py` runs these automatically after financials; use
`--skip-filings`, `--skip-credit`, or `--skip-analytics` to skip a step.

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
