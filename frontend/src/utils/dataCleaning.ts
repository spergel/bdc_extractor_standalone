/**
 * Data normalization layer for BDC investment data.
 *
 * LLM-scraped SEC filings use wildly inconsistent terminology across BDCs.
 * These functions normalize investment types, industries, and reference rates
 * into standard categories using keyword-based matching.
 */

/** Decode common HTML entities that leak through from SEC filings */
function decodeHtmlEntities(s: string): string {
  return s
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#x2F;/g, '/');
}

/**
 * Normalize investment type strings to a small set of standard categories.
 * Uses keyword cascade — first match wins.
 */
/**
 * Canonical investment types (aligned with backend standardization_rules.py):
 *   First Lien, Second Lien, Delayed Draw, Revolver,
 *   Subordinated Debt, Unsecured Debt, Preferred Equity, Common Equity,
 *   Partnership Interest, Warrants, Other Equity, Other Debt
 */
export function normalizeInvestmentType(raw: string | undefined | null): string {
  if (!raw || !raw.trim()) return '';
  const s = raw.trim();
  const lower = s.toLowerCase();

  // Delayed draw (before first lien — DDTLs are often first lien)
  if (lower.includes('delayed draw') || lower.includes('ddtl')) return 'Delayed Draw';

  // Revolver (before first lien, since revolvers can be first lien)
  if (lower.includes('revolver') || lower.includes('revolving')) return 'Revolver';

  // Second lien / last-out before first lien
  if (lower.includes('second lien') || lower.includes('2nd lien') || lower.includes('last-out')) return 'Second Lien';

  // First lien / senior secured (after revolver and second lien checks)
  if (lower.includes('first lien') || lower.includes('1st lien') || lower.includes('senior secured') || lower.includes('unitranche') || lower.includes('term loan')) return 'First Lien';

  // Subordinated / mezzanine
  if (lower.includes('subordinat') || lower.includes('mezzanine')) return 'Subordinated Debt';

  // Unsecured
  if (lower.includes('unsecured')) return 'Unsecured Debt';

  // Warrant
  if (lower.includes('warrant')) return 'Warrants';

  // Preferred equity (must check before generic equity)
  if (lower.includes('preferred') && (lower.includes('equity') || lower.includes('stock') || lower.includes('share') || lower.includes('unit'))) return 'Preferred Equity';

  // Common equity
  if (lower.includes('common') && (lower.includes('equity') || lower.includes('stock') || lower.includes('share') || lower.includes('unit'))) return 'Common Equity';

  // Partnership / membership interest
  if (lower.includes('partnership') || lower.includes('member interest') || lower.includes('lp interest')) return 'Partnership Interest';

  // Generic equity fallback
  if (lower.includes('equity') || lower.includes('stock') || lower.includes('share') || lower.includes('ownership')) return 'Other Equity';

  // Generic debt fallback
  if (lower.includes('debt') || lower.includes('loan') || lower.includes('note') || lower.includes('bond')) return 'Other Debt';

  return s;
}

// ---------------------------------------------------------------------------
// Industry normalization
// ---------------------------------------------------------------------------

/** Helper: returns true if `s` contains any of the given keywords */
function has(s: string, keywords: string[]): boolean {
  return keywords.some(kw => s.includes(kw));
}

/** Exact-match junk values that are not real industries */
const JUNK_EXACT = new Set([
  'cash', 'cash equivalents', 'cash and cash equivalents',
  'debt', 'equity', 'equity interest', 'common stock', 'preferred stock',
  'expense', 'fee expense', 'financing', 'investment',
  'n/a', 'unknown', 'various', 'other', 'industry', 'inc.', 'l', 'libor',
]);

/**
 * Industry keyword cascade — ordered from most specific to most generic.
 * First match wins. Each entry: [keywords, standardized category].
 *
 * ORDERING RATIONALE:
 *  - Specific end-markets (Aerospace, Insurance, Healthcare...) first so that
 *    "healthcare software" → Healthcare, not Software.
 *  - Generic categories (Software, Transportation, Industrials, Business
 *    Services) last so they only catch values with no specific end-market.
 *  - Transportation before Media so "transport" doesn't false-positive via
 *    the substring "sport" matching Media keywords.
 */
