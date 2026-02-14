import { useMemo } from 'react';
import { useCompanyExposures, useCompanyProfiles, useCompanyDetail } from '../api/hooks';
import type { CompanyExposure, CompanyDetail } from '../data/adapter';
import { playClickSound } from '../utils/sounds';
import { formatMillionsAsCurrency, CURRENCY_M_LABEL } from '../utils/formatCurrency';

type Props = {
  companyId: string | undefined;
  onSelectBDC: (ticker: string) => void;
  onSwitchToBDC: () => void;
};

export function CompanyPage({ companyId, onSelectBDC, onSwitchToBDC }: Props) {
  const { data: profiles } = useCompanyProfiles();
  const { data: exposures } = useCompanyExposures();
  const { data: detailMap } = useCompanyDetail();

  const exposure = useMemo(() => {
    if (!companyId || !exposures?.length) return null;
    return (exposures as CompanyExposure[]).find((e) => e.company_id === companyId) ?? null;
  }, [companyId, exposures]);

  const profile = useMemo(() => {
    if (!companyId || !profiles) return undefined;
    return profiles[companyId];
  }, [companyId, profiles]);

  const detail = useMemo((): CompanyDetail | null => {
    if (!companyId || !detailMap) return null;
    return detailMap[companyId] ?? null;
  }, [companyId, detailMap]);

  if (!companyId) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#808080]">Select a company from the sidebar.</div>
      </div>
    );
  }

  if (!exposure) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#808080]">Company not found or no exposure data.</div>
      </div>
    );
  }

  const bdcsList = (exposure.bdcs_invested ?? '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
  const totalExp = Number(exposure.total_exposure_millions) ?? 0;
  const expStr = formatMillionsAsCurrency(totalExp);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto p-4 space-y-4">
      <div className="window p-4">
        <h2 className="text-base font-bold text-black mb-2">{exposure.company_name ?? companyId}</h2>
        {profile?.description && (
          <p className="text-xs text-black mb-2">{profile.description}</p>
        )}
        {!profile?.description && (
          <p className="text-xs text-[#808080] italic">No profile description yet.</p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {profile?.industry && (
            <div><span className="text-[#808080]">Industry:</span> {profile.industry}</div>
          )}
          {profile?.website && (
            <div>
              <span className="text-[#808080]">Website:</span>{' '}
              <a
                href={profile.website.startsWith('http') ? profile.website : `https://${profile.website}`}
                target="_blank"
                rel="noreferrer"
                className="text-[#0000ff] hover:underline"
              >
                {profile.website}
              </a>
            </div>
          )}
          {profile?.location && (
            <div><span className="text-[#808080]">Location:</span> {profile.location}</div>
          )}
          {profile?.employee_range && (
            <div><span className="text-[#808080]">Employees:</span> {profile.employee_range}</div>
          )}
          {profile?.leadership && (
            <div className="sm:col-span-2"><span className="text-[#808080]">Leadership:</span> {profile.leadership}</div>
          )}
          {profile?.funding && (
            <div className="sm:col-span-2"><span className="text-[#808080]">Funding:</span> {profile.funding}</div>
          )}
          {profile?.recent_news && (
            <div className="sm:col-span-2"><span className="text-[#808080]">Recent:</span> {profile.recent_news}</div>
          )}
        </div>
      </div>

      <div className="window p-4">
        <div className="text-xs font-semibold text-black mb-2">Exposure summary</div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
          <div><span className="text-[#808080]">Total exposure ($M)</span><br /><span className="font-semibold">{expStr}</span></div>
          <div><span className="text-[#808080]"># BDCs</span><br /><span className="font-semibold">{exposure.num_bdcs_invested ?? 0}</span></div>
          <div><span className="text-[#808080]">Primary industry</span><br /><span className="font-semibold">{exposure.primary_industry ?? '—'}</span></div>
          <div><span className="text-[#808080]">Avg rate</span><br /><span className="font-semibold">{exposure.avg_interest_rate != null ? `${Number(exposure.avg_interest_rate).toFixed(2)}%` : '—'}</span></div>
        </div>
      </div>

      <div className="window p-4">
        <div className="text-xs font-semibold text-black mb-2">Lenders (who the company owes)</div>
        {detail?.by_bdc && Object.keys(detail.by_bdc).length > 0 ? (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-left text-[#808080]">
                <th className="pb-1 pr-2">BDC</th>
                <th className="pb-1 text-right">Exposure ($M)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(detail.by_bdc)
                .sort(([, a], [, b]) => b - a)
                .map(([ticker, millions]) => (
                  <tr key={ticker}>
                    <td className="py-0.5 pr-2">
                      <button
                        type="button"
                        className="btn text-xs py-0.5"
                        onClick={() => {
                          playClickSound();
                          onSelectBDC(ticker);
                          onSwitchToBDC();
                        }}
                      >
                        {ticker}
                      </button>
                    </td>
                    <td className="py-0.5 text-right font-medium">{formatMillionsAsCurrency(millions)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ) : (
          <div className="flex flex-wrap gap-1">
            {bdcsList.map((ticker) => (
              <button
                key={ticker}
                type="button"
                className="btn text-xs"
                onClick={() => {
                  playClickSound();
                  onSelectBDC(ticker);
                  onSwitchToBDC();
                }}
              >
                {ticker}
              </button>
            ))}
          </div>
        )}
        <p className="text-xs text-[#808080] mt-2">Click a BDC to view its holdings.</p>
      </div>

      {detail?.by_maturity && Object.keys(detail.by_maturity).length > 0 && (
        <div className="window p-4">
          <div className="text-xs font-semibold text-black mb-2">Due date (maturity) breakdown</div>
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-left text-[#808080]">
                <th className="pb-1 pr-2">Maturity</th>
                <th className="pb-1 text-right">Exposure</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(detail.by_maturity)
                .filter(([, v]) => v > 0)
                .sort(([a], [b]) => {
                  const order = ['Before 2026', '2026', '2027', '2028', '2029', '2030+', 'No date'];
                  return order.indexOf(a) - order.indexOf(b) || a.localeCompare(b);
                })
                .map(([bucket, millions]) => (
                  <tr key={bucket}>
                    <td className="py-0.5 pr-2">{bucket}</td>
                    <td className="py-0.5 text-right font-medium">{formatMillionsAsCurrency(millions)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {detail?.by_investment_type && Object.keys(detail.by_investment_type).length > 0 && (
        <div className="window p-4">
          <div className="text-xs font-semibold text-black mb-2">Investment type breakdown</div>
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-left text-[#808080]">
                <th className="pb-1 pr-2">Type</th>
                <th className="pb-1 text-right">Exposure</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(detail.by_investment_type)
                .filter(([, v]) => v > 0)
                .sort(([, a], [, b]) => b - a)
                .map(([typeName, millions]) => (
                  <tr key={typeName}>
                    <td className="py-0.5 pr-2">{typeName}</td>
                    <td className="py-0.5 text-right font-medium">{formatMillionsAsCurrency(millions)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
