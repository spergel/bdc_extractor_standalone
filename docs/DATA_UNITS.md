# Data units – single source of truth

All currency and numeric types are standardized so numbers match across BDC view, Companies view, Sectors, Analytics, and tables.

## Currency

### THOUSANDS (holdings / analytics)

- **Source:** Investment CSVs (`investments/*.csv`, consolidated), in-memory `Holding` (`fair_value_thousands`, `principal_amount_thousands`, `cost_thousands`, etc.).
- **Meaning:** Value in thousands of dollars. `8750` = $8,750,000 = $8.75 M.
- **Frontend:** Use **`formatThousandsAsCurrency(value)`** from `frontend/src/utils/formatCurrency.ts`.
- **Used in:** Holdings table (principal, cost, fair value), Analytics (total FV, avg position, breakdowns), Charts, Diff viewer, Drill-down, BDC sector view.

**Backend requirement:** Extraction and consolidation must output `fair_value`, `principal_amount`, `cost`, `amortized_cost`, `commitment_limit`, `undrawn_commitment` in **thousands**. (LLM scraper and XBRL extractor should scale to thousands before or when writing CSV.)

### MILLIONS (exposures / company detail / sectors)

- **Source:** `company_exposures.csv` (`total_exposure_millions`), `company_detail.json` (`by_bdc`, `by_maturity`, `by_investment_type`), portfolio/industry summaries.
- **Meaning:** Value in millions of dollars. `608` = $608 M, `1.2` = $1.2 B when shown as billions.
- **Frontend:** Use **`formatMillionsAsCurrency(value)`** from `frontend/src/utils/formatCurrency.ts`.
- **Used in:** Company page (total exposure, lender table, maturity/type breakdowns), Companies sidebar, Sectors sidebar, Sector page.

## Rules

1. **Never** format currency with ad-hoc `toFixed` / `toLocaleString` + "M" or "B". Always use `formatThousandsAsCurrency` or `formatMillionsAsCurrency`.
2. **Never** mix units: if a value is in thousands, don’t pass it to `formatMillionsAsCurrency` (you’d show 1000× too small).
3. **New views:** If you add a new component that shows dollar amounts, check whether the data is in thousands (holdings/analytics) or millions (exposures/detail) and use the matching formatter.
4. **CSV producers:** Keep investment CSVs in thousands so the frontend (adapter + formatters) stays correct everywhere.

## Quick reference

| View / data              | Unit     | Formatter                     |
|--------------------------|----------|-------------------------------|
| Holdings table           | Thousands| `formatThousandsAsCurrency`  |
| Analytics (total FV, etc.) | Thousands | `formatThousandsAsCurrency` |
| Charts, Diff, Drill-down | Thousands| `formatThousandsAsCurrency`  |
| Companies sidebar/list   | Millions | `formatMillionsAsCurrency`   |
| Company page (exposure)  | Millions | `formatMillionsAsCurrency`   |
| Sectors sidebar/page     | Millions | `formatMillionsAsCurrency`   |

See also `frontend/src/utils/formatCurrency.ts` (table in file comment).
