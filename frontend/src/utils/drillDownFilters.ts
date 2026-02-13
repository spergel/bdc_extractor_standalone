import type { Holding } from '../data/adapter';
import {
  toPercent,
  getFV,
  getCost,
  getPrincipal,
  getIndustry,
  getInvType,
  getIndustryDistribution,
  getInvestmentTypeDistribution,
} from './holdingsAnalytics';

export type DrillDownSelection =
  | { source: 'spread'; bucketIndex: number; range: string }
  | { source: 'fv-principal'; bucketIndex: number; range: string }
  | { source: 'fv-cost'; bucketIndex: number; range: string }
  | { source: 'industry'; category: string }
  | { source: 'type'; category: string }
  | null;

// Replicate spread bucketing from holdingsAnalytics.ts getSpreadDistribution
function filterBySpreadBucket(holdings: Holding[], bucketIndex: number): Holding[] {
  const spreads: number[] = [];
  holdings.forEach(h => {
    const spread = h.spread_clean ?? toPercent(h.spread);
    if (spread > 0) spreads.push(spread);
  });

  if (spreads.length === 0) return [];

  const min = Math.min(...spreads);
  const max = Math.max(...spreads);
  const range = max - min;
  const bucketCount = 10;
  const bucketSize = range / bucketCount;

  return holdings.filter(h => {
    const spread = h.spread_clean ?? toPercent(h.spread);
    if (spread <= 0) return false;
    const idx = Math.min(
      Math.floor((spread - min) / bucketSize),
      bucketCount - 1
    );
    return idx === bucketIndex;
  });
}

// Replicate FV/Principal ratio bucketing from getFVRatioDistribution
function filterByFVPrincipalBucket(holdings: Holding[], bucketIndex: number): Holding[] {
  const ratios: { holding: Holding; ratio: number }[] = [];
  holdings.forEach(h => {
    const fv = getFV(h);
    const principal = getPrincipal(h);
    if (principal > 0 && fv > 0) {
      const ratio = fv / principal;
      if (ratio >= 0.01 && ratio <= 5) {
        ratios.push({ holding: h, ratio });
      }
    }
  });

  if (ratios.length === 0) return [];

  const allRatios = ratios.map(r => r.ratio);
  const min = Math.min(...allRatios);
  const max = Math.max(...allRatios);
  const range = max - min;
  const bucketCount = 12;
  const bucketSize = range / bucketCount;

  return ratios
    .filter(({ ratio }) => {
      const idx = Math.min(
        Math.floor((ratio - min) / bucketSize),
        bucketCount - 1
      );
      return idx === bucketIndex;
    })
    .map(r => r.holding);
}

// Replicate FV/Cost ratio bucketing from getFVRatioDistribution
function filterByFVCostBucket(holdings: Holding[], bucketIndex: number): Holding[] {
  const ratios: { holding: Holding; ratio: number }[] = [];
  holdings.forEach(h => {
    const fv = getFV(h);
    const cost = getCost(h);
    if (cost > 0 && fv > 0) {
      const ratio = fv / cost;
      if (ratio >= 0.01 && ratio <= 5) {
        ratios.push({ holding: h, ratio });
      }
    }
  });

  if (ratios.length === 0) return [];

  const allRatios = ratios.map(r => r.ratio);
  const min = Math.min(...allRatios);
  const max = Math.max(...allRatios);
  const range = max - min;
  const bucketCount = 12;
  const bucketSize = range / bucketCount;

  return ratios
    .filter(({ ratio }) => {
      const idx = Math.min(
        Math.floor((ratio - min) / bucketSize),
        bucketCount - 1
      );
      return idx === bucketIndex;
    })
    .map(r => r.holding);
}

// Filter by industry category, handling "Other" as slice(10) remainder
function filterByIndustry(holdings: Holding[], category: string): Holding[] {
  if (category === 'Other') {
    const dist = getIndustryDistribution(holdings);
    const topCategories = new Set(dist.slice(0, 10).map(d => d.category));
    return holdings.filter(h => !topCategories.has(getIndustry(h)));
  }
  return holdings.filter(h => getIndustry(h) === category);
}

// Filter by investment type category, handling "Other" as slice(10) remainder
function filterByType(holdings: Holding[], category: string): Holding[] {
  if (category === 'Other') {
    const dist = getInvestmentTypeDistribution(holdings);
    const topCategories = new Set(dist.slice(0, 10).map(d => d.category));
    return holdings.filter(h => !topCategories.has(getInvType(h)));
  }
  return holdings.filter(h => getInvType(h) === category);
}

// Master dispatcher
export function getFilteredHoldings(holdings: Holding[], selection: DrillDownSelection): Holding[] {
  if (!selection) return holdings;

  switch (selection.source) {
    case 'spread':
      return filterBySpreadBucket(holdings, selection.bucketIndex);
    case 'fv-principal':
      return filterByFVPrincipalBucket(holdings, selection.bucketIndex);
    case 'fv-cost':
      return filterByFVCostBucket(holdings, selection.bucketIndex);
    case 'industry':
      return filterByIndustry(holdings, selection.category);
    case 'type':
      return filterByType(holdings, selection.category);
    default:
      return holdings;
  }
}
