import type { Holding } from '../data/adapter';

// Helper to convert string to number safely
function toNum(s: string | undefined | null): number {
  if (!s || s === '') return 0;
  const n = Number(s);
  return Number.isNaN(n) ? 0 : n;
}

// Helper to parse percentage strings (e.g., "5.5%" -> 5.5)
function toPercent(s: string | undefined | null): number {
  if (!s || s === '') return 0;
  const cleaned = String(s).replace(/%/g, '').trim();
  const n = Number(cleaned);
  return Number.isNaN(n) ? 0 : n;
}

// Helper to get cost value (supports both cost and amortized_cost)
function getCost(h: Holding): number {
  return toNum(h.cost || h.amortized_cost);
}

// Helper to get fair value
function getFV(h: Holding): number {
  return toNum(h.fair_value);
}

// Helper to get principal
function getPrincipal(h: Holding): number {
  return toNum(h.principal_amount);
}

// Helper to check if holding has reference rate (variable rate)
function isVariableRate(h: Holding): boolean {
  return !!(h.reference_rate && h.reference_rate.trim() !== '');
}

// Helper to check if holding has PIK
function hasPIK(h: Holding): boolean {
  const pik = toPercent(h.pik_rate);
  return pik > 0;
}

// Helper to get days until maturity
function daysToMaturity(h: Holding): number | null {
  if (!h.maturity_date) return null;
  const maturity = Date.parse(h.maturity_date);
  if (Number.isNaN(maturity)) return null;
  const now = Date.now();
  const diff = maturity - now;
  return Math.floor(diff / (1000 * 60 * 60 * 24));
}

// Red flag types
export type RedFlagType = 
  | 'fv_equals_principal'
  | 'fv_below_principal'
  | 'fv_below_cost'
  | 'has_pik'
  | 'near_maturity';

export type RedFlag = {
  type: RedFlagType;
  severity: 'low' | 'medium' | 'high';
  message: string;
};

// Check for red flags on a holding
export function checkRedFlags(h: Holding, currentPeriod: string): RedFlag[] {
  const flags: RedFlag[] = [];
  const fv = getFV(h);
  const principal = getPrincipal(h);
  const cost = getCost(h);
  
  // FV ≈ Principal (within 1% and position is older than 2 quarters)
  if (principal > 0) {
    const diff = Math.abs(fv - principal);
    const pctDiff = (diff / principal) * 100;
    if (pctDiff < 1 && fv > 0) {
      // Check if position is old (would need acquisition_date comparison, simplified for now)
      flags.push({
        type: 'fv_equals_principal',
        severity: 'medium',
        message: 'Fair value approximately equals principal (potential marking issue)',
      });
    }
  }
  
  // FV below Principal (FV/Principal < 0.95)
  if (principal > 0) {
    const ratio = fv / principal;
    if (ratio < 0.95) {
      flags.push({
        type: 'fv_below_principal',
        severity: ratio < 0.90 ? 'high' : 'medium',
        message: `Fair value ${((1 - ratio) * 100).toFixed(1)}% below principal`,
      });
    }
  }
  
  // FV below Cost (FV/Cost < 0.95)
  if (cost > 0) {
    const ratio = fv / cost;
    if (ratio < 0.95) {
      flags.push({
        type: 'fv_below_cost',
        severity: ratio < 0.90 ? 'high' : 'medium',
        message: `Fair value ${((1 - ratio) * 100).toFixed(1)}% below cost`,
      });
    }
  }
  
  // Has PIK
  if (hasPIK(h)) {
    flags.push({
      type: 'has_pik',
      severity: 'low',
      message: 'Position includes PIK component',
    });
  }
  
  // Near maturity (within 12 months) with large FV
  const days = daysToMaturity(h);
  if (days !== null && days > 0 && days <= 365 && fv > 1000000) {
    flags.push({
      type: 'near_maturity',
      severity: days <= 90 ? 'high' : 'medium',
      message: `Matures in ${Math.floor(days / 30)} months (${days} days)`,
    });
  }
  
  return flags;
}