const INDUSTRY_RULES: [string[], string][] = [
  // 1. Aerospace & Defense
  [['aerospace', 'defense', 'defence', 'military', 'avionics', 'aerostructure',
    'armored component'], 'Aerospace & Defense'],

  // 2. Insurance (before Financial — more specific)
  [['insurance', 'underwriting', 'actuarial', 'managing general a'], 'Insurance'],

  // 3. Healthcare & Life Sciences (before Software/Technology/Services)
  [['healthcar', 'health care', 'pharma', 'medical', 'biotech', 'biopharm',
    'dental', 'hospital', 'clinical', 'physician', 'surgical', 'surgery',
    'patient', 'nursing', 'ophthalm', 'dermatol', 'infusion', 'anesthesi',
    'behavioral health', 'mental health', 'telehealth', 'telemedicine',
    'orthop', 'orthot', 'prosthe', 'oncol', 'gastroenter', 'urolog',
    'vascular practice', 'opioid', 'opiod', 'substance abuse', 'substance use',
    'autism', 'pain treatment', 'vision care', 'cancer', 'life science',
    'drug develop', 'drug manufactur', 'therapeutic', 'ambulance',
    'veterinar', 'animal health', 'animal nutrition', 'physical therapy',
    'wellness', 'supplement', 'fertility', 'chiropr',
    'medical device', 'health & pharma'], 'Healthcare'],

  // 4. Education
  [['education', 'school', 'learning', 'academic', 'university', 'k-12',
    'literacy'], 'Education'],

  // 5. Energy (before Industrials)
  [['energy', 'oil &', 'oil and', 'oil gas', 'oil field', 'oilfield',
    'natural gas', 'petroleum', 'propane', 'pipeline', 'midstream',
    'wellhead', 'proppant', 'refining', 'refinery', 'electricity',
    'electric util', 'power generat', 'power plant', 'power &', 'power util',
    'independent power', 'gas util', 'fuel distribut',
    'solar energy', 'solar power', 'solar provider', 'solar system',
    'renewable energy', 'wind farm', 'wind power',
    'energy & util', 'energy util'], 'Energy'],

  // 6. Real Estate
  [['real estate', 'reit', 'property manag', 'homebuilding', 'homebuilder',
    'housing'], 'Real Estate'],

  // 7. Environmental Services (before Industrials)
  [['environmental', 'waste manage', 'waste process', 'waste service',
    'waste collect', 'waste disposal', 'solid waste', 'recycl',
    'remediat', 'water treatment'], 'Environmental Services'],

  // 8. Chemicals & Materials (before Industrials)
  [['chemical', 'plastic', 'rubber', 'polymer', 'adhesive',
    'mineral', 'mining', 'quarr'], 'Chemicals & Materials'],

  // 9. Containers & Packaging (before Industrials)
  [['container', 'packaging', 'paper &', 'paper and', 'forest product',
    'paper packaging'], 'Containers & Packaging'],

  // 10. Automotive (before Industrials & Transportation)
  [['automotive', 'automobile', 'auto part', 'auto body', 'auto component',
    'auto collision', 'auto repair', 'auto aftermarket', 'auto &',
    'vehicle', 'car wash', 'collision repair'], 'Automotive'],

  // 11. Food & Beverage (before Consumer)
  [['food', 'beverage', 'restaurant', 'tobacco', 'bakery', 'dairy',
    'meat', 'seafood', 'snack', 'grocer', 'supermarket',
    'confection', 'spice', 'seasoning', 'catering', 'foodservice',
    'frozen fruit', 'wine', 'beer', 'spirits', 'nutrition',
    'produce distribut'], 'Food & Beverage'],

  // 12. Hospitality & Leisure (before Consumer & Media)
  [['hotel', 'hospitality', 'leisure', 'travel', 'lodging', 'resort',
    'casino', 'recreation', 'fitness', 'cruise', 'amusement',
    'theme park', 'golf', 'gaming'], 'Hospitality & Leisure'],

  // 13. Transportation & Logistics — BEFORE Media to avoid "transport" ⊃ "sport"
  [['transport', 'logistic', 'freight', 'trucking', 'shipping',
    'airline', 'aviation', 'fleet', 'cargo', 'ferry', 'marine',
    'postal', 'courier'], 'Transportation & Logistics'],

  // 14. Media & Entertainment — after Transportation so "transport" doesn't match "sport"
  [['media', 'entertainment', 'advertis', 'publish', 'broadcast',
    'music', 'film', 'sporting', 'sports', 'sport ', 'esport', 'e-sport',
    'soccer', 'martial art', 'streaming', 'television', 'radio', 'movie',
    'cable &', 'cable and', 'satellite', 'video gam'], 'Media & Entertainment'],

  // 15. Telecommunications
  [['telecom', 'tele-com', 'communication service', 'wireless carrier',
    'wireless telecom'], 'Telecommunications'],

  // 16. Financial Services
  [['financial', 'banking', 'capital market', 'asset manage', 'wealth',
    'lending', 'mortgage', 'brokerage', 'securities', 'payment',
    'investment', 'fund admin', 'venture capital', 'private equity',
    'credit rating', 'finance', 'fintech', 'banks'], 'Financial Services'],

  // 17. Consumer & Retail
  [['consumer', 'retail', 'e-commerce', 'ecommerce', 'apparel',
    'footwear', 'fashion', 'clothing', 'household', 'personal care',
    'personal product', 'cosmetic', 'beauty', 'jewelry', 'luxury',
    'sporting good', 'textile', 'pet ', 'pet care'], 'Consumer & Retail'],

  // 18. Software & Technology
  [['software', 'saas', 'technology', 'high tech', 'internet',
    'it service', 'it solution', 'it consulting', 'it hardware',
    'information tech', 'cloud', 'cyber', 'data analytics', 'data center',
    'data service', 'data processing', 'data storage',
    'semiconductor', 'electronic', 'computer', 'hardware',
    'networking', 'virtualization'], 'Software & Technology'],

  // 19. Distribution & Wholesale (catch remaining distribution companies)
  [['distribut', 'wholesale', 'wholesaler'], 'Distribution & Wholesale'],

  // 20. Industrials (broad catch-all for manufacturing, construction, etc.)
  [['manufactur', 'industrial', 'building', 'construct', 'machinery',
    'equipment', 'engineer', 'metal', 'steel', 'welding',
    'fabricat', 'hvac', 'plumbing', 'electrical', 'fire safety',
    'fire protect', 'alarm', 'janitorial', 'cleaning service',
    'maintenance', 'elevator', 'fencing', 'roofing', 'paving',
    'signage', 'capital goods', 'conglomerat'], 'Industrials'],

  // 21. Business Services (very broad catch-all)
  [['service', 'consult', 'staffing', 'outsourc', 'human resource',
    'workforce', 'recruit', 'legal', 'marketing',
    'management', 'professional', 'commercial',
    'government', 'testing', 'inspection', 'provider of'], 'Business Services'],

  // 22. Agriculture
  [['agricult', 'farm', 'crop', 'horticultur', 'landscap',
    'nurseri', 'lawn', 'garden', 'greenhouse', 'animal care'], 'Agriculture'],

  // 23. Diversified — explicit catch for BDCs that use this as a category
  [['diversified'], 'Diversified'],
];

