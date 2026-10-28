# Data units – single source of truth

All currency and numeric types are standardized so numbers match across Holdings, Analytics, Financials, and Peers views.

## Currency

### THOUSANDS (holdings / analytics)

- **Source:** Investment CSVs (`investments/*.csv`, consolidated).
- **Meaning:** Value in thousands of dollars. `8750` = $8,750,000 = $8.75 M.
- **Frontend:** Use **`fmtK(value)`** in `app/src/index.ts`.
- **Used in:** Holdings table (principal, cost, fair value), Overview cards, Type/Sector/Vintage breakdowns, Peers table (fair value).

**Backend requirement:** Extraction and consolidation must output `fair_value`, `principal_amount`, `cost`, `amortized_cost`, `commitment_limit`, `undrawn_commitment` in **thousands**.

### RAW DOLLARS (financial statements)

- **Source:** `financials/{TICKER}_financials.json` (SEC company facts API values are actual dollars).
- **Meaning:** `30596000000` = $30,596,000,000 = $30.6 B.
- **Frontend:** Use **`fmtMoney(value)`** in `app/src/index.ts`.
- **Used in:** Financials tab (balance sheet / income / cash flow tables), Overview financial snapshot.

### MILLIONS (exposures / company detail / sectors)

- **Source:** `company_exposures.csv` (`total_exposure_millions`), `company_detail.json`.
- **Meaning:** Value in millions of dollars. `608` = $608 M.
- **Used in:** company exposure artifacts (not the current app UI).

## Rules

1. **Never** format currency with ad-hoc `toFixed` / `toLocaleString` + "M" or "B". Use `fmtK` (thousands), `fmtMoney` (raw dollars), or an explicit millions formatter.
2. **Never** mix units: holdings/analytics are thousands; financials JSON is raw dollars.
3. **CSV producers:** Keep investment CSVs in thousands.

## Quick reference

| View / data              | Unit      | Formatter (app/src/index.ts) |
|--------------------------|-----------|------------------------------|
| Holdings table           | Thousands | `fmtK`                       |
| Analytics (total FV, etc.) | Thousands | `fmtK`                     |
| Type/Sector/Vintage bars | Thousands | `fmtK`                       |
| Peers fair value         | Thousands | `fmtK`                       |
| Financial statements     | Raw $     | `fmtMoney`                   |
| Per-share values         | $ / share | `'$' + num(v).toFixed(2)`    |