// Distribution by category
export type DistributionItem = {
  category: string;
  count: number;
  fairValue: number;
  percentage: number;
};

export function getIndustryDistribution(holdings: Holding[]): DistributionItem[] {
  const map = new Map<string, { count: number; fv: number }>();
  let totalFV = 0;
  
  holdings.forEach(h => {
    const industry = h.industry || 'Unknown';
    const fv = getFV(h);
    totalFV += fv;
    
    const existing = map.get(industry) || { count: 0, fv: 0 };
    map.set(industry, {
      count: existing.count + 1,
      fv: existing.fv + fv,
    });
  });
  
  const items: DistributionItem[] = Array.from(map.entries())
    .map(([category, data]) => ({
      category,
      count: data.count,
      fairValue: data.fv,
      percentage: totalFV > 0 ? (data.fv / totalFV) * 100 : 0,
    }))
    .sort((a, b) => b.fairValue - a.fairValue);
  
  return items;
}

export function getInvestmentTypeDistribution(holdings: Holding[]): DistributionItem[] {
  const map = new Map<string, { count: number; fv: number }>();
  let totalFV = 0;
  
  holdings.forEach(h => {
    const type = h.investment_type || 'Unknown';
    const fv = getFV(h);
    totalFV += fv;
    
    const existing = map.get(type) || { count: 0, fv: 0 };
    map.set(type, {
      count: existing.count + 1,
      fv: existing.fv + fv,
    });
  });
  
  const items: DistributionItem[] = Array.from(map.entries())
    .map(([category, data]) => ({
      category,
      count: data.count,
      fairValue: data.fv,
      percentage: totalFV > 0 ? (data.fv / totalFV) * 100 : 0,
    }))
    .sort((a, b) => b.fairValue - a.fairValue);
  
  return items;
}

// Rate structure analysis
export type RateStructure = {
  variable: { count: number; fairValue: number; percentage: number };
  fixed: { count: number; fairValue: number; percentage: number };
  total: { count: number; fairValue: number };
};

export function getRateStructure(holdings: Holding[]): RateStructure {
  let variableCount = 0;
  let variableFV = 0;
  let fixedCount = 0;
  let fixedFV = 0;
  let totalFV = 0;
  
  holdings.forEach(h => {
    const fv = getFV(h);
    totalFV += fv;
    
    if (isVariableRate(h)) {
      variableCount++;
      variableFV += fv;
    } else {
      fixedCount++;
      fixedFV += fv;
    }
  });
  
  return {
    variable: {
      count: variableCount,
      fairValue: variableFV,
      percentage: totalFV > 0 ? (variableFV / totalFV) * 100 : 0,
    },
    fixed: {
      count: fixedCount,
      fairValue: fixedFV,
      percentage: totalFV > 0 ? (fixedFV / totalFV) * 100 : 0,
    },
    total: {
      count: holdings.length,
      fairValue: totalFV,
    },
  };
}

// PIK analysis
export type PIKAnalysis = {
  pikCount: number;
  pikFairValue: number;
  nonPikCount: number;
  nonPikFairValue: number;
  averagePikRate: number;
  pikPercentage: number;
};

export function getPIKAnalysis(holdings: Holding[]): PIKAnalysis {
  let pikCount = 0;
  let pikFV = 0;
  let pikRateSum = 0;
  let nonPikCount = 0;
  let nonPikFV = 0;
  let totalFV = 0;
  
  holdings.forEach(h => {
    const fv = getFV(h);
    totalFV += fv;
    
    if (hasPIK(h)) {
      pikCount++;
      pikFV += fv;
      pikRateSum += toPercent(h.pik_rate);
    } else {
      nonPikCount++;
      nonPikFV += fv;
    }
  });
  
  return {
    pikCount,
    pikFairValue: pikFV,
    nonPikCount,
    nonPikFairValue: nonPikFV,
    averagePikRate: pikCount > 0 ? pikRateSum / pikCount : 0,
    pikPercentage: totalFV > 0 ? (pikFV / totalFV) * 100 : 0,
  };
}