/**
 * Normalize industry strings to ~25 standard categories.
 *
 * Handles:
 *  - Junk from misaligned CSV columns (percentages, numbers, investment types)
 *  - FIRE taxonomy labels ("FIRE: Finance", "FIRE: Insurance", etc.)
 *  - "Services: Business" / "Consumer Goods: Durable" prefix conventions
 *  - ~200+ keyword patterns across 23 industry categories
 *  - Long company descriptions ("Provider of ...", "Manufacturer of ...")
 *    caught by the same keyword matching since they contain industry terms
 */
export function normalizeIndustry(raw: string | undefined | null): string {
  if (!raw || !raw.trim()) return '';
  const s = raw.trim();
  const lower = s.toLowerCase().replace(/&amp;/g, '&');

  // ── Junk detection ───────────────────────────────────────────────────
  // Numbers / percentages from misaligned CSV columns
  if (/^\d[\d.,% ]*(%(\s*PIK)?)?$/.test(s)) return '';
  // Investment types that leaked into industry column
  if (/^(first lien|second lien|senior secured|subordinat|unsecured|revolver|delayed draw|junior secured|unfunded)/i.test(s)) return '';
  // Known non-industry exact values
  if (JUNK_EXACT.has(lower)) return '';

  // ── FIRE taxonomy (Finance, Insurance, Real Estate) ──────────────────
  if (lower.startsWith('fire')) {
    if (lower.includes('insurance')) return 'Insurance';
    if (lower.includes('real estate')) return 'Real Estate';
    return 'Financial Services';
  }

  // ── "Services: X" / "Consumer Goods: X" prefix conventions ───────────
  if (lower.startsWith('services: business') || lower.startsWith('services business')) return 'Business Services';
  if (lower.startsWith('services: consumer') || lower.startsWith('services consumer')) return 'Consumer & Retail';
  if (lower.startsWith('services: professional') || lower.startsWith('services: professional')) return 'Business Services';

  // ── Keyword cascade — first match wins ───────────────────────────────
  for (const [keywords, category] of INDUSTRY_RULES) {
    if (has(lower, keywords)) return category;
  }

  return s;
}

