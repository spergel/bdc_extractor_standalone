/**
 * Current reference rates for calculating yields
 * 
 * These are loaded from reference_rates.json which is updated daily
 * by the update_reference_rates.py script.
 */

// Default fallback rates (used if JSON file not available)
const DEFAULT_RATES: Record<string, number> = {
  'SOFR': 4.33,
  'TERM SOFR': 4.33,
  '1M SOFR': 4.33,
  '3M SOFR': 4.33,
  '6M SOFR': 4.33,
  'LIBOR': 5.50,
  'L': 5.50,
  '1M LIBOR': 5.50,
  '3M LIBOR': 5.50,
  '6M LIBOR': 5.50,
  'PRIME': 7.50,
  'P': 7.50,
  'BASE RATE': 7.50,
  '10Y UST': 4.25,
  '5Y UST': 4.10,
  '2Y UST': 4.30,
  'FIXED': 0,
};

// In-memory cache of loaded rates
let REFERENCE_RATES: Record<string, number> = { ...DEFAULT_RATES };
let ratesLoadPromise: Promise<void> | null = null;

/**
 * Load reference rates from JSON file
 */
async function loadReferenceRates(): Promise<void> {
  try {
    const response = await fetch('/data/reference_rates.json');
    if (!response.ok) {
      console.warn('Reference rates file not found, using defaults');
      return;
    }
    
    const data = await response.json();
    if (data.rates) {
      REFERENCE_RATES = { ...DEFAULT_RATES, ...data.rates };
      console.log(`[RefRates] Loaded rates (updated: ${data.last_updated})`);
    }
  } catch (error) {
    console.warn('Failed to load reference rates, using defaults:', error);
  }
}

// Start loading rates immediately
ratesLoadPromise = loadReferenceRates();

/**
 * Ensure rates are loaded (await this before using rates)
 */
export async function ensureRatesLoaded(): Promise<void> {
  if (ratesLoadPromise) {
    await ratesLoadPromise;
    ratesLoadPromise = null;
  }
}

/**
 * Get the current rate for a reference rate string
 * Handles various formats and variations
 */
export function getReferenceRate(refRate: string | null | undefined): number | null {
  if (!refRate) return null;
  
  // Normalize the reference rate string
  const normalized = refRate.toUpperCase().trim();
  
  // Direct lookup
  if (REFERENCE_RATES[normalized] !== undefined) {
    return REFERENCE_RATES[normalized];
  }
  
  // Fuzzy matching for common variations
  for (const [key, value] of Object.entries(REFERENCE_RATES)) {
    if (normalized.includes(key)) {
      return value;
    }
  }
  
  return null;
}

/**
 * Calculate current yield from spread and reference rate
 */
export function calculateCurrentYield(
  spread: number | string | null | undefined,
  refRate: string | null | undefined
): number | null {
  if (!spread || !refRate) return null;
  
  // Parse spread percentage
  let spreadNum: number;
  if (typeof spread === 'string') {
    const cleaned = spread.replace(/%/g, '').trim();
    spreadNum = Number(cleaned);
    if (Number.isNaN(spreadNum)) return null;
  } else {
    spreadNum = Number(spread);
    if (Number.isNaN(spreadNum)) return null;
  }
  
  // Get reference rate
  const refRateNum = getReferenceRate(refRate);
  if (refRateNum === null) return null;
  
  return spreadNum + refRateNum;
}
