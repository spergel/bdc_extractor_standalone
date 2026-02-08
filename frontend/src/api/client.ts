export const API_BASE = '/data';

export async function getJSON<T>(path: string, init?: RequestInit): Promise<T | null> {
  const url = `${API_BASE}${path}`;
  try {
    console.debug('[getJSON] fetching', url);
    const res = await fetch(url, init);
    if (!res.ok) {
      // Return null for 404s (missing files) instead of throwing
      if (res.status === 404) {
        console.debug('[getJSON] not found (404)', url);
        return null;
      }
      const text = await res.text().catch(() => '');
      console.error('[getJSON] non-OK response', { url, status: res.status, text });
      throw new Error(`Failed to fetch ${path}: ${res.status}`);
    }
    
    // Check content type and peek at response to detect HTML (404 pages)
    const contentType = res.headers.get('content-type') || '';
    const text = await res.text();
    
    // If it's HTML (likely a 404 page), return null
    if (text.trim().toLowerCase().startsWith('<!doctype') || text.trim().toLowerCase().startsWith('<html')) {
      console.debug('[getJSON] received HTML instead of JSON (likely 404)', url);
      return null;
    }
    
    // Try to parse as JSON
    let json: T;
    try {
      json = JSON.parse(text) as T;
    } catch (parseErr) {
      // If parsing fails, it's not valid JSON
      console.debug('[getJSON] invalid JSON', url);
      return null;
    }
    // Best-effort: log a small summary to avoid spamming console with huge payloads
    const summary = Array.isArray(json)
      ? { type: 'array', length: json.length }
      : typeof json === 'object' && json !== null
      ? { type: 'object', keys: Object.keys(json as object).slice(0, 10) }
      : { type: typeof json };
    console.debug('[getJSON] success', { url, summary });
    return json;
  } catch (err) {
    // If it's a JSON parse error, it might be HTML (404 page), so return null silently
    if (err instanceof SyntaxError) {
      console.debug('[getJSON] invalid JSON (likely 404 HTML)', url);
      return null;
    }
    // Only log actual errors, not expected missing files
    if (err instanceof Error && err.message.includes('404')) {
      console.debug('[getJSON] file not found', url);
      return null;
    }
    console.error('[getJSON] error', { url, err });
    throw err;
  }
}

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

export async function fetchIndex() {
  return getJSON<BDCIndex>('/index.json');
}

export async function fetchPeriods(ticker: string) {
  return getJSON<string[]>(`/${ticker.toUpperCase()}/periods.json`);
}

export async function fetchPeriodSnapshot(ticker: string, period: string) {
  return getJSON<PeriodSnapshot>(`/${ticker.toUpperCase()}/investments_${period}.json`);
}

export type TickerProfile = {
  ticker: string;
  name: string;
  price?: Record<string, any>;
  summaryDetail?: Record<string, any>;
  assetProfile?: Record<string, any>;
  generated_at: string;
};

export async function fetchProfile(ticker: string) {
  const path = `/${ticker.toUpperCase()}/profile.json`;
  const url = `${API_BASE}${path}`;
  try {
    return await getJSON<TickerProfile>(path);
  } catch (err) {
    // Gracefully handle missing or non-JSON profile files
    return null as unknown as TickerProfile;
  }
}

// Financials
export type PeriodFinancials = {
  ticker: string;
  name: string;
  period?: string;
  period_start?: string | null;
  period_end?: string | null;
  filing_date?: string | null;
  accession_number?: string | null;
  form_type?: string | null; // '10-Q' for quarterly, '10-K' for annual
  income_statement?: Record<string, number | null>;
  gains_losses?: Record<string, number | null>;
  balance_sheet?: Record<string, number | null>;
  shares?: Record<string, number | null>;
  leverage?: Record<string, number | null>;
  derived?: Record<string, number | null>;
  // Full statements (per-concept objects with label/value)
  full_income_statement?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  full_cash_flow_statement?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  full_balance_sheet?: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>;
  generated_at?: string;
};

