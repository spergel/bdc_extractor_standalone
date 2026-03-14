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
# Extract all BDCs (latest quarter)
python process_all_bdcs.py --years-back 0

# Done! Clean, standardized data ready in frontend/public/data/
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
frontend/public/data/
├── investments/
│   ├── ARCC.csv                    # All ARCC holdings (consolidated)
│   ├── ARCC/
│   │   ├── 2025-11-06.csv         # Quarterly snapshot
│   │   ├── 2025-08-07.csv
│   │   └── ...
│   └── investments_index.json      # Metadata index
├── financials/
│   ├── ARCC_balance_sheet.csv
│   ├── ARCC_income_statement.csv
│   └── ...
├── balance_sheets.csv               # All BDCs consolidated
├── income_statements.csv
└── cash_flows.csv
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
- `src/extraction/llm_table_scraper.py` - LLM-powered table extraction
- `src/extraction/financial_statements_extractor.py` - Financial data extraction
- `src/extraction/sec_api_client.py` - SEC EDGAR API interface
- `src/processing/post_process_extraction.py` - Automatic data cleaning
- `src/processing/standardization_rules.py` - Standardization logic
- `src/consolidation/consolidate_investments.py` - Merge investment CSVs
- `src/consolidation/consolidate_financial_statements.py` - Merge financial CSVs

## 🎨 Frontend Integration

The output is designed for React/Next.js frontends:

```typescript
import investmentsIndex from '@/public/data/investments_index.json';
import { parse } from 'csv-parse/sync';

// Load all tickers
const tickers = investmentsIndex.tickers; // ["ARCC", "MAIN", ...]

// Load specific ticker's latest data
const response = await fetch('/data/investments/ARCC.csv');
const data = parse(await response.text(), { columns: true });
```

## 📈 Supported BDCs

Currently configured for 10 major BDCs:
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

### Getting Help

See [docs/OPERATIONS.md](docs/OPERATIONS.md) and [docs/PIPELINE_AND_ROADMAP.md](docs/PIPELINE_AND_ROADMAP.md) for troubleshooting and workflow guidance.

## 🎯 Roadmap

- [ ] Add more BDCs
- [ ] Historical data backfill automation
- [ ] Real-time monitoring for new filings
- [ ] API endpoint for data access
- [ ] Interactive data quality dashboard

---

**Last Updated:** February 2026  
**Version:** 2.0 (Full Standardization)
