import Papa from 'papaparse';

// Enhanced holding type with all new cleaned fields
export type Holding = {
  // Core identification
  ticker?: string;
  filing_date?: string;
  company_id?: string;
  company_name_clean?: string;
  loan_description?: string;
  
  // Classification
  investment_type_standardized?: string;
  industry_clean?: string;
  asset_class?: string;
  seniority?: string;
  
  // Financial data (in thousands)
  fair_value_thousands?: number;
  principal_amount_thousands?: number;
  cost_thousands?: number;
  amortized_cost_thousands?: number;
  percent_of_net_assets?: number;
  commitment_limit_thousands?: number;
  undrawn_commitment_thousands?: number;
  
  // Interest rate components (as decimals)
  total_interest_rate?: number;
  reference_rate_clean?: string;
  spread_clean?: number;
  is_pik?: boolean;
  payment_frequency?: string;
  floor_rate?: number;
  
  // Dates
  acquisition_date?: string;
  maturity_date?: string;
  
  // Derived fields
  investment_size_category?: string;
  investment_age_years?: number;
  remaining_term_years?: number;
  effective_yield_pct?: number;
  is_floating_rate?: boolean;
  is_fixed_rate?: boolean;
  is_performing?: boolean;
  
  // Par value
  par_value_thousands?: number;
  
  // Legacy fields for backward compatibility
  company_name?: string;
  investment_type?: string;
  industry?: string;
  business_description?: string;
  interest_rate?: string;
  reference_rate?: string;
  spread?: string;
  fair_value?: string;
  principal_amount?: string;
  cost?: string;
  amortized_cost?: string;
  pik_rate?: string;
};

export type PortfolioSummary = {
  ticker: string;
  filing_date: string;
  total_investments_count: number;
  total_fair_value_millions: number;
  senior_debt_count: number;
  senior_debt_fair_value_millions: number;
  equity_count: number;
  equity_fair_value_millions: number;
  weighted_avg_yield: number;
  num_industries: number;
  top_industry: string;
  num_companies: number;
  largest_position_millions: number;
  pct_floating_rate: number;
  pct_senior_secured: number;
};

export type CompanyExposure = {
  company_id?: string;
  company_name: string;
  num_bdcs_invested: number;
  bdcs_invested: string;
  total_exposure_millions: number;
  primary_industry: string;
  avg_interest_rate: number;
  most_common_investment_type: string;
};

/** Per-company breakdown: who they owe (BDCs), due-date buckets, investment types. From company_detail.json. */
export type CompanyDetail = {
  by_bdc: Record<string, number>;
  by_maturity: Record<string, number>;
  by_investment_type: Record<string, number>;
};

export type CompanyDetailPayload = {
  generated_at: string;
  detail: Record<string, CompanyDetail>;
};

export type CompanyIndexEntry = {
  company_id: string;
  canonical_name: string;
  name_variants: string[];
};

export type CompaniesIndex = {
  generated_at: string;
  companies: CompanyIndexEntry[];
};

export type CompanyProfile = {
  company_id: string;
  canonical_name: string;
  description?: string;
  industry?: string;
  website?: string;
  location?: string;
  employee_range?: string;
  leadership?: string;
  funding?: string;
  recent_news?: string;
  source?: string;
  updated_at?: string;
};

/** Holding plus its company profile when available. Use enrichHoldingsWithProfiles to build. */
export type HoldingWithProfile = Holding & { profile?: CompanyProfile };

export type IndustrySummary = {
  industry: string;
  num_investments: number;
  num_companies: number;
  total_fair_value_millions: number;
  avg_interest_rate: number;
  num_bdcs: number;
};

// Load and parse CSV with typed output
async function loadCsv<T>(path: string): Promise<T[]> {
  const res = await fetch(path);
  const text = await res.text();
  const parsed = Papa.parse<T>(text, { 
    header: true, 
    dynamicTyping: true, 
    skipEmptyLines: true 
  });
  return parsed.data || [];
}

// Load all investments (enhanced version)
export async function loadInvestments(): Promise<Holding[]> {
  return loadCsv<Holding>('/data/investments.csv');
}

// Load investments for a specific BDC
export async function loadBDCInvestments(ticker: string, date?: string): Promise<Holding[]> {
  const allInvestments = await loadInvestments();
  let filtered = allInvestments.filter(h => 
    h.ticker?.toUpperCase() === ticker.toUpperCase()
  );
  
  if (date) {
    filtered = filtered.filter(h => h.filing_date === date);
  } else {
    // Get most recent filing
    const dates = Array.from(
      new Set(filtered.map(h => h.filing_date).filter(d => d))
    ).sort();
    if (dates.length > 0) {
      const latestDate = dates[dates.length - 1];
      filtered = filtered.filter(h => h.filing_date === latestDate);
    }
  }
  
  return filtered;
}

// Load portfolio summaries
export async function loadPortfolioSummaries(): Promise<PortfolioSummary[]> {
  return loadCsv<PortfolioSummary>('/data/portfolio_summaries.csv');
}

// Load company exposures (keyed by company_id when available)
export async function loadCompanyExposures(): Promise<CompanyExposure[]> {
  return loadCsv<CompanyExposure>('/data/company_exposures.csv');
}

// Load companies index (company_id -> canonical_name, name_variants)
export async function loadCompaniesIndex(): Promise<CompaniesIndex> {
  const res = await fetch('/data/companies_index.json');
  if (!res.ok) {
    return { generated_at: '', companies: [] };
  }
  return res.json();
}

