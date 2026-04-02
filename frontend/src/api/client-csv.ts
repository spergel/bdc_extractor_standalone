/**
 * CSV-based data client - reads from consolidated investments.csv
 */
import Papa from 'papaparse';
import { cleanHolding } from '../utils/dataCleaning';

export const API_BASE = '/data';

export type BDCIndex = {
  generated_at: string;
  bdcs: { ticker: string; name: string; periods: string[]; latest?: string }[];
};

export type PeriodSnapshot = {
  ticker: string;
  name: string;
  period: string;
  filing_date?: string;
  accession_number?: string;
  investments: any[];
  generated_at: string;
};

export type TickerProfile = {
  ticker: string;
  name: string;
  price?: Record<string, any>;
  summaryDetail?: Record<string, any>;
  assetProfile?: Record<string, any>;
  generated_at: string;
};

export type PeriodFinancials = {
  ticker: string;
  name: string;
  period?: string;
  period_start?: string | null;
  period_end?: string | null;
  filing_date?: string | null;
  accession_number?: string | null;
  form_type?: string | null;
  income_statement?: Record<string, number | null>;
  gains_losses?: Record<string, number | null>;
  balance_sheet?: Record<string, number | null>;
  shares?: Record<string, number | null>;
  leverage?: Record<string, number | null>;
  derived?: Record<string, number | null>;
  full_income_statement?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  full_cash_flow_statement?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  full_balance_sheet?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  cash_flow_statement?: Record<string, number | null>;
  generated_at?: string;
};

// BDC name mapping
const BDC_NAMES: Record<string, string> = {
  'ARCC': 'Ares Capital Corporation',
  'BBDC': 'Barings BDC, Inc.',
  'BCRED': 'Blackstone Private Credit Fund',
  'BCSF': 'Bain Capital Specialty Finance, Inc.',
  'BXSL': 'Blackstone Secured Lending Fund',
  'CGBD': 'Carlyle Secured Lending, Inc.',
  'CION': 'CION Investment Corporation',
  'CSWC': 'Capital Southwest Corporation',
  'FDUS': 'Fidus Investment Corporation',
  'GAIN': 'Gladstone Investment Corporation',
  'GBDC': 'Golub Capital BDC, Inc.',
  'GSBD': 'Goldman Sachs BDC, Inc.',
  'HTGC': 'Hercules Capital, Inc.',
  'MAIN': 'Main Street Capital Corporation',
  'MFIC': 'MidCap Financial Investment Corporation',
  'MSDL': 'Morgan Stanley Direct Lending Fund',
  'NCDL': 'Nuveen Churchill Direct Lending Corp.',
  'OCSL': 'Oaktree Specialty Lending Corporation',
  'OFS': 'OFS Capital Corporation',
  'PFLT': 'PennantPark Floating Rate Capital Ltd.',
  'PSEC': 'Prospect Capital Corporation',
  'SAR': 'Saratoga Investment Corp.',
  'SLRC': 'SLR Investment Corp.',
  'TCPC': 'BlackRock TCP Capital Corp.',
  'TRIN': 'Trinity Capital Inc.',
  'TSLX': 'TPG Specialty Lending, Inc.',
};

// Cache for the investments index
let investmentsIndexCache: BDCIndex | null = null;
let investmentsIndexPromise: Promise<BDCIndex> | null = null;

type StatementRow = {
  ticker?: string;
  filing_date?: string;
  period_end_date?: string;
  end_date?: string;
  instant_date?: string;
  statement_label?: string;
  statement_type?: string;
  line_item?: string;
  concept?: string;
  value?: number | string;
  label?: string;
  preferred_label?: string;
  order_index?: number;
};

type StatementType = 'income' | 'balance' | 'cash';

function getStatementFile(type: StatementType, ticker: string): string {
  const upper = ticker.toUpperCase();
  switch (type) {
    case 'income': return `financials/${upper}_income_statement.csv`;
    case 'balance': return `financials/${upper}_balance_sheet.csv`;
    case 'cash': return `financials/${upper}_cash_flows.csv`;
  }
}

