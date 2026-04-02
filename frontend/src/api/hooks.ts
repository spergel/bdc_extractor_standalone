import { useQuery, useQueries } from '@tanstack/react-query';
import {
  fetchIndex,
  fetchPeriods,
  fetchPeriodSnapshot,
  fetchProfile,
  fetchFinancials,
  fetchStatementFilingDates,
} from './client-csv';
import { loadCompanyExposures, loadCompanyProfiles, loadCompanyDetail } from '../data/adapter';

export function useBDCIndex() {
  return useQuery({
    queryKey: ['bdc-index'],
    queryFn: fetchIndex,
    staleTime: 24 * 60 * 60 * 1000,
  });
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function useBDCPeriods(ticker: string | undefined) {
  return useQuery({
    queryKey: ['bdc-periods', ticker],
    queryFn: () => fetchPeriods(ticker!),
    enabled: !!ticker,
    staleTime: ONE_DAY_MS,
  });
}

/** Filing dates found in income/balance statement CSVs (often more complete than holdings periods). */
export function useBDCStatementFilingDates(ticker: string | undefined) {
  return useQuery({
    queryKey: ['bdc-statement-dates', ticker],
    queryFn: () => fetchStatementFilingDates(ticker!),
    enabled: !!ticker,
    staleTime: ONE_DAY_MS,
  });
}

export function useBDCInvestments(ticker: string | undefined, period: string | undefined) {
  return useQuery({
    queryKey: ['bdc-investments', ticker, period],
    queryFn: () => fetchPeriodSnapshot(ticker!, period!),
    enabled: !!ticker && !!period,
    staleTime: 24 * 60 * 60 * 1000,
  });
}

export function useBDCProfile(ticker: string | undefined) {
  return useQuery({
    queryKey: ['bdc-profile', ticker],
    queryFn: () => fetchProfile(ticker!),
    enabled: !!ticker,
    staleTime: 60 * 60 * 1000,
    retry: 0, // if missing, don't spam retries
  });
}

export function useBDCFinancials(ticker: string | undefined, period: string | undefined) {
  return useQuery({
    queryKey: ['bdc-financials', ticker, period],
    queryFn: () => fetchFinancials(ticker!, period!),
    enabled: !!ticker && !!period,
    staleTime: 24 * 60 * 60 * 1000,
    retry: 1,
  });
}

export function useBDCFinancialsMultiple(ticker: string | undefined, periods: string[] = []) {
  const queries = useQueries({
    queries: periods.map(period => ({
      queryKey: ['bdc-financials', ticker, period],
      queryFn: () => fetchFinancials(ticker!, period),
      enabled: !!ticker && !!period,
      staleTime: 24 * 60 * 60 * 1000,
      retry: 1,
    })),
  });
  
  return periods.map((period, idx) => ({
    period,
    data: queries[idx]?.data ?? null,
    isLoading: queries[idx]?.isLoading ?? false,
  }));
}

export function useBDCInvestmentsMultiple(ticker: string | undefined, periods: string[] = []) {
  const queries = useQueries({
    queries: periods.map(period => ({
      queryKey: ['bdc-investments', ticker, period],
      queryFn: () => fetchPeriodSnapshot(ticker!, period),
      enabled: !!ticker && !!period,
      staleTime: 24 * 60 * 60 * 1000,
      retry: 1,
    })),
  });
  
  return periods.map((period, idx) => ({
    period,
    data: queries[idx]?.data ?? null,
    isLoading: queries[idx]?.isLoading ?? false,
    error: queries[idx]?.error ?? null,
  }));
}

/** Load market/NAV profiles for every BDC at once — used by the intro page NAV table. */
export function useAllBDCProfiles(tickers: string[]) {
  const queries = useQueries({
    queries: tickers.map((ticker) => ({
      queryKey: ['bdc-profile', ticker],
      queryFn: () => fetchProfile(ticker),
      enabled: !!ticker,
      staleTime: 60 * 60 * 1000,
      retry: 0,
    })),
  });
  return tickers.map((ticker, i) => ({
    ticker,
    data: queries[i]?.data ?? null,
    isLoading: queries[i]?.isLoading ?? false,
  }));
}

/** Load latest period for every BDC at once — used by the Peer Marks page. */
export function useAllBDCsLatestInvestments(bdcs: { ticker: string; latest?: string }[]) {
  const eligible = bdcs.filter((b) => !!b.latest);
  const queries = useQueries({
    queries: eligible.map((b) => ({
      queryKey: ['bdc-investments', b.ticker, b.latest],
      queryFn: () => fetchPeriodSnapshot(b.ticker, b.latest!),
      enabled: true,
      staleTime: 24 * 60 * 60 * 1000,
    })),
  });
  return eligible.map((b, i) => ({
    ticker: b.ticker,
    period: b.latest!,
    data: queries[i]?.data ?? null,
    isLoading: queries[i]?.isLoading ?? false,
  }));
}

export function useCompanyExposures() {
  return useQuery({
    queryKey: ['company-exposures'],
    queryFn: loadCompanyExposures,
    staleTime: 24 * 60 * 60 * 1000,
  });
}

export function useCompanyProfiles() {
  return useQuery({
    queryKey: ['company-profiles'],
    queryFn: loadCompanyProfiles,
    staleTime: 24 * 60 * 60 * 1000,
  });
}

export function useCompanyDetail() {
  return useQuery({
    queryKey: ['company-detail'],
    queryFn: loadCompanyDetail,
    staleTime: 24 * 60 * 60 * 1000,
    retry: 0,
  });
}
