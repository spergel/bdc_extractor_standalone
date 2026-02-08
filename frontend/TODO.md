# Frontend TODO

## 🎨 UI/UX Improvements

### High Priority
- [ ] **Price Chart/Sparkline** - `TickerWindow.tsx` is currently a placeholder with "LIVE MOCK" badge
  - Need to integrate real price data (likely from yfinance API that ProfileCard uses)
  - Add sparkline visualization with cyberpunk styling
  - Show price trends over time (1D, 1W, 1M, 1Y)
  
- [ ] **ExportBar Component** - Component exists but not integrated
  - Determine if this should be used for holdings export
  - Or remove if not needed (currently using direct download link)

- [x] **Holdings Table Performance** - Optimized sorting performance ✅
  - Implemented useTransition for non-blocking sorting ✅
  - Added pre-computed sort keys ✅
  - Added loading spinner during sort operations ✅
  - Added React.memo optimization ✅
- [ ] **Holdings Table Performance** - Further optimizations if needed
  - Consider virtualization if dataset grows beyond 1000 rows
  - Optimize cell rendering further if needed

- [ ] **Mobile Layout Overhaul**
  - Replace sidebar with dropdown selector on small screens
  - Ensure main content (analytics/changes/etc.) fills viewport below dropdown
  - Add responsive tests for tablets and phones

### Medium Priority
- [ ] **Financials Display Names** - Some statement line items still need better labels
  - Complete the `DISPLAY_NAME_OVERRIDES` map in `FinancialsPanel.tsx`
  - Add more edge cases as they're discovered
  
- [x] **Chart Tooltips** - Mouseover tooltips on analytics charts ✅
  - Pie charts tooltips ✅
  - Donut chart tooltips ✅
  - Maturity ladder tooltips ✅
  - Histogram tooltips ✅
  
- [ ] **Chart Improvements** (if charts are re-added)
  - Add more metrics to chart selector (e.g., Total Assets, Total Liabilities, Debt/Equity)
  - Add comparison mode (overlay multiple metrics on same chart)
  - Add export chart as image functionality
  - Improve tooltip positioning on mobile devices

- [ ] **Responsive Design**
  - Test and fix mobile layout (especially charts grid)
  - Ensure tables scroll properly on small screens
  - Optimize sidebar for mobile (maybe collapse to icon bar)

- [ ] **Accessibility**
  - Add ARIA labels to interactive elements
  - Keyboard navigation for tabs
  - Screen reader support for charts

### Low Priority / Nice to Have
- [ ] **Dark Mode Variants** - Consider different cyberpunk themes
- [x] **Data Export** - CSV export available in Holdings table ✅
- [ ] **Search/Filter** - Add search box to holdings table
- [ ] **Column Customization** - Let users show/hide columns in holdings
- [x] **Period Comparison** - Diff viewer provides side-by-side comparison of different periods ✅
- [ ] **Bookmarks/Favorites** - Save favorite BDCs for quick access
- [ ] **Keyboard Shortcuts** - Quick navigation shortcuts

## 🐛 Bug Fixes

- [x] **Chart Tooltip Positioning** - Fixed tooltip positioning to be relative to containers ✅
- [x] **HTML Entity Decoding** - Added decodeHtmlEntities function to HoldingsTable ✅
- [ ] **Period Selector State** - Ensure period selection persists correctly when switching tabs

## 🔧 Technical Debt

- [ ] **Type Safety** - Add proper TypeScript types for all API responses
- [ ] **Error Handling** - Better error states for API failures
- [ ] **Loading States** - Consistent loading indicators across all components
- [x] **Code Organization** - Removed ChartsPanel (Charts tab removed) ✅
- [ ] **Code Organization** - Consider splitting large components (especially AnalyticsPanel if it grows)
- [ ] **Performance Monitoring** - Add performance metrics/logging
- [ ] **Testing** - Add unit tests for critical components (charts, table sorting)