// Load company profiles (company_id -> profile with description, etc.)
export async function loadCompanyProfiles(): Promise<Record<string, CompanyProfile>> {
  const res = await fetch('/data/company_profiles.json');
  if (!res.ok) {
    return {};
  }
  const data = await res.json();
  const raw = data?.profiles;
  if (typeof raw === 'object' && raw !== null) {
    return raw as Record<string, CompanyProfile>;
  }
  if (Array.isArray(raw)) {
    return (raw as CompanyProfile[]).reduce<Record<string, CompanyProfile>>((acc, p) => {
      if (p?.company_id) acc[p.company_id] = p;
      return acc;
    }, {});
  }
  return {};
}

// Load company detail (per-BDC exposure, maturity buckets, investment type). Optional artifact.
export async function loadCompanyDetail(): Promise<Record<string, CompanyDetail>> {
  const res = await fetch('/data/company_detail.json');
  if (!res.ok) return {};
  const data = (await res.json()) as CompanyDetailPayload;
  return data?.detail ?? {};
}

/**
 * Attach company profile to each holding by company_id. Use this so components
 * get a single "holdings with profile" list; profile is undefined when missing.
 */
export function enrichHoldingsWithProfiles(
  holdings: Holding[],
  profiles: Record<string, CompanyProfile>
): HoldingWithProfile[] {
  return holdings.map((h) => ({
    ...h,
    profile: h.company_id ? profiles[h.company_id] : undefined,
  }));
}

// Load industry summaries
export async function loadIndustrySummaries(): Promise<IndustrySummary[]> {
  return loadCsv<IndustrySummary>('/data/industry_summaries.csv');
}

// Get available BDCs and their periods
export async function getBDCList(): Promise<Array<{ticker: string, dates: string[]}>> {
  const allInvestments = await loadInvestments();
  const bdcMap = new Map<string, Set<string>>();
  
  allInvestments.forEach(h => {
    if (!h.ticker || !h.filing_date) return;
    
    if (!bdcMap.has(h.ticker)) {
      bdcMap.set(h.ticker, new Set());
    }
    bdcMap.get(h.ticker)!.add(h.filing_date);
  });
  
  return Array.from(bdcMap.entries()).map(([ticker, dates]) => ({
    ticker,
    dates: Array.from(dates).sort()
  })).sort((a, b) => a.ticker.localeCompare(b.ticker));
}

// Legacy function for backward compatibility
export async function loadOFSCsv(path: string): Promise<Holding[]> {
  const res = await fetch(path);
  const text = await res.text();
  const parsed = Papa.parse<any>(text, { header: true, dynamicTyping: false, skipEmptyLines: true });
  return (parsed.data || []).map((r: any) => normalizeHolding(r));
}

function normalizeHolding(row: any): Holding {
  const normalizeType = (t?: string) => (t ? t.replace(/\s+/g, ' ').trim() : '');
  const parseNum = (val: any) => {
    if (val === null || val === undefined || val === '') return undefined;
    const num = parseFloat(val);
    return isNaN(num) ? undefined : num;
  };
  const parseBool = (val: any) => {
    if (val === null || val === undefined || val === '') return undefined;
    return val === true || val === 'true' || val === 'True' || val === 1 || val === '1';
  };
  
  return {
    ticker: row.ticker || undefined,
    filing_date: row.filing_date || undefined,
    company_id: row.company_id || undefined,
    company_name_clean: row.company_name_clean || row.company_name || undefined,
    loan_description: row.loan_description || undefined,
    
    investment_type_standardized: normalizeType(row.investment_type_standardized || row.investment_type) || undefined,
    industry_clean: row.industry_clean || row.industry || undefined,
    asset_class: row.asset_class || undefined,
    seniority: row.seniority || undefined,
    
    fair_value_thousands: parseNum(row.fair_value_thousands ?? row.fair_value),
    principal_amount_thousands: parseNum(row.principal_amount_thousands ?? row.principal_amount),
    cost_thousands: parseNum(row.cost_thousands ?? row.cost),
    amortized_cost_thousands: parseNum(row.amortized_cost_thousands ?? row.amortized_cost),
    percent_of_net_assets: parseNum(row.percent_of_net_assets),
    commitment_limit_thousands: parseNum(row.commitment_limit_thousands ?? row.commitment_limit),
    undrawn_commitment_thousands: parseNum(row.undrawn_commitment_thousands ?? row.undrawn_commitment),
    
    total_interest_rate: parseNum(row.total_interest_rate),
    reference_rate_clean: row.reference_rate_clean || row.reference_rate || undefined,
    spread_clean: parseNum(row.spread_clean ?? row.spread),
    is_pik: parseBool(row.is_pik),
    payment_frequency: row.payment_frequency || undefined,
    floor_rate: parseNum(row.floor_rate),
    
    acquisition_date: row.acquisition_date || undefined,
    maturity_date: row.maturity_date || undefined,
    
    investment_size_category: row.investment_size_category || undefined,
    investment_age_years: parseNum(row.investment_age_years),
    remaining_term_years: parseNum(row.remaining_term_years),
    effective_yield_pct: parseNum(row.effective_yield_pct),
    is_floating_rate: parseBool(row.is_floating_rate),
    is_fixed_rate: parseBool(row.is_fixed_rate),
    is_performing: parseBool(row.is_performing),
    
    par_value_thousands: parseNum(row.par_value_thousands),
    
    // Legacy fields
    company_name: row.company_name || undefined,
    investment_type: row.investment_type || undefined,
    industry: row.industry || undefined,
    business_description: row.business_description || undefined,
    interest_rate: row.interest_rate || undefined,
    reference_rate: row.reference_rate || undefined,
    spread: row.spread || undefined,
    fair_value: row.fair_value || undefined,
    principal_amount: row.principal_amount || undefined,
    cost: row.cost || undefined,
    amortized_cost: row.amortized_cost || undefined,
    pik_rate: row.pik_rate || undefined,
  };
}