const statementCache: Map<
  string,
  {
    data: StatementRow[] | null;
    promise: Promise<StatementRow[]> | null;
  }
> = new Map();

async function loadInvestmentsIndex(): Promise<BDCIndex> {
  // Return cached index if available
  if (investmentsIndexCache) {
    return investmentsIndexCache;
  }
  
  // If already loading, return the existing promise
  if (investmentsIndexPromise) {
    return investmentsIndexPromise;
  }
  
  // Start loading index (no-store so we always see latest after re-running consolidation)
  investmentsIndexPromise = (async () => {
    console.log('[CSV] Loading investments_index.json...');
    const res = await fetch(`${API_BASE}/investments_index.json`, { cache: 'no-store' });
    if (!res.ok) {
      throw new Error(`Failed to load investments_index.json: ${res.status}`);
    }
    
    const indexData = await res.json();
    
    // Convert to BDCIndex format
    const bdcs = Object.entries(indexData.tickers).map(([ticker, data]: [string, any]) => ({
      ticker,
      name: BDC_NAMES[ticker] || ticker,
      periods: data.periods || [],
      latest: data.latest || null,
    })).sort((a, b) => a.ticker.localeCompare(b.ticker));
    
    const index: BDCIndex = {
      generated_at: indexData.generated_at || new Date().toISOString(),
      bdcs,
    };
    
    console.log(`[CSV] Loaded index with ${bdcs.length} tickers`);
    investmentsIndexCache = index;
    return index;
  })();
  
  return investmentsIndexPromise;
}

// Cache for period-specific data
const periodDataCache: Map<string, any[]> = new Map();
const periodDataPromises: Map<string, Promise<any[]>> = new Map();

async function loadPeriodInvestments(ticker: string, period: string): Promise<any[]> {
  const upperTicker = ticker.toUpperCase();
  const cacheKey = `${upperTicker}:${period}`;
  
  // Return cached data if available
  if (periodDataCache.has(cacheKey)) {
    return periodDataCache.get(cacheKey)!;
  }
  
  // If already loading, return the existing promise
  if (periodDataPromises.has(cacheKey)) {
    return periodDataPromises.get(cacheKey)!;
  }
  
  // Start loading period-specific file
  const promise = (async () => {
    console.log(`[CSV] Loading investments/${upperTicker}/${period}.csv...`);
    const res = await fetch(`${API_BASE}/investments/${upperTicker}/${period}.csv`);
    if (!res.ok) {
      throw new Error(`Failed to load investments/${upperTicker}/${period}.csv: ${res.status}`);
    }
    
    const text = await res.text();
    const parsed = Papa.parse(text, {
      header: true,
      dynamicTyping: false, // Don't auto-convert - handle manually to avoid comma issues
      skipEmptyLines: true,
      transformHeader: (header) => header.trim(), // Trim whitespace from headers
    });
    
    if (parsed.errors.length > 0) {
      console.warn(`[CSV] Parse errors in ${upperTicker}/${period}.csv:`, parsed.errors);
    }
    
    // Clean and convert numeric fields manually
    const cleanedData = parsed.data.map((row: any) => {
      const cleaned: any = { ...row };
      
      // Clean numeric fields that might have commas or formatting
      const numericFields = ['fair_value', 'cost', 'principal_amount', 'amortized_cost', 
                             'interest_rate', 'spread', 'pik_rate', 'cash_rate', 
                             'percent_of_net_assets', 'undrawn_commitment', 'commitment_limit'];
      
      for (const field of numericFields) {
        if (cleaned[field] !== null && cleaned[field] !== undefined && cleaned[field] !== '') {
          const str = String(cleaned[field]).replace(/,/g, '').replace(/[^\d.-]/g, '');
          const num = parseFloat(str);
          cleaned[field] = isNaN(num) ? cleaned[field] : num;
        }
      }
      
      return cleanHolding(cleaned);
    });

    console.log(`[CSV] Loaded ${cleanedData.length} investments for ${upperTicker} period ${period}`);
    periodDataCache.set(cacheKey, cleanedData);
    periodDataPromises.delete(cacheKey);
    return cleanedData;
  })();
  
  periodDataPromises.set(cacheKey, promise);
  return promise;
}

