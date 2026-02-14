# Missing BDC Overview Metrics

## Currently Missing from XBRL Extraction

These metrics are **not available in standard XBRL tags** and need alternative extraction methods:

### 1. **Originations** (New Investments During Quarter)
- **What**: Dollar amount of new investments made during the quarter
- **Where to find**: 
  - MD&A section: "During the quarter, we originated $X million..."
  - Statement of Changes in Net Assets / Cash Flow Statement
  - Portfolio Activity tables
- **Extraction method needed**: HTML table parsing or LLM-based text extraction

### 2. **Repayments** (Exits/Paydowns During Quarter)
- **What**: Dollar amount of investments repaid/exited during the quarter
- **Where to find**:
  - MD&A section: "We received $X million in repayments..."
  - Statement of Changes in Net Assets
  - Portfolio Activity tables
- **Extraction method needed**: HTML table parsing or LLM-based text extraction

### 3. **Non-Accruals %**
- **What**: Percentage of portfolio (by cost or FV) that is non-accrual
- **Where to find**:
  - Investment schedule footnotes: "Non-accrual investments totaled $X..."
  - MD&A portfolio quality section
  - Sometimes in investment schedule headers
- **Extraction method needed**: 
  - Parse investment schedule table notes/footnotes
  - Look for "non-accrual" or "non-income producing" tags in holdings
  - Text extraction from MD&A

### 4. **Quarterly Realized/Unrealized Gains** (Partially Working)
- **Status**: XBRL extracts YTD values, but quarterly breakout is inconsistent
- **Where to find**: Statement of Operations (3 months vs 9 months columns)
- **Extraction method needed**: Parse financial statement tables to extract 3-month column

## Potential Solutions

### Option 1: Enhanced HTML Table Parsing
- Parse Statement of Changes in Net Assets tables
- Look for "Purchases/Originations" and "Sales/Repayments" rows
- Extract numerical values from table cells

### Option 2: LLM-Based Extraction
- Use Gemini/GPT to extract specific metrics from MD&A text
- Prompt: "Extract originations, repayments, and non-accrual % from this 10-Q filing"
- More flexible but potentially less reliable

### Option 3: Calculate from Holdings Changes
- **Originations**: Compare holdings between quarters, identify new positions
- **Repayments**: Compare holdings between quarters, identify closed/reduced positions
- **Non-accruals**: Flag holdings with "Non-accrual" or "PIK" in investment type
- Requires historical holdings data

## Notes

- Some BDCs use **custom XBRL extensions** (e.g., `mrcc:Originations`, `invest:NonAccrualInvestments`)
  - Should scan for company-specific taxonomy elements
- Financial statement **tables are often in HTML**, not XBRL instance documents
- May need to parse the `.htm` filing directly instead of relying on XBRL

## Priority

**Medium Priority**: These metrics are nice-to-have but not critical for initial dashboard
- Core metrics (NAV, debt/equity, portfolio composition) are working ✅
- Can add these in a future iteration once we see patterns across multiple BDCs