// Maturity ladder
export type MaturityBucket = 
  | '0-6m'
  | '6-12m'
  | '1-2y'
  | '2-3y'
  | '3-5y'
  | '5y+'
  | 'no_maturity';

export type MaturityLadder = {
  bucket: MaturityBucket;
  count: number;
  fairValue: number;
  percentage: number;
}[];

export function getMaturityLadder(holdings: Holding[]): MaturityLadder {
  const buckets: Record<MaturityBucket, { count: number; fv: number }> = {
    '0-6m': { count: 0, fv: 0 },
    '6-12m': { count: 0, fv: 0 },
    '1-2y': { count: 0, fv: 0 },
    '2-3y': { count: 0, fv: 0 },
    '3-5y': { count: 0, fv: 0 },
    '5y+': { count: 0, fv: 0 },
    'no_maturity': { count: 0, fv: 0 },
  };
  
  let totalFV = 0;
  
  holdings.forEach(h => {
    const days = daysToMaturity(h);
    const fv = getFV(h);
    totalFV += fv;
    
    let bucket: MaturityBucket = 'no_maturity';
    if (days !== null && days > 0) {
      if (days <= 180) bucket = '0-6m';
      else if (days <= 365) bucket = '6-12m';
      else if (days <= 730) bucket = '1-2y';
      else if (days <= 1095) bucket = '2-3y';
      else if (days <= 1825) bucket = '3-5y';
      else bucket = '5y+';
    }
    
    buckets[bucket].count++;
    buckets[bucket].fv += fv;
  });
  
  return Object.entries(buckets).map(([bucket, data]) => ({
    bucket: bucket as MaturityBucket,
    count: data.count,
    fairValue: data.fv,
    percentage: totalFV > 0 ? (data.fv / totalFV) * 100 : 0,
  }));
}

// Spread statistics
export type SpreadStats = {
  average: number;
  min: number;
  max: number;
  median: number;
  count: number;
  withSpread: number;
  withoutSpread: number;
};

export function getSpreadStats(holdings: Holding[]): SpreadStats {
  const spreads: number[] = [];
  
  holdings.forEach(h => {
    const spread = toPercent(h.spread);
    if (spread > 0) {
      spreads.push(spread);
    }
  });
  
  spreads.sort((a, b) => a - b);
  
  const count = spreads.length;
  const sum = spreads.reduce((a, b) => a + b, 0);
  const avg = count > 0 ? sum / count : 0;
  const min = count > 0 ? spreads[0] : 0;
  const max = count > 0 ? spreads[count - 1] : 0;
  const median = count > 0 
    ? (count % 2 === 0 
      ? (spreads[Math.floor(count / 2) - 1] + spreads[Math.floor(count / 2)]) / 2
      : spreads[Math.floor(count / 2)])
    : 0;
  
  return {
    average: avg,
    min,
    max,
    median,
    count,
    withSpread: count,
    withoutSpread: holdings.length - count,
  };
}

// Top holdings by fair value
export type TopHolding = {
  company_name: string;
  investment_type: string;
  industry: string;
  fair_value: number;
  principal: number;
  cost: number;
  percentage: number;
};

export function getTopHoldings(holdings: Holding[], limit: number = 10): TopHolding[] {
  const totalFV = holdings.reduce((sum, h) => sum + getFV(h), 0);
  
  return holdings
    .map(h => ({
      company_name: h.company_name,
      investment_type: h.investment_type,
      industry: h.industry,
      fair_value: getFV(h),
      principal: getPrincipal(h),
      cost: getCost(h),
      percentage: totalFV > 0 ? (getFV(h) / totalFV) * 100 : 0,
    }))
    .sort((a, b) => b.fair_value - a.fair_value)
    .slice(0, limit);
}