export async function fetchFinancials(ticker: string, period: string) {
  const base = `/${ticker.toUpperCase()}`;
  // Prefer CSVs when available
  async function tryCsv(prefix: string) {
    const url = `${base}/${prefix}_${period}.csv`;
    const text = await getText(url);
    if (!text) return null;
    // Reject HTML (likely 404 pages)
    if (text.trim().toLowerCase().startsWith('<!doctype') || text.trim().toLowerCase().startsWith('<html')) {
      return null;
    }
    // Simple CSV parse supporting quotes
    const rows: Array<Record<string, string>> = [];
    const lines = text.split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) return null;
    const headers = parseCsvLine(lines[0]);
    // Must have at least 'key' or 'concept' header to be valid
    if (!headers.includes('key') && !headers.includes('concept')) {
      return null;
    }
    for (let i = 1; i < lines.length; i++) {
      const fields = parseCsvLine(lines[i]);
      if (fields.length === 0) continue;
      const row: Record<string, string> = {};
      headers.forEach((h, idx) => {
        row[h] = fields[idx] ?? '';
      });
      rows.push(row);
    }
    // Only return if we have valid rows with non-empty keys
    const validRows = rows.filter(r => {
      const key = r.key || r['key'] || r.concept || '';
      return key && key.trim();
    });
    return validRows.length > 0 ? validRows : null;
  }

  function parseCsvLine(line: string): string[] {
    const out: string[] = [];
    let cur = '';
    let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inQ) {
        if (c === '"') {
          if (line[i + 1] === '"') { cur += '"'; i++; } else { inQ = false; }
        } else { cur += c; }
      } else {
        if (c === ',') { out.push(cur); cur = ''; }
        else if (c === '"') { inQ = true; }
        else { cur += c; }
      }
    }
    out.push(cur);
    return out;
  }

  async function getText(url: string): Promise<string | null> {
    try {
      const res = await fetch(`/data${url}`);
      if (!res.ok) return null;
      const ct = res.headers.get('Content-Type') || '';
      const txt = await res.text();
      if (!txt) return null;
      if (ct.includes('text') || ct.includes('csv') || (!ct && txt.length > 0)) {
        return txt;
      }
      return null;
    } catch {
      return null;
    }
  }

  // Attempt load CSVs
  const incomeRows = await tryCsv('income');
  const balanceRows = await tryCsv('balance');
  const cashRows = await tryCsv('cashflow');
  // Only use CSV if we have actual data (not empty arrays)
  const hasCsvData = (incomeRows && incomeRows.length > 0) || (balanceRows && balanceRows.length > 0) || (cashRows && cashRows.length > 0);
  if (hasCsvData) {
    const result: PeriodFinancials = {
      ticker: ticker.toUpperCase(),
      name: ticker.toUpperCase(),
      period,
      income_statement: {},
      balance_sheet: {},
      derived: {},
      shares: {},
      leverage: {},
      full_income_statement: {},
      full_balance_sheet: {},
      full_cash_flow_statement: {},
    };
    const consume = (rows: Array<Record<string, string>> | null | undefined, target: Record<string, number | null>, full: Record<string, { label: string; concept: string; value: number | null; period?: string | null }>) => {
      if (!rows || rows.length === 0) return;
      for (const r of rows) {
        const key = r.key || r['key'] || '';
        // Skip rows with empty keys - they're invalid
        if (!key || !key.trim()) continue;
        const label = r.label || '';
        const concept = r.concept || key;
        // Skip if concept is still empty after fallback
        if (!concept || !concept.trim()) continue;
        const valueStr = r.value || '';
        const val = valueStr === '' ? null : Number(valueStr);
        target[concept] = val;
        full[concept] = { label, concept, value: val, period };
      }
    };
    consume(incomeRows, result.income_statement!, result.full_income_statement!);
    consume(balanceRows, result.balance_sheet!, result.full_balance_sheet!);
    // Cash flow should go into a proper cash_flow_statement structure
    if (cashRows) {
      result.cash_flow_statement = {};
      consume(cashRows, result.cash_flow_statement, result.full_cash_flow_statement!);
    }
    return result;
  }

  // Fallback to JSON
  const json = await getJSON<PeriodFinancials>(`/${ticker.toUpperCase()}/financials_${period}.json`);
  if (!json) {
    console.warn(`[fetchFinancials] Failed to load financials for ${ticker} period ${period}`);
  }
  return json;
}