## 📊 Data & Features

- [ ] **Additional Metrics** - Add more financial metrics to charts
  - Price-to-NAV ratio
  - Dividend coverage ratio
  - Expense ratio
  - Portfolio concentration metrics
  
- [ ] **Historical Data** - Extend historical period options
  - Custom date range selector
  - Year-over-year comparisons

### Holdings Analytics & Diff Viewer ✅ COMPLETED

- [x] **GitHub-like Diff Viewer** - Quarterly changes comparison ✅
  - Period selector (base vs compare period) ✅
  - Summary banner (adds/removals/changes counts and values) ✅
  - Table diff with inline change indicators (green/red/yellow) ✅
  - Cell-level highlighting for value changes ✅
  - Filters for changes above threshold (e.g., $1M or 5%) ✅
  - Individual holding change cards with detailed breakdown ✅

- [x] **Analytics Panel** - Portfolio-level insights ✅
  - **Pie Charts:** ✅
    - Industry distribution (by count and $ fair value) ✅
    - Investment type distribution (by count and $ fair value) ✅
    - Mouseover tooltips with detailed information ✅
  - **Rate Structure Analysis:** ✅
    - Variable vs fixed rate breakdown (donut chart) ✅
    - Spread distribution (histogram/bar chart) ✅
    - Floor rate usage and average floors ✅
    - Average spread by industry or investment type ✅
    - Mouseover tooltips ✅
  - **Maturity Ladder:** ✅
    - Time buckets (0-6m, 6-12m, 1-2y, 2-3y, 3-5y, 5y+) ✅
    - Visualization showing maturity concentration ✅
    - Mouseover tooltips ✅
  - **PIK Analysis:** ✅
    - Count and $ amount of PIK positions ✅
    - Average PIK rate where present ✅
    - PIK trend over time (requires multi-period data) ⏳
  - **Fair Value vs Principal Analysis:** ✅
    - FV/Principal ratio distribution (histogram) ✅
    - FV/Cost ratio distribution (histogram) ✅
    - Identification of positions where FV ≈ Principal (potential red flag) ✅
    - Analysis of companies marking FV as Principal quickly (requires multi-period tracking) ⏳

- [x] **Red Flag Detection** - Automated risk identification ✅
  - FV ≈ Principal heuristic (abs(FV - Principal)/max(Principal,1) < 1% and age > 2Q) ✅
  - FV below Principal (FV/Principal < 0.95) ✅
  - FV below Cost materially (FV/Cost < 0.95) ✅
  - PIK present or high PIK rate ✅
  - Near maturities (within 12 months) with large FV ✅
  - Low spread vs peers (if peer data available) ⏳
  - Badge/indicator system in Holdings table ✅
  - Dedicated "Watchlist" table for flagged positions ✅
  - Quarterly recomputation and trend tracking (requires multi-period data) ⏳

- [x] **Concentration Metrics** ✅
  - Top 10 holdings by fair value ✅
  - Herfindahl index for industry/investment type concentration ✅
  - Exposure to specific reference rates (SOFR, LIBOR, etc.) ⏳

- [ ] **Turnover Analysis** (requires multi-period data)
  - Additions count and $ amount
  - Exits count and $ amount
  - Net originations
  - Paydowns vs new originations
  - Valuation attribution (unrealized gains/losses)

## 🎯 Future Enhancements

- [ ] **Multi-BDC Comparison** - Compare multiple BDCs side-by-side
  - Separate page for comparison view
  - Side-by-side tables and charts
  - Peer quartile analysis
  - This is a later feature, will likely require separate page/route

- [ ] **Real-time Updates** - WebSocket integration for live price updates
- [ ] **Alerts/Notifications** - Price alerts, NAV threshold alerts
- [ ] **Advanced Analytics** - Risk metrics, correlation analysis
- [ ] **Data Export Suite** - Export reports (PDF, Excel, JSON)