// FV/Principal and FV/Cost ratios
export type FVRatioStats = {
  fvPrincipal: {
    average: number;
    min: number;
    max: number;
    count: number;
  };
  fvCost: {
    average: number;
    min: number;
    max: number;
    count: number;
  };
};

export function getFVRatioStats(holdings: Holding[]): FVRatioStats {
  const fvPrincipalRatios: number[] = [];
  const fvCostRatios: number[] = [];
  
  holdings.forEach(h => {
    const fv = getFV(h);
    const principal = getPrincipal(h);
    const cost = getCost(h);
    
    if (principal > 0 && fv > 0) {
      fvPrincipalRatios.push(fv / principal);
    }
    
    if (cost > 0 && fv > 0) {
      fvCostRatios.push(fv / cost);
    }
  });
  
  const calcStats = (ratios: number[]) => {
    if (ratios.length === 0) {
      return { average: 0, min: 0, max: 0, count: 0 };
    }
    ratios.sort((a, b) => a - b);
    return {
      average: ratios.reduce((a, b) => a + b, 0) / ratios.length,
      min: ratios[0],
      max: ratios[ratios.length - 1],
      count: ratios.length,
    };
  };
  
  return {
    fvPrincipal: calcStats(fvPrincipalRatios),
    fvCost: calcStats(fvCostRatios),
  };
}

// Concentration metrics (Herfindahl index)
export function getHerfindahlIndex(items: DistributionItem[]): number {
  const total = items.reduce((sum, item) => sum + item.percentage, 0);
  if (total === 0) return 0;
  
  return items.reduce((sum, item) => {
    const normalized = item.percentage / total;
    return sum + (normalized * normalized);
  }, 0) * 10000; // Scale to 0-10000 (where 10000 = perfect monopoly)
}

// Spread distribution histogram
export type SpreadBucket = {
  range: string;
  count: number;
  percentage: number;
};

export function getSpreadDistribution(holdings: Holding[]): SpreadBucket[] {
  const spreads: number[] = [];
  holdings.forEach(h => {
    const spread = toPercent(h.spread);
    if (spread > 0) spreads.push(spread);
  });
  
  if (spreads.length === 0) return [];
  
  const min = Math.min(...spreads);
  const max = Math.max(...spreads);
  const range = max - min;
  const bucketCount = 10;
  const bucketSize = range / bucketCount;
  
  const buckets: Record<number, number> = {};
  for (let i = 0; i < bucketCount; i++) {
    buckets[i] = 0;
  }
  
  spreads.forEach(spread => {
    const bucketIdx = Math.min(
      Math.floor((spread - min) / bucketSize),
      bucketCount - 1
    );
    buckets[bucketIdx] = (buckets[bucketIdx] || 0) + 1;
  });
  
  return Object.entries(buckets).map(([idx, count]) => {
    const bucketNum = Number(idx);
    const bucketMin = min + bucketNum * bucketSize;
    const bucketMax = bucketNum === bucketCount - 1 ? max : min + (bucketNum + 1) * bucketSize;
    return {
      range: `${bucketMin.toFixed(1)}-${bucketMax.toFixed(1)}%`,
      count,
      percentage: (count / spreads.length) * 100,
    };
  });
}

// Floor rate analysis
export type FloorRateAnalysis = {
  withFloor: { count: number; fairValue: number; percentage: number };
  withoutFloor: { count: number; fairValue: number; percentage: number };
  averageFloor: number;
  minFloor: number;
  maxFloor: number;
};