async function loadStatementCsv(type: StatementType, ticker: string): Promise<StatementRow[]> {
  const cacheKey = `${type}:${ticker.toUpperCase()}`;
  let cache = statementCache.get(cacheKey);
  if (!cache) {
    cache = { data: null, promise: null };
    statementCache.set(cacheKey, cache);
  }
  if (cache.data) {
    return cache.data;
  }
  if (cache.promise) {
    return cache.promise;
  }

  const c = cache;
  c.promise = (async () => {
    const filename = getStatementFile(type, ticker);
    console.log(`[CSV] Loading ${filename}...`);
    const res = await fetch(`${API_BASE}/${filename}`);
    if (!res.ok) {
      // Return empty array for missing files (e.g. no cash flow data)
      console.warn(`[CSV] ${filename} not found (${res.status}), returning empty`);
      c.data = [];
      c.promise = null;
      return [];
    }

    const text = await res.text();
    const parsed = Papa.parse<StatementRow>(text, {
      header: true,
      dynamicTyping: true,
      skipEmptyLines: true,
    });

    if (parsed.errors.length > 0) {
      console.warn(`[CSV] Parse errors in ${filename}:`, parsed.errors);
    }

    c.data = parsed.data;
    c.promise = null;
    console.log(`[CSV] Loaded ${parsed.data.length} rows from ${filename}`);
    return parsed.data;
  })();

  return c.promise;
}

function sanitizeValue(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function addStatementRows(
  rows: StatementRow[],
  period: string,
  target: Record<string, number | null>,
  full: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>
) {
  for (const row of rows) {
    const rawConcept = (row.concept ?? '').toString().trim();
    const rawLabel = (row.line_item ?? row.label ?? row.preferred_label ?? '').toString().trim();
    const concept = rawConcept || rawLabel;
    if (!concept) {
      continue;
    }
    const value = sanitizeValue(row.value ?? null);
    // Filings often repeat NetAssetValuePerShare for current + prior quarters; last row was
    // overwriting and could show a stale comparative NAV. Keep the first (current period).
    const isNavPerShare = concept.replace(/:/g, '').toLowerCase().endsWith('netassetvaluepershare');
    if (isNavPerShare && target[concept] != null) {
      continue;
    }
    target[concept] = value;
    full[concept] = {
      label: rawLabel || concept,
      concept,
      value,
      period,
    };
  }
}

function derivePeriodEnd(rows: StatementRow[]): string | null {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const candidates = [row.period_end_date, row.instant_date as any, row.end_date as any, row.filing_date];
    for (const candidate of candidates) {
      const d = (candidate ?? '').toString().trim();
      if (!d) continue;
      counts.set(d, (counts.get(d) ?? 0) + 1);
    }
  }
  if (!counts.size) return null;
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0][0];
}

function pickNavPerShareFromBalanceSheet(bs: Record<string, number | null>): number | null {
  const direct = bs['NetAssetValuePerShare'];
  if (direct != null && direct > 0) return direct;
  for (const [k, v] of Object.entries(bs)) {
    if (v == null || v <= 0) continue;
    if (k.replace(/:/g, '').toLowerCase() === 'netassetvaluepershare') return v;
  }
  return null;
}

/**
 * Unique SEC filing dates (YYYY-MM-DD) present in consolidated income + balance CSVs.
 * Usually a superset of holdings index periods — use for Financials so Q1/Q2/Q3/Q4 aren’t
 * dropped just when a quarter wasn’t processed for portfolio extraction.
 */
