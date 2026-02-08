# Automatic Reference Rate Updates

This system automatically fetches current reference rates (SOFR, Prime, Treasury, etc.) from public APIs and updates the calculated yields.

## 🔄 How It Works

1. **Python Script** (`update_reference_rates.py`):
   - Fetches current SOFR from NY Fed API
   - Fetches Prime Rate from Federal Reserve FRED API
   - Attempts to fetch Treasury rates from Treasury.gov
   - Updates `frontend/public/data/reference_rates.json`

2. **Frontend** (`frontend/src/utils/referenceRates.ts`):
   - Loads rates from JSON file on startup
   - Calculates current yields: `spread + reference_rate`
   - Displays calculated yields in **RED**

## 📅 Scheduling Daily Updates

### Windows (Task Scheduler)

1. Open **Task Scheduler** (`Win+R` → `taskschd.msc`)
2. **Create Basic Task**:
   - Name: "BDC Reference Rates Update"
   - Description: "Daily update of SOFR, Prime, and other reference rates"
3. **Trigger**: Daily at **6:00 AM** (before market open)
4. **Action**: Start a program
   - Program: `C:\Users\jsper\dev\Github\new_finance\bdc_extractor_standalone\schedule_rate_updates.bat`
   - Start in: `C:\Users\jsper\dev\Github\new_finance\bdc_extractor_standalone`
5. **Finish** and test by right-clicking → "Run"

### Linux/Mac (cron)

Add to crontab (`crontab -e`):

```bash
# Run daily at 6:00 AM EST
0 6 * * * cd /path/to/bdc_extractor_standalone && python update_reference_rates.py >> logs/rate_updates.log 2>&1
```

## 🔧 Manual Updates

Run manually anytime:

```bash
cd bdc_extractor_standalone
python update_reference_rates.py
```

Output:
```
=== Updating Reference Rates ===
✓ Fetched SOFR: 3.65%
✓ Fetched Prime Rate: 6.75%
✓ Updated 14 reference rates
✓ Saved to frontend/public/data/reference_rates.json

=== Current Reference Rates ===
  SOFR                :   3.65%
  PRIME               :   6.75%
  LIBOR               :   3.91%
  ...
```

## 📊 Data Sources

| Rate | Source | API |
|------|--------|-----|
| **SOFR** | NY Fed | https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json |
| **Prime** | FRED | https://fred.stlouisfed.org/graph/fredgraph.csv?id=DPRIME |
| **Treasury** | Treasury.gov | (API currently unavailable) |

## 🎨 How Yields Are Displayed

- **Red yields** = Calculated from `spread + current_ref_rate`
  - Example: SOFR (3.65%) + Spread (4.75%) = **8.40%** (red)
  - Hover to see tooltip: "Calculated: SOFR + 4.75%"

- **Black yields** = Stated rate from SEC filing
  - Used when spread or ref rate is unavailable

## 🔍 Troubleshooting

### Rates not updating?
1. Check logs: `bdc_extractor_standalone/logs/rate_updates.log`
2. Verify JSON file exists: `frontend/public/data/reference_rates.json`
3. Check browser console for: `[RefRates] Loaded rates (updated: ...)`

### API errors?
- **SOFR**: NY Fed API is usually very reliable
- **Prime**: FRED API is free but rate-limited (should work for daily updates)
- **Treasury**: Currently returning 404, may need alternative source

### Yields still showing old rates?
1. Hard refresh browser: `Ctrl+Shift+R` (Windows) or `Cmd+Shift+R` (Mac)
2. Check `reference_rates.json` was actually updated
3. Verify `last_updated` timestamp in JSON file

## 🚀 Future Enhancements

- [ ] Add fallback APIs for Treasury rates
- [ ] Add email/Slack notifications if rate fetch fails
- [ ] Store historical rates for trending
- [ ] Add UI indicator showing when rates were last updated
- [ ] Fetch additional rates (Fed Funds, EURIBOR, etc.)