export function getFloorRateAnalysis(holdings: Holding[]): FloorRateAnalysis {
  let withFloorCount = 0;
  let withFloorFV = 0;
  let withoutFloorCount = 0;
  let withoutFloorFV = 0;
  let totalFV = 0;
  const floors: number[] = [];
  
  holdings.forEach(h => {
    const fv = getFV(h);
    totalFV += fv;
    const floor = toPercent(h.floor_rate);
    
    if (floor > 0) {
      withFloorCount++;
      withFloorFV += fv;
      floors.push(floor);
    } else {
      withoutFloorCount++;
      withoutFloorFV += fv;
    }
  });
  
  return {
    withFloor: {
      count: withFloorCount,
      fairValue: withFloorFV,
      percentage: totalFV > 0 ? (withFloorFV / totalFV) * 100 : 0,
    },
    withoutFloor: {
      count: withoutFloorCount,
      fairValue: withoutFloorFV,
      percentage: totalFV > 0 ? (withoutFloorFV / totalFV) * 100 : 0,
    },
    averageFloor: floors.length > 0 ? floors.reduce((a, b) => a + b, 0) / floors.length : 0,
    minFloor: floors.length > 0 ? Math.min(...floors) : 0,
    maxFloor: floors.length > 0 ? Math.max(...floors) : 0,
  };
}

// Average spread by category
export type AverageSpreadByCategory = {
  category: string;
  averageSpread: number;
  count: number;
  totalFV: number;
};

export function getAverageSpreadByIndustry(holdings: Holding[]): AverageSpreadByCategory[] {
  const map = new Map<string, { spreads: number[]; fv: number }>();
  
  holdings.forEach(h => {
    const industry = h.industry || 'Unknown';
    const spread = toPercent(h.spread);
    const fv = getFV(h);
    
    if (spread > 0) {
      const existing = map.get(industry) || { spreads: [], fv: 0 };
      existing.spreads.push(spread);
      existing.fv += fv;
      map.set(industry, existing);
    }
  });
  
  return Array.from(map.entries())
    .map(([category, data]) => ({
      category,
      averageSpread: data.spreads.reduce((a, b) => a + b, 0) / data.spreads.length,
      count: data.spreads.length,
      totalFV: data.fv,
    }))
    .sort((a, b) => b.totalFV - a.totalFV);
}

export function getAverageSpreadByInvestmentType(holdings: Holding[]): AverageSpreadByCategory[] {
  const map = new Map<string, { spreads: number[]; fv: number }>();
  
  holdings.forEach(h => {
    const type = h.investment_type || 'Unknown';
    const spread = toPercent(h.spread);
    const fv = getFV(h);
    
    if (spread > 0) {
      const existing = map.get(type) || { spreads: [], fv: 0 };
      existing.spreads.push(spread);
      existing.fv += fv;
      map.set(type, existing);
    }
  });
  
  return Array.from(map.entries())
    .map(([category, data]) => ({
      category,
      averageSpread: data.spreads.reduce((a, b) => a + b, 0) / data.spreads.length,
      count: data.spreads.length,
      totalFV: data.fv,
    }))
    .sort((a, b) => b.totalFV - a.totalFV);
}

// FV ratio distributions for histograms
export type FVRatioDistribution = {
  range: string;
  count: number;
  percentage: number;
};

export function getFVRatioDistribution(ratios: number[]): FVRatioDistribution[] {
  if (ratios.length === 0) return [];
  
  const min = Math.min(...ratios);
  const max = Math.max(...ratios);
  const range = max - min;
  const bucketCount = 12;
  const bucketSize = range / bucketCount;
  
  const buckets: Record<number, number> = {};
  for (let i = 0; i < bucketCount; i++) {
    buckets[i] = 0;
  }
  
  ratios.forEach(ratio => {
    const bucketIdx = Math.min(
      Math.floor((ratio - min) / bucketSize),
      bucketCount - 1
    );
    buckets[bucketIdx] = (buckets[bucketIdx] || 0) + 1;
  });
  
  return Object.entries(buckets).map(([idx, count]) => {
    const bucketNum = Number(idx);
    const bucketMin = min + bucketNum * bucketSize;
    const bucketMax = bucketNum === bucketCount - 1 ? max : min + (bucketNum + 1) * bucketSize;
    return {
      range: `${bucketMin.toFixed(3)}-${bucketMax.toFixed(3)}`,
      count,
      percentage: (count / ratios.length) * 100,
    };
  });
}

