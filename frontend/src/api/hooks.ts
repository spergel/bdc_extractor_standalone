import { useQuery, useQueries } from '@tanstack/react-query';
import { fetchIndex, fetchPeriods, fetchPeriodSnapshot, fetchProfile, fetchFinancials } from './client-csv';
import { loadCompanyExposures, loadCompanyProfiles, loadCompanyDetail } from '../data/adapter';

export function useBDCIndex() {
  return useQuery({
    queryKey: ['bdc-index'],
    queryFn: fetchIndex,
    staleTime: 24 * 60 * 60 * 1000,
  });
}

export function useBDCPeriods(ticker: string | undefined) {
  return useQuery({
    queryKey: ['bdc-periods', ticker],
    queryFn: () => fetchPeriods(ticker!),
    enabled: !!ticker,
    staleTime: 24 * 60 * 60 * 1000,
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
