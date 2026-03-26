import { useMemo } from 'react';
import { useBDCFinancialsMultiple } from '../api/hooks';

type Props = {
  ticker?: string;
  periods?: string[]; // Support multiple periods
  name?: string;
  mode?: 'overview' | 'historical'; // 'overview' shows key metrics + latest statements, 'historical' shows all line items across periods
};

function fmt(n: unknown) {
  if (n == null) return '';
  const v = Number(n);
  if (Number.isNaN(v)) return '';
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatLabel(label: string): string {
  if (!label) return '';
  
  // If it already has proper formatting (spaces, capitalization), use it
  if (label.includes(' ') && label !== label.toLowerCase()) {
    return label;
  }
  
  // Convert camelCase/PascalCase/snake_case to Title Case
  let formatted = label
    // Split on camelCase boundaries (handle multiple consecutive capitals)
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    // Split on underscores
    .replace(/_/g, ' ')
    // Split on numbers
    .replace(/([a-z])(\d)/g, '$1 $2')
    .replace(/(\d)([a-z])/g, '$1 $2')
    // Capitalize first letter of each word
    .split(' ')
    .map(word => {
      // Handle common abbreviations
      if (word.toLowerCase() === 'pik') return 'PIK';
      if (word.toLowerCase() === 'nav') return 'NAV';
      if (word.toLowerCase() === 'eps') return 'EPS';
      if (word.toLowerCase() === 'nii') return 'NII';
      if (word.toLowerCase() === 'fx') return 'FX';
      if (word.toLowerCase() === 'sbc') return 'SBC';
      // Capitalize first letter
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(' ')
    .trim();
  
  // Fix common patterns
  formatted = formatted
    .replace(/\bAnd\b/gi, 'and')
    .replace(/\bOf\b/gi, 'of')
    .replace(/\bIn\b/gi, 'in')
    .replace(/\bTo\b/gi, 'to')
    .replace(/\bFor\b/gi, 'for')
    .replace(/\bThe\b/gi, 'the')
    .replace(/\bAt\b/gi, 'at')
    .replace(/\bOn\b/gi, 'on')
    .replace(/\bBy\b/gi, 'by')
    // But capitalize first word
    .replace(/^[a-z]/, (match) => match.toUpperCase());
  
  return formatted;
}

function truncateLabel(label: string, maxLen = 45): string {
  if (!label) return '';
  if (label.length <= maxLen) return label;
  return `${label.slice(0, maxLen - 1)}...`;
}

function toNumber(value: unknown): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
}

function TinyLineChart({
  title,
  points,
  formatter = fmt,
}: {
  title: string;
  points: Array<{ period: string; value: number | null }>;
  formatter?: (n: unknown) => string;
}) {
  const validPoints = points.filter((p) => p.value != null) as Array<{ period: string; value: number }>;
  if (validPoints.length < 2) return null;

  const width = 260;
  const height = 80;
  const minV = Math.min(...validPoints.map((p) => p.value));
  const maxV = Math.max(...validPoints.map((p) => p.value));
  const span = Math.max(1e-9, maxV - minV);
  const xFor = (idx: number) => (idx / (validPoints.length - 1)) * (width - 10) + 5;
  const yFor = (v: number) => height - ((v - minV) / span) * (height - 16) - 8;
  const path = validPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(p.value)}`).join(' ');

  return (
    <div className="window p-3">
      <div className="text-xs font-semibold mb-2 text-black">{title}</div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-24">
        <path d={path} fill="none" stroke="#0000ff" strokeWidth="2" />
        {validPoints.map((p, i) => (
          <circle key={`${p.period}-${i}`} cx={xFor(i)} cy={yFor(p.value)} r="2.5" fill="#000080" />
        ))}
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-[#808080]">
        <span>{validPoints[0].period}</span>
        <span>{formatter(validPoints[0].value)}</span>
        <span>{validPoints[validPoints.length - 1].period}</span>
        <span>{formatter(validPoints[validPoints.length - 1].value)}</span>
      </div>
    </div>
  );
}

// Map raw statement labels/keys to human-friendly display names
const DISPLAY_NAME_OVERRIDES: Record<string, string> = {
  // Income Statement
  'netincomeloss': 'Net Income',
  'net income loss': 'Net Income',
  'earningspersharebasic': 'EPS (Basic)',
  'earnings per share basic': 'EPS (Basic)',
  'earningspersharediluted': 'EPS (Diluted)',
  'earnings per share diluted': 'EPS (Diluted)',
  'operatingexpenses': 'Operating Expenses',
  'operating expenses': 'Operating Expenses',
  'interestanddividendincomeoperatingpaidincash': 'Interest & Dividend Income (Cash)',
  'interest income operating paid in cash': 'Interest & Dividend Income (Cash)',
  'interestincomeoperatingpaidinkind': 'Interest Income (PIK)',
  'interest income operating paid in kind': 'Interest Income (PIK)',
  'interestanddividendincomeoperating': 'Interest & Dividend Income',
  'interest and dividend income operating': 'Interest & Dividend Income',
  'feeincome': 'Fee Income',
  'fee income': 'Fee Income',
  'grossinvestmentincomeoperating': 'Gross Investment Income',
  'gross investment income operating': 'Gross Investment Income',
  'interestexpensedebt': 'Interest Expense (Debt)',
  'debt related commitment fees and debt issuance costs': 'Debt Fees & Issuance Costs',
  'debtrelatedcommitmentfeesanddebtissuancecosts': 'Debt Fees & Issuance Costs',
  'generalandadministrativeexpense': 'General & Administrative Expense',
  'investmentincomeoperatingtaxexpensebenefit': 'Investment Income Tax (Benefit)',
  'laborandrelatedexpense': 'Labor & Related Expense',
  'employeebenefitsandsharebasedcompensation': 'Employee Benefits & SBC',
  'htgc employeecompensation': 'Employee Compensation',
  'htgc expensesallocatedtoadvisersubsidiary1': 'Expenses Allocated to Adviser Subsidiary',
  'htgc operatingexpensesnet': 'Operating Expenses, Net',
  'netinvestmentincome': 'Net Investment Income',
  'net investment income': 'Net Investment Income',
  'realizedinvestmentgainslosses': 'Realized Investment Gains/Losses',
  'gainslossesonextinguishmentofdebt': 'Gain/Loss on Extinguishment of Debt',
  'othergeneralandadministrativeexpense': 'Other General & Administrative Expense',
  'other general and administrative expense': 'Other General & Administrative Expense',
  'otherincome': 'Other Income',
  'other income': 'Other Income',
  'realizedandunrealizedgainlossinvestmentderivativeandforeigncurrencytransactionpricechangeoperatingaftertax': 'Realized & Unrealized Gain/Loss (After Tax)',
  'realizedgainlossinvestmentandderivativeoperatingtaxexpensebenefit': 'Realized Gain/Loss Tax Expense (Benefit)',
  'realizedgainlossinvestmentderivativeandforeigncurrencytransactionpricechangeoperatingaftertax': 'Realized Gain/Loss (After Tax)',
  'unrealizedgainlossinvestmentandderivativeoperatingtaxexpensebenefit': 'Unrealized Gain/Loss Tax Expense (Benefit)',
  'unrealizedgainlossinvestmentderivativeandforeigncurrencytransactionpricechangeoperatingaftertax': 'Unrealized Gain/Loss (After Tax)',
  'arcc dividendincomeoperatingexcludingpaidinkind': 'Dividend Income (Excl. PIK)',
  'arcc incentivefeeexpensecapitalgainbasedincludingreductionincapitalgains': 'Incentive Fee Expense (Capital Gains)',
  'arcc incentivefeeexpensereversal': 'Incentive Fee Expense Reversal',
  'arcc incomebasedfees': 'Income Based Fees',
  'administrativefeesexpense': 'Administrative Fees Expense',
  'administrative fees expense': 'Administrative Fees Expense',
  'debtandequitysecuritiesrealizedgainloss': 'Debt and Equity Securities Realized Gain/Loss',
  'debt and equity securities realized gain loss': 'Debt and Equity Securities Realized Gain/Loss',
  'debtandequitysecuritiesunrealizedgainloss': 'Debt and Equity Securities Unrealized Gain/Loss',
  'debt and equity securities unrealized gain loss': 'Debt and Equity Securities Unrealized Gain/Loss',
  'dividendincomeoperating': 'Dividend Income Operating',
  'dividend income operating': 'Dividend Income Operating',
  'dividendincomeoperatingpaidinkind': 'Dividend Income Operating (PIK)',
  'dividend income operating paid in kind': 'Dividend Income Operating (PIK)',
  'interestexpense': 'Interest Expense',
  'interest expense': 'Interest Expense',
  'interestincomeoperatingpaidincash': 'Interest Income Operating (Cash)',
  'investmentincomeinvestmentexpense': 'Investment Income Investment Expense',
  'investment income investment expense': 'Investment Income Investment Expense',
  'investmentincomeoperatingafterexpenseandtax': 'Investment Income Operating (After Expense and Tax)',
  'investment income operating after expense and tax': 'Investment Income Operating (After Expense and Tax)',
  'managementfeeexpense': 'Management Fee Expense',
  'management fee expense': 'Management Fee Expense',
  'foreigncurrencytransactiongainlossrealized': 'Foreign Currency Transaction Gain/Loss (Realized)',
  'foreign currency transaction gain loss realized': 'Foreign Currency Transaction Gain/Loss (Realized)',
  'foreigncurrencytransactiongainlossunrealized': 'Foreign Currency Transaction Gain/Loss (Unrealized)',
  'foreign currency transaction gain loss unrealized': 'Foreign Currency Transaction Gain/Loss (Unrealized)',
  'htgc realizedinvestmentgainslossandgainlossonextinguishmentofdebt': 'Realized Gains/Losses and Debt Extinguishment',
  'unrealizedgainlossoninvestments': 'Unrealized Gain/Loss on Investments',
  'investmentcompanyrealizedandunrealizedgainlossoninvestmentandforeigncurrency': 'Realized/Unrealized Gain/Loss incl. FX',
  'investmentcompanyinvestmentincomelosspershare': 'Investment Income/Loss per Share',
  'weightedaveragenumberofsharesoutstandingbasic': 'Weighted Avg Shares (Basic)',
  'weightedaveragenumberofdilutedsharesoutstanding': 'Weighted Avg Shares (Diluted)',
  'investmentcompanydistributiontoshareholderspershare': 'Distribution to Shareholders per Share',

  // Cash Flow Statement
  'cashcashequivalentsrestrictedcashandrestrictedcashequivalents': 'Cash & Cash Equivalents (incl. Restricted)',
  'cash flows from financing activities: (aggregated)': 'Cash Flows from Financing (Aggregated)',
  'netcashprovidedbyusedinfinancingactivities': 'Net Cash Provided by (Used in) Financing',
  'paymentsforrepurchaseofcommonstock': 'Repurchase of Common Stock',
  'cash flows from investing activities: (aggregated)': 'Cash Flows from Investing (Aggregated)',
  'netcashprovidedbyusedininvestingactivities': 'Net Cash Provided by (Used in) Investing',
  'payments to acquire businesses (aggregated)': 'Payments to Acquire Businesses',
  'cash flows from operating activities: (aggregated)': 'Cash Flows from Operating (Aggregated)',
  'netcashprovidedbyusedinoperatingactivities': 'Net Cash Provided by (Used in) Operating',
  'adjustments to reconcile net income to net cash provided by operating activities: (aggregated)': 'Adjustments to Reconcile Net Income',
  'sharebasedcompensation': 'Share-based Compensation',
  'cashcashequivalentsrestrictedcashandrestrictedcashequivalentsperiodincreasedecreaseincludingexchangerateeffect': 'Change in Cash & Equivalents (incl. FX)',
  'paymentsforpurchaseofinvestmentoperatingactivity': 'Payments for Purchase of Investments (Operating)',
  'htgc fundingassignedtoadviserfunds': 'Funding Assigned to Adviser Funds',
  'proceedsfromdispositionofinvestmentoperatingactivity': 'Proceeds from Disposition of Investments (Operating)',
  'htgc proceedsfromthesaleofdebtinvestments': 'Proceeds from Sale of Debt Investments',
  'htgc proceedsfromdispositionofinvestmentequityandwarrantsinvestmentsoperatingactivity': 'Proceeds from Sale of Equity & Warrants (Operating)',
  'unrealizedgainlossinvestmentderivativeandforeigncurrencytransactionpricechangeoperatingbeforetax': 'Unrealized Gain/Loss (Investments/Derivatives/FX) Before Tax',
  'realizedgainlossinvestmentandderivativeoperatingaftertax': 'Realized Gain/Loss (Investments/Derivatives) After Tax',
  'htgc paymentforderivativeinstrumentoperatingactivities': 'Payments for Derivative Instruments (Operating)',
  'accretionamortizationofdiscountsandpremiumsinvestments': 'Accretion/Amortization of Discounts and Premiums',
  'htgc accretionamortizationofdiscountsandpremiumsinvestmentsconvertiblenotes': 'Accretion/Amortization on Convertible Notes',
  'htgc accretionofloanexitfees': 'Accretion of Loan Exit Fees',
  'htgc changeinloanincomenetofcollections': 'Change in Loan Income (Net of Collections)',
  'htgc unearnedfeesrelatedtounfundedcommitments': 'Unearned Fees related to Unfunded Commitments',
  'amortizationoffinancingcostsanddiscounts': 'Amortization of Financing Costs and Discounts',
  'amortizationofdebtdiscountpremium': 'Amortization of Debt Discount/Premium',
  'amortization of debt discount premium': 'Amortization of Debt Discount/Premium',
  'amortizationoffinancingcosts': 'Amortization of Financing Costs',
  'amortization of financing costs': 'Amortization of Financing Costs',
  'dividendspaidinkind': 'Dividends Paid in Kind',
  'dividends paid in kind': 'Dividends Paid in Kind',
  'dividendspayablecurrentandnoncurrent': 'Dividends Payable (Current and Noncurrent)',
  'dividends payable current and noncurrent': 'Dividends Payable (Current and Noncurrent)',
  'paidinkindinterest': 'Paid in Kind Interest',
  'paid in kind interest': 'Paid in Kind Interest',
  'proceedsfromissuanceoflongtermdebt': 'Proceeds from Issuance of Long-Term Debt',
  'proceeds from issuance of long term debt': 'Proceeds from Issuance of Long-Term Debt',
  'repaymentsofconvertibledebt': 'Repayments of Convertible Debt',
  'repayments of convertible debt': 'Repayments of Convertible Debt',
  'repaymentsofseniordebt': 'Repayments of Senior Debt',
  'repayments of senior debt': 'Repayments of Senior Debt',
  'unrealizedgainlossonderivatives': 'Unrealized Gain/Loss on Derivatives',
  'unrealized gain loss on derivatives': 'Unrealized Gain/Loss on Derivatives',
  'increasedecreaseinaccountspayableandotheroperatingliabilities': 'Increase/(Decrease) in Accounts Payable and Other Operating Liabilities',
  'increase decrease in accounts payable and other operating liabilities': 'Increase/(Decrease) in Accounts Payable and Other Operating Liabilities',
  'increasedecreaseinincentivefeepayable': 'Increase/(Decrease) in Incentive Fee Payable',
  'increase decrease in incentive fee payable': 'Increase/(Decrease) in Incentive Fee Payable',
  'increasedecreaseinmanagementfeepayable': 'Increase/(Decrease) in Management Fee Payable',
  'increase decrease in management fee payable': 'Increase/(Decrease) in Management Fee Payable',
  'increasedecreaseinsecuritiesloanedtransactions': 'Increase/(Decrease) in Securities Loaned Transactions',
  'increase decrease in securities loaned transactions': 'Increase/(Decrease) in Securities Loaned Transactions',
  'arcc increasedecreaseinincomebasedfeespayable': 'Increase/(Decrease) in Income Based Fees Payable',
  'arcc increase decrease in income based fees payable': 'Increase/(Decrease) in Income Based Fees Payable',
  'arcc increasedecreaseininterestandfacilityfeespayable': 'Increase/(Decrease) in Interest and Facility Fees Payable',
  'arcc increase decrease in interest and facility fees payable': 'Increase/(Decrease) in Interest and Facility Fees Payable',
  'arcc increasedecreaseininterestrateswapcollateralpayable': 'Increase/(Decrease) in Interest Rate Swap Collateral Payable',
  'arcc increase decrease in interest rate swap collateral payable': 'Increase/(Decrease) in Interest Rate Swap Collateral Payable',
  'arcc increasedecreaseinpayablestoparticipants': 'Increase/(Decrease) in Payables to Participants',
  'arcc increase decrease in payables to participants': 'Increase/(Decrease) in Payables to Participants',
  'depreciationandamortization': 'Depreciation and Amortization',
  
  // Balance Sheet
  'assets': 'Assets',
  'cashandcashequivalentsatcarryingvalue': 'Cash and Cash Equivalents',
  'liabilitiesandstockholdersequity': 'Liabilities and Stockholders\' Equity',
  'liabilities': 'Liabilities',
  'totalcurrentliabilitiesaggregated': 'Total Current Liabilities',
  'retainedearningsaccumulateddeficit': 'Retained Earnings (Accumulated Deficit)',
  'stockholdersequity': 'Stockholders\' Equity',
  'commonstockvalue': 'Common Stock Value',
  'additionalpaidincapital': 'Additional Paid-in Capital',
  'investmentownedatfairvalue': 'Investments at Fair Value',
  'cashheldinforeigncurrency': 'Cash Held in Foreign Currency',
  'restrictedcash': 'Restricted Cash',
  'interestreceivable': 'Interest Receivable',
  'operatingleaserightofuseasset': 'Operating Lease Right-of-Use Asset',
  'otherassets': 'Other Assets',
  'longtermdebt': 'Long-Term Debt',
  'accountspayableandaccruedliabilitiescurrentandnoncurrent': 'Accounts Payable and Accrued Liabilities',
  'accountspayableandotheraccruedliabilities': 'Accounts Payable and Other Accrued Liabilities',
  'accounts payable and other accrued liabilities': 'Accounts Payable and Other Accrued Liabilities',
  'additionalpaidincapitalcommonstock': 'Additional Paid-in Capital (Common Stock)',
  'additional paid in capital common stock': 'Additional Paid-in Capital (Common Stock)',
  'commitmentsandcontingencies': 'Commitments and Contingencies',
  'commitments and contingencies': 'Commitments and Contingencies',
  'deferredincometaxliabilitiesnet': 'Deferred Income Tax Liabilities (Net)',
  'deferred income tax liabilities net': 'Deferred Income Tax Liabilities (Net)',
  'derivativeliabilities': 'Derivative Liabilities',
  'derivative liabilities': 'Derivative Liabilities',
  'incentivefeepayable': 'Incentive Fee Payable',
  'incentive fee payable': 'Incentive Fee Payable',
  'incentivefeepayablecapitalgainbased': 'Incentive Fee Payable (Capital Gain Based)',
  'incentive fee payable capital gain based': 'Incentive Fee Payable (Capital Gain Based)',
  'payableinvestmentpurchase': 'Payable Investment Purchase',
  'payable investment purchase': 'Payable Investment Purchase',
  'receivableinvestmentsale': 'Receivable Investment Sale',
  'receivable investment sale': 'Receivable Investment Sale',
  'securitiesloaned': 'Securities Loaned',
  'securities loaned': 'Securities Loaned',
  'arcc incomebasedfeespayable': 'Income Based Fees Payable',
  'arcc income based fees payable': 'Income Based Fees Payable',
  'arcc interestandfacilityfeespayable': 'Interest and Facility Fees Payable',
  'arcc interest and facility fees payable': 'Interest and Facility Fees Payable',
  'arcc payabletoparticipants': 'Payable to Participants',
  'arcc payable to participants': 'Payable to Participants',
  'operatingleaseliability': 'Operating Lease Liability',
  'commonstocksharesoutstanding': 'Common Stock Shares Outstanding',
  'netassetvaluepershare': 'Net Asset Value per Share',
  'totalassets': 'Total Assets',
  'totalliabilities': 'Total Liabilities',
  'totalequity': 'Total Equity',
  'totalinvestments': 'Total Investments',
  'cashandcashequivalents': 'Cash and Cash Equivalents',
  
  // Cash Flow additional items
  'increasedecreaseinaccruedinterestreceivablenet': 'Increase/(Decrease) in Accrued Interest Receivable, Net',
  'increasedecreaseinotheroperatingassets': 'Increase/(Decrease) in Other Operating Assets',
  'increasedecreaseinaccruedliabilities': 'Increase/(Decrease) in Accrued Liabilities',
  'paymentstoacquireproductiveassets': 'Payments to Acquire Productive Assets',
  'proceedsfromissuanceofcommonstock': 'Proceeds from Issuance of Common Stock',
  'paymentsofstockissuancecosts': 'Payments of Stock Issuance Costs',
  'paymentsofdividends': 'Payments of Dividends',
  'proceedsfromissuanceofdebt': 'Proceeds from Issuance of Debt',
  'repaymentsofdebt': 'Repayments of Debt',
  'paymentsofdebtissuancecosts': 'Payments of Debt Issuance Costs',
  'htgc paymentoffeesforcreditfacilitiesanddebentures': 'Payments of Fees for Credit Facilities and Debentures',
  'interestpaidnet': 'Interest Paid, Net',
  'incometaxespaidnet': 'Income Taxes Paid, Net',
  'htgc distributionsreinvested': 'Distributions Reinvested',
  'netincome': 'Net Income',
};

function normalizeKey(s?: string): string {
  if (!s) return '';
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function resolveDisplayLabel(k: string, rawLabel?: string): string {
  const fromLabel = DISPLAY_NAME_OVERRIDES[normalizeKey(rawLabel)];
  if (fromLabel) return fromLabel;
  const fromKey = DISPLAY_NAME_OVERRIDES[normalizeKey(k)];
  if (fromKey) return fromKey;
  return formatLabel(rawLabel || k);
}

export function FinancialsPanel({ ticker, periods = [], name: _name, mode = 'overview' }: Props) {
  // Fetch financials for all periods
  const financialsData = useBDCFinancialsMultiple(ticker, periods);
  

  // Use only periods that successfully loaded data to avoid empty columns
  const availableData = useMemo(() => financialsData.filter(({ data }) => !!data), [financialsData]);

  const rows = useMemo(() => {
    const metricKeys = [
      { key: 'total_investment_income', label: 'Total Investment Income', source: 'income_statement' },
      { key: 'net_investment_income', label: 'Net Investment Income', source: 'income_statement' },
      { key: 'net_investment_income_per_share', label: 'NII / Share', source: 'income_statement', fallback: 'derived' },
      { key: 'total_expenses', label: 'Total Expenses', source: 'income_statement' },
      { key: 'nav_per_share', label: 'NAV / Share', source: 'balance_sheet' },
      { key: 'total_assets', label: 'Total Assets', source: 'balance_sheet' },
      { key: 'total_liabilities', label: 'Total Liabilities', source: 'balance_sheet' },
      { key: 'shares_outstanding', label: 'Shares Outstanding', source: 'shares' },
      { key: 'debt_to_equity', label: 'Debt / Equity', source: 'derived' },
      { key: 'asset_coverage', label: 'Asset Coverage', source: 'derived' },
    ];

    return metricKeys.map(metric => {
      const values = availableData.map(({ period, data }) => {
        if (!data) return { period, value: null };
        const source = data[metric.source as keyof typeof data] as Record<string, unknown> | undefined;
        const fallback = metric.fallback ? data[metric.fallback as keyof typeof data] as Record<string, unknown> | undefined : undefined;
        const value = source?.[metric.key] ?? fallback?.[metric.key] ?? null;
        return { period, value };
      });
      return { label: metric.label, values };
    });
  }, [availableData]);

  const hasData = availableData.length > 0;

  const trendSeries = useMemo(() => {
    const getConceptValue = (data: any, keys: string[]) => {
      for (const key of keys) {
        const fromBalance = toNumber(data?.balance_sheet?.[key]);
        if (fromBalance != null) return fromBalance;
        const fromIncome = toNumber(data?.income_statement?.[key]);
        if (fromIncome != null) return fromIncome;
      }
      return null;
    };

    return availableData.map(({ period, data }) => {
      const totalAssets = getConceptValue(data, ['Assets', 'assets']);
      const totalLiabilities = getConceptValue(data, ['Liabilities', 'liabilities']);
      const netIncome = getConceptValue(data, ['NetIncomeLoss', 'netincomeloss']);
      const leverage = totalAssets && totalLiabilities ? totalLiabilities / totalAssets : null;
      return { period, totalAssets, totalLiabilities, netIncome, leverage };
    });
  }, [availableData]);

  // Pick the latest period's full statements (first item in list is latest)
  const latestFull = useMemo(() => {
    if (!availableData.length) return null;
    const latest = availableData[0]?.data as any;
    if (!latest) return null;
    return {
      income: latest.full_income_statement as Record<string, { label: string; value: number | null }> | undefined,
      cashflow: latest.full_cash_flow_statement as Record<string, { label: string; value: number | null }> | undefined,
      balance: latest.full_balance_sheet as Record<string, { label: string; value: number | null }> | undefined,
    };
  }, [availableData]);


  const renderStatement = (title: string, stmt?: Record<string, { label: string; value: number | null }>) => {
    if (!stmt || Object.keys(stmt).length === 0) return null;
    const entries = Object.entries(stmt)
      .map(([k, v]) => ({
        key: k,
        label: resolveDisplayLabel(k, v.label),
        value: v.value,
      }))
      .filter((e) => e.value !== null)
      .slice(0, 50); // Show more items
    if (entries.length === 0) return null;
    return (
      <div className="mt-4">
        <div className="text-sm tracking-wide mb-2 text-[#808080] font-medium">{title} (latest period)</div>
        <div className="window overflow-auto">
          <table className="w-full text-sm border-separate border-spacing-0">
            <thead className="sticky top-0 bg-[#c0c0c0] z-10">
              <tr className="border-b border-[#808080]">
                <th className="text-left py-2 px-3 text-[#808080] font-semibold">Line Item</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Value</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.key} className="border-b border-[#c0c0c0] hover:bg-[#c0c0c0]">
                  <td className="py-1.5 px-3 text-black max-w-[360px]">
                    <span className="block truncate whitespace-nowrap" title={e.label}>
                      {truncateLabel(e.label, 45)}
                    </span>
                  </td>
                  <td className="py-1.5 px-3 text-right font-mono text-black">{fmt(e.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  if (mode === 'historical') {
    if (!hasData) {
      return (
        <div className="window p-4">
          <div className="text-sm text-[#808080]">Loading financials...</div>
        </div>
      );
    }
    
    // Get all unique keys from all periods, filtering out empty strings
    const getAllKeys = (key: 'full_income_statement' | 'full_balance_sheet' | 'full_cash_flow_statement') => {
      const allKeys = new Set<string>();
      availableData.forEach(({ data }) => {
        if (data && (data as any)[key]) {
          const stmt = (data as any)[key];
          if (stmt && typeof stmt === 'object' && !Array.isArray(stmt)) {
            // Try multiple methods to get keys
            const keys = Object.getOwnPropertyNames(stmt);
            for (const k of keys) {
              if (k && k.trim() && k !== '__proto__') { // Only add non-empty, non-whitespace keys
                allKeys.add(k);
              }
            }
            // Also try for...in as a fallback
            for (const k in stmt) {
              if (k && k.trim() && k !== '__proto__') {
                allKeys.add(k);
              }
            }
          }
        }
      });
      return Array.from(allKeys).sort();
    };
    
    const incomeKeys = getAllKeys('full_income_statement');
    const balanceKeys = getAllKeys('full_balance_sheet');
    const cashflowKeys = getAllKeys('full_cash_flow_statement');
    
    const renderTable = (title: string, keys: string[], dataKey: 'full_income_statement' | 'full_balance_sheet' | 'full_cash_flow_statement') => {
      if (keys.length === 0) return null;

      return (
        <div className="mt-4">
          <div className="titlebar">
            <div className="text-sm tracking-wide">{title}</div>
          </div>
          <table className="w-full text-sm table-excel">
            <thead className="sticky top-0 z-10">
              <tr>
                <th className="text-left py-2 px-3 text-black text-xs sticky left-0 bg-[#c0c0c0] border border-[#c0c0c0] w-[320px] min-w-[320px] max-w-[320px]">Line Item</th>
                {availableData.map(({ period }) => {
                  // Format period date nicely (e.g., "2025-10-23" -> "Q3 2025" or "Oct 2025")
                  let periodLabel = period;
                  try {
                    const date = new Date(period);
                    if (!isNaN(date.getTime())) {
                      const month = date.getMonth() + 1;
                      const year = date.getFullYear();
                      // Determine quarter
                      const quarter = Math.floor((month - 1) / 3) + 1;
                      periodLabel = `Q${quarter} ${year}`;
                    }
                  } catch {
                    // Keep original if parsing fails
                  }
                  return (
                    <th key={period} className="text-right py-2 px-3 text-black text-xs min-w-[120px] border border-[#c0c0c0] bg-[#c0c0c0]">
                      {periodLabel}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                // Get label from first period that has this key
                let label = key;
                for (const { data } of availableData) {
                  const stmt = (data as any)?.[dataKey];
                  const entry = stmt?.[key];
                  if (entry?.label) {
                    label = entry.label;
                    break;
                  }
                }
                
                // Use resolveDisplayLabel to get proper formatting with overrides
                const displayLabel = resolveDisplayLabel(key, label);
                
                return (
                  <tr key={key} className="hover:bg-[#c0c0c0]">
                    <td className="py-1.5 px-3 text-black text-xs sticky left-0 bg-[#c0c0c0] border border-[#c0c0c0] w-[320px] min-w-[320px] max-w-[320px]">
                      <span className="block truncate whitespace-nowrap" title={displayLabel}>
                        {truncateLabel(displayLabel, 45)}
                      </span>
                    </td>
                    {availableData.map(({ period, data }) => {
                      const stmt = (data as any)?.[dataKey];
                      const entry = stmt?.[key];
                      const value = entry?.value;
                      return (
                        <td key={period} className="text-right py-1.5 px-3 font-mono text-black border border-[#c0c0c0]">
                          {value != null ? fmt(value) : ''}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
    };

    return (
      <div className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TinyLineChart
            title="Total Assets Trend"
            points={trendSeries.map((p) => ({ period: p.period, value: p.totalAssets }))}
          />
          <TinyLineChart
            title="Total Liabilities Trend"
            points={trendSeries.map((p) => ({ period: p.period, value: p.totalLiabilities }))}
          />
          <TinyLineChart
            title="Net Income Trend"
            points={trendSeries.map((p) => ({ period: p.period, value: p.netIncome }))}
          />
          <TinyLineChart
            title="Leverage (Liabilities / Assets)"
            points={trendSeries.map((p) => ({ period: p.period, value: p.leverage }))}
            formatter={(n) => {
              const v = Number(n);
              return Number.isNaN(v) ? '' : `${(v * 100).toFixed(1)}%`;
            }}
          />
        </div>
        {renderTable('Income Statement', incomeKeys, 'full_income_statement')}
        {renderTable('Balance Sheet', balanceKeys, 'full_balance_sheet')}
        {renderTable('Cash Flow Statement', cashflowKeys, 'full_cash_flow_statement')}
        {incomeKeys.length === 0 && balanceKeys.length === 0 && cashflowKeys.length === 0 && (
          <div className="window p-4">
            <div className="text-sm text-[#808080]">No financial data found</div>
          </div>
        )}
      </div>
    );
  }

  // Overview mode: show key metrics + latest statements
  return (
    <div className="space-y-4">
      {hasData ? (
        <>
          <div className="window overflow-auto">
            <div className="titlebar">
              <div className="text-sm tracking-wide">Key Metrics</div>
            </div>
            <table className="w-full text-sm border-separate border-spacing-0">
              <thead className="sticky top-0 bg-[#c0c0c0] z-10">
                <tr className="border-b border-[#808080]">
                  <th className="text-left py-2 px-3 text-[#808080] font-semibold">Metric</th>
                  {availableData.map(({ period }) => (
                    <th key={period} className="text-right py-2 px-3 text-[#808080] font-semibold">
                      {period}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.label} className="border-b border-[#c0c0c0] hover:bg-[#c0c0c0]">
                    <td className="py-1.5 px-3 text-black max-w-[360px]">
                      <span className="block truncate whitespace-nowrap" title={row.label}>
                        {truncateLabel(row.label, 45)}
                      </span>
                    </td>
                    {row.values.map(({ period, value }) => (
                      <td key={period} className="text-right py-1.5 px-3 font-mono text-black">
                        {fmt(value)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {renderStatement('Income Statement', latestFull?.income)}
          {renderStatement('Balance Sheet', latestFull?.balance)}
          {renderStatement('Cash Flow Statement', latestFull?.cashflow)}
        </>
      ) : (
        <div className="text-xs text-[#808080]">Loading financials...</div>
      )}
    </div>
  );
}