export async function fetchStatementFilingDates(ticker: string): Promise<string[]> {
  const upper = ticker.toUpperCase();
  const [income, balance] = await Promise.all([
    loadStatementCsv('income', upper),
    loadStatementCsv('balance', upper),
  ]);
  const seen = new Set<string>();
  for (const row of income) {
    const fd = (row.filing_date ?? '').toString().trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(fd)) seen.add(fd);
  }
  for (const row of balance) {
    const fd = (row.filing_date ?? '').toString().trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(fd)) seen.add(fd);
  }
  return Array.from(seen).sort((a, b) => a.localeCompare(b));
}

export async function fetchIndex(): Promise<BDCIndex> {
  return await loadInvestmentsIndex();
}

export async function fetchPeriods(ticker: string): Promise<string[]> {
  const index = await loadInvestmentsIndex();
  const bdc = index.bdcs.find(b => b.ticker === ticker.toUpperCase());
  return bdc?.periods || [];
}

export async function fetchPeriodSnapshot(ticker: string, period: string): Promise<PeriodSnapshot> {
  // Load period-specific file directly - no filtering needed!
  const investments = await loadPeriodInvestments(ticker, period);
  
  return {
    ticker: ticker.toUpperCase(),
    name: BDC_NAMES[ticker.toUpperCase()] || ticker,
    period,
    filing_date: period,
    accession_number: investments[0]?.accession_number,
    investments,
    generated_at: new Date().toISOString(),
  };
}

export async function fetchProfile(ticker: string): Promise<TickerProfile | null> {
  // Profile data still comes from JSON files if they exist
  try {
    const res = await fetch(`${API_BASE}/${ticker.toUpperCase()}/profile.json`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchFinancials(ticker: string, period: string): Promise<PeriodFinancials | null> {
  const upperTicker = ticker.toUpperCase();
  const [incomeData, balanceData, cashData] = await Promise.all([
    loadStatementCsv('income', upperTicker),
    loadStatementCsv('balance', upperTicker),
    loadStatementCsv('cash', upperTicker),
  ]);

  const rowMatchesPeriod = (row: StatementRow) => {
    const filingDate = (row.filing_date ?? '').toString().trim();
    const periodEndDate = (row.period_end_date ?? '').toString().trim();
    const endDate = ((row as any).end_date ?? '').toString().trim();
    const instantDate = ((row as any).instant_date ?? '').toString().trim();
    return (
      filingDate === period ||
      periodEndDate === period ||
      endDate === period ||
      instantDate === period
    );
  };

  const incomeRows = incomeData.filter(rowMatchesPeriod);
  const balanceRows = balanceData.filter(rowMatchesPeriod);
  const cashRows = cashData.filter(rowMatchesPeriod);

  const hasData = incomeRows.length + balanceRows.length + cashRows.length > 0;
  if (!hasData) {
    return null;
  }

  const result: PeriodFinancials = {
    ticker: upperTicker,
    name: BDC_NAMES[upperTicker] || upperTicker,
    period,
    period_end: derivePeriodEnd([...incomeRows, ...balanceRows, ...cashRows]),
    income_statement: {},
    balance_sheet: {},
    derived: {},
    shares: {},
    leverage: {},
    full_income_statement: {},
    full_balance_sheet: {},
    full_cash_flow_statement: {},
  };

  addStatementRows(incomeRows, period, result.income_statement!, result.full_income_statement!);
  addStatementRows(balanceRows, period, result.balance_sheet!, result.full_balance_sheet!);
  if (cashRows.length > 0) {
    result.cash_flow_statement = {};
    addStatementRows(cashRows, period, result.cash_flow_statement, result.full_cash_flow_statement!);
  }

  const navFromFacts = pickNavPerShareFromBalanceSheet(result.balance_sheet!);
  if (navFromFacts != null) {
    result.balance_sheet!.nav_per_share = navFromFacts;
  }

  return result;
}