/**
 * Normalize reference rate codes/names to standard labels.
 * Handles single-letter abbreviations the LLM sometimes produces.
 */
export function normalizeReferenceRate(raw: string | undefined | null): string {
  if (!raw || !raw.trim()) return '';
  const s = raw.trim();

  // SOFR patterns: "S", "SF", "SF+", "SOFR", "S+", "S,"
  if (/^S$/i.test(s) || /^SF$/i.test(s) || /^SF\+/i.test(s) || /sofr/i.test(s) || /^S\+/i.test(s) || /^S,/i.test(s)) return 'SOFR';

  // EURIBOR patterns: "E", "EURIBOR", "E+"
  if (/^E$/i.test(s) || /euribor/i.test(s) || /^E\+/i.test(s)) return 'EURIBOR';

  // Prime patterns: "P", "P+", "Prime"
  if (/^P$/i.test(s) || /^P\+/i.test(s) || /prime/i.test(s)) return 'Prime';

  // LIBOR patterns: "L", "L+", "LIBOR"
  if (/libor/i.test(s) || /^L$/i.test(s) || /^L\+/i.test(s)) return 'LIBOR';

  // SONIA
  if (/sonia/i.test(s)) return 'SONIA';

  // BBSY
  if (/bbsy/i.test(s)) return 'BBSY';

  // CDOR patterns: "CA", "CDOR"
  if (/^CA/i.test(s) || /cdor/i.test(s)) return 'CDOR';

  // BBSW (but not BBSY, already matched above)
  if (/^BB/i.test(s)) return 'BBSW';

  return s;
}

/**
 * Apply all normalizers to a single holding row.
 * Writes into _standardized / _clean fields, preserving raw values.
 * Also handles PIK detection in wrong fields and HTML entity decoding.
 */
export function cleanHolding(row: any): any {
  // Investment type normalization
  if (row.investment_type && !row.investment_type_standardized) {
    row.investment_type_standardized = normalizeInvestmentType(row.investment_type);
  }

  // Industry normalization
  if (row.industry && !row.industry_clean) {
    row.industry_clean = normalizeIndustry(row.industry);
  }

  // Reference rate normalization
  if (row.reference_rate && !row.reference_rate_clean) {
    row.reference_rate_clean = normalizeReferenceRate(row.reference_rate);
  }

  // Company name: decode HTML entities
  if (row.company_name && !row.company_name_clean) {
    row.company_name_clean = decodeHtmlEntities(row.company_name.trim());
  }

  // Detect PIK in wrong fields (e.g. cash_rate: "10.26% PIK")
  if (!row.pik_rate) {
    for (const field of ['cash_rate', 'interest_rate', 'spread']) {
      const val = row[field];
      if (typeof val === 'string' && /pik/i.test(val)) {
        const match = val.match(/([\d.]+)\s*%?\s*PIK/i);
        if (match) {
          row.pik_rate = parseFloat(match[1]);
        }
        break;
      }
    }
  }

  return row;
}
