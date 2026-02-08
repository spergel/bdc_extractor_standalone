import { useBDCProfile } from '../api/hooks';
import { useState, useMemo } from 'react';

type Props = {
  ticker?: string;
  name?: string;
};

function fmtNumber(n: unknown) {
  if (n === null || n === undefined) return '';
  const v = Number(n);
  if (Number.isNaN(v)) return String(n);
  return v.toLocaleString();
}

function fmtCurrency(n: unknown, currency?: string) {
  if (n === null || n === undefined) return '';
  const v = Number(n);
  if (Number.isNaN(v)) return String(n);
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD', maximumFractionDigits: 2 }).format(v);
  } catch {
    return v.toLocaleString();
  }
}

export function ProfileCard({ ticker, name }: Props) {
  const { data: profile, isLoading, error } = useBDCProfile(ticker);
  const price = profile?.price || {};
  const sd = profile?.summaryDetail || {};
  const ap = profile?.assetProfile || {};

  // All hooks must be called before any early returns
  // Normalize dividend yield: yfinance may return either 0.1049 (10.49%) or 10.49
  const dividendYieldDisplay = useMemo(() => {
    if (sd.dividendYield == null) return '';
    const raw = Number(sd.dividendYield);
    if (!Number.isFinite(raw)) return '';
    const pct = raw > 1 ? raw : raw * 100;
    return `${pct.toFixed(2)}%`;
  }, [sd.dividendYield]);

  const [expanded, setExpanded] = useState(false);

  // Early returns after all hooks
  if (error) {
    return (
      <div className="window p-4">
        <div className="text-sm text-[#ff0000]">
          ⚠️ Error loading profile for {ticker}
        </div>
        <div className="text-xs text-[#808080] mt-1">
          Profile data is unavailable. Other data may still be accessible.
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="window p-4">
        <div className="text-sm text-[#808080]">Loading profile...</div>
      </div>
    );
  }

  const change = Number(price.regularMarketChange || 0);
  const changePct = Number(price.regularMarketChangePercent || 0);
  const currency = price.currency || 'USD';

  return (
    <div className="window p-3">
      <div className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <div className="text-[#808080]">Price</div>
          <div className="text-lg">
            {fmtCurrency(price.regularMarketPrice, currency)}
            {change ? (
              <span className={change >= 0 ? 'text-[#00ff00] ml-2' : 'text-[#ff0000] ml-2'}>
                {change >= 0 ? '+' : ''}{change.toFixed(2)} ({changePct.toFixed(2)}%)
              </span>
            ) : null}
          </div>
          <div className="text-xs text-[#808080]">Prev: {fmtCurrency(price.regularMarketPreviousClose, currency)} • Open: {fmtCurrency(price.regularMarketOpen, currency)}</div>
        </div>
        <div>
          <div className="text-[#808080]">Summary</div>
          <div>Market Cap: {fmtNumber(sd.marketCap)}</div>
          <div>PE (TTM): {sd.trailingPE ?? ''}</div>
          <div>Dividend Yield: {dividendYieldDisplay}</div>
        </div>
        <div>
          <div className="text-[#808080]">Profile</div>
          <div>{ap.sector ? `Sector: ${ap.sector}` : ''}</div>
          <div>{ap.industry ? `Industry: ${ap.industry}` : ''}</div>
          {ap.website ? (
            <a className="text-[#0000ff] hover:underline" href={ap.website} target="_blank" rel="noreferrer">{ap.website}</a>
          ) : null}
        </div>
      </div>
      {ap.longBusinessSummary ? (
        <div className="mt-3 text-xs text-[#808080] leading-relaxed">
          <div className={expanded ? '' : 'max-h-24 overflow-hidden'}>
            {ap.longBusinessSummary}
          </div>
          <button
            type="button"
            className="mt-2 text-[11px] text-[#0000ff] hover:underline"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        </div>
      ) : null}
    </div>
  );
}


