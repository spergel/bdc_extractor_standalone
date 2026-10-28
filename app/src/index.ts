/**
 * BDC app — plain HTML interface over the extracted holdings + financial data.
 *
 * Local development:
 *   cd app && npm install && npm run dev
 *   open http://localhost:8787
 *
 * Routing is plain Hono; on Cloudflare, replace `readDataFile` with
 * `env.ASSETS.fetch(...)` / R2 / KV.
 */

import { Hono } from "hono";
import { serve } from "@hono/node-server";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const PORT = Number(process.env.PORT || 8787);
const DATA_DIR = resolve(process.cwd(), "public/data");

const app = new Hono();

// ---------------------------------------------------------------------------
// Data helpers (local filesystem; swap for Workers assets/R2/KV on Cloudflare)
// ---------------------------------------------------------------------------

async function readDataFile<T = string>(rel: string): Promise<T | null> {
  try {
    const path = resolve(DATA_DIR, rel);
    if (!path.startsWith(DATA_DIR)) return null; // path traversal guard
    const buf = await readFile(path);
    return buf.toString("utf8") as T;
  } catch {
    return null;
  }
}

async function readJson<T>(rel: string): Promise<T | null> {
  const text = await readDataFile(rel);
  if (text == null) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

/** Minimal CSV parser that handles quoted fields (commas inside names, etc). */
function parseCsv(text: string): { header: string[]; rows: Record<string, string>[] } {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (c !== "\r") {
      field += c;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  const header = rows[0] ?? [];
  const data = rows.slice(1).map((r) => {
    const obj: Record<string, string> = {};
    header.forEach((h, i) => {
      obj[h] = r[i] ?? "";
    });
    return obj;
  });
  return { header, rows: data };
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

type IndexPayload = {
  generated_at?: string;
  total_tickers?: number;
  tickers?: Record<string, { periods: string[]; latest: string | null; row_count: number }>;
};

app.get("/api/tickers", async (c) => {
  const index = await readJson<IndexPayload>("investments_index.json");
  if (!index) return c.json({ error: "no investments_index.json found" }, 500);
  return c.json(index);
});

app.get("/api/tickers/:ticker", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const index = await readJson<IndexPayload>("investments_index.json");
  const info = index?.tickers?.[ticker];
  if (!info) return c.json({ error: `ticker ${ticker} not found` }, 404);
  return c.json({ ticker, ...info });
});

app.get("/api/tickers/:ticker/periods/:period", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const period = c.req.param("period");
  const csv = await readDataFile(`investments/${ticker}/${period}.csv`);
  if (csv == null) return c.json({ error: "period not found" }, 404);
  const parsed = parseCsv(csv);
  return c.json({ ticker, period, columns: parsed.header, rows: parsed.rows });
});

app.get("/api/tickers/:ticker/analytics", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const data = await readJson(`analytics/${ticker}.json`);
  if (!data) return c.json({ error: "analytics not found" }, 404);
  return c.json(data);
});

app.get("/api/tickers/:ticker/financials", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const data = await readJson(`financials/${ticker}_financials.json`);
  if (!data) return c.json({ error: "financials not found" }, 404);
  return c.json(data);
});

app.get("/api/tickers/:ticker/profile", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const data = await readJson(`${ticker}/profile.json`);
  if (!data) return c.json({ error: "profile not found" }, 404);
  return c.json(data);
});

app.get("/api/peers", async (c) => {
  const data = await readJson("analytics/peers.json");
  if (!data) return c.json({ error: "peers not found" }, 404);
  return c.json(data);
});

/** One request that returns compact summaries of every ticker profile. */
app.get("/api/profiles", async (c) => {
  const index = await readJson<IndexPayload>("investments_index.json");
  const tickers = Object.keys(index?.tickers ?? {});
  const out: Record<string, any> = {};
  for (const t of tickers) {
    const p = await readJson<any>(`${t}/profile.json`);
    if (p) {
      out[t] = {
        name: p.name || t,
        price: p.price?.regularMarketPrice ?? null,
        dividendYield: p.summaryDetail?.dividendYield ?? null,
        bookValue: p.summaryDetail?.bookValue ?? null,
      };
    } else {
      out[t] = { name: t, price: null, dividendYield: null, bookValue: null };
    }
  }
  return c.json(out);
});

// Raw static data files (profile.json, company_profiles.json, ...)
app.get("/data/*", async (c) => {
  const rel = c.req.path.replace(/^\/data\//, "");
  const text = await readDataFile(rel);
  if (text == null) return c.text("not found", 404);
  return c.body(text);
});

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

app.get("/", (c) => c.html(UI_HTML));

// ---------------------------------------------------------------------------
// Start (local Node)
// ---------------------------------------------------------------------------

serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`BDC app running at http://localhost:${info.port}`);
  console.log(`Serving data from ${DATA_DIR}`);
});

export default app;

// ---------------------------------------------------------------------------
// HTML — plain HTML/CSS/JS, no framework, no build step
// ---------------------------------------------------------------------------

const UI_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>BDC Portfolio Analytics</title>
<style>
  :root {
    --bg: #e9ebee;
    --panel: #ffffff;
    --navy: #1a2f5a;
    --navy2: #24457c;
    --line: #c9cdd4;
    --text: #1c1f24;
    --muted: #5c6570;
    --accent: #c08a2d;
    --good: #1d7a4f;
    --bad: #a13333;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
    font-size: 13px;
  }
  header.top {
    background: var(--navy);
    color: #fff;
    padding: 8px 14px;
    display: flex;
    align-items: baseline;
    gap: 12px;
    border-bottom: 3px solid var(--accent);
  }
  header.top h1 { font-size: 15px; margin: 0; font-weight: 700; letter-spacing: .2px; }
  header.top .sub { font-size: 11px; opacity: .8; }
  .layout { display: grid; grid-template-columns: 250px 1fr; gap: 10px; padding: 10px; height: calc(100% - 40px); }
  .window { background: var(--panel); border: 1px solid var(--line); border-radius: 4px; display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; }
  .titlebar { background: var(--navy2); color: #fff; font-weight: 700; font-size: 12px; padding: 6px 10px; display: flex; align-items: center; justify-content: space-between; }
  .titlebar .meta { font-weight: 400; font-size: 10.5px; opacity: .85; }
  .body { flex: 1; overflow: auto; padding: 10px; }
  .sidebar .body { padding: 6px; }
  .sidebar input { width: calc(100% - 12px); margin: 6px; padding: 5px 8px; border: 1px solid var(--line); border-radius: 3px; font-size: 12px; }
  .ticker {
    display: block; width: 100%; text-align: left;
    background: #f6f7f9; border: 1px solid var(--line); border-radius: 3px;
    padding: 6px 8px; margin-bottom: 4px; cursor: pointer; font-size: 12px;
  }
  .ticker:hover { background: #eef1f5; }
  .ticker.active { background: var(--navy2); color: #fff; border-color: var(--navy2); }
  .ticker .name { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ticker.active .name { color: #d6e0f0; }
  .toolbar { display: flex; gap: 8px; padding: 8px 10px; align-items: center; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .toolbar input, .toolbar select { font-size: 12px; padding: 4px 6px; border: 1px solid var(--line); border-radius: 3px; }
  .toolbar input { flex: 1; min-width: 140px; }
  .status { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .tabs { display: flex; gap: 2px; padding: 0 8px; background: #f2f3f5; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .tab { padding: 7px 12px; cursor: pointer; font-size: 12px; border: 1px solid transparent; border-bottom: none; background: transparent; color: var(--muted); }
  .tab:hover { color: var(--text); }
  .tab.active { background: var(--panel); color: var(--navy2); font-weight: 700; border-color: var(--line); border-radius: 4px 4px 0 0; position: relative; top: 1px; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 12px; }
  th, td { border: 1px solid var(--line); padding: 3px 7px; white-space: nowrap; text-align: left; }
  th { background: #eef1f5; position: sticky; top: 0; cursor: default; }
  th.sortable { cursor: pointer; }
  th.sortable:hover { background: #dfe4ea; }
  tr:nth-child(even) td { background: #fafbfc; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.company { max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-bottom: 10px; }
  .card { background: #f6f7f9; border: 1px solid var(--line); border-radius: 4px; padding: 8px 10px; }
  .card .label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .card .value { font-size: 17px; font-weight: 700; margin-top: 2px; }
  .card .note { font-size: 10.5px; color: var(--muted); margin-top: 1px; }
  .section { margin-bottom: 14px; }
  .section h3 { font-size: 12.5px; margin: 0 0 6px; color: var(--navy2); }
  .bars { display: flex; flex-direction: column; gap: 3px; }
  .bar-row { display: grid; grid-template-columns: 170px 1fr 150px; gap: 6px; align-items: center; font-size: 11.5px; }
  .bar-row .label { text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { background: #eef1f5; border: 1px solid var(--line); border-radius: 2px; height: 14px; overflow: hidden; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, var(--navy2), #4f7bc0); }
  .bar-fill.alt { background: linear-gradient(90deg, #c08a2d, #e0b45f); }
  .bar-row .val { color: var(--muted); white-space: nowrap; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  .muted { color: var(--muted); }
  .small { font-size: 10.5px; }
  .pos { color: var(--good); }
  .neg { color: var(--bad); }
  .empty { padding: 20px; color: var(--muted); }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 10.5px; background: #eef1f5; border: 1px solid var(--line); margin-right: 4px; }
  @media (max-width: 800px) {
    .layout { grid-template-columns: 1fr; grid-template-rows: 220px 1fr; }
    .bar-row { grid-template-columns: 120px 1fr 110px; }
  }
</style>
</head>
<body>
  <header class="top">
    <h1>BDC Portfolio Analytics</h1>
    <span class="sub">Holdings + quarterly financials from SEC 10-Q / 10-K filings</span>
  </header>
  <div class="layout">
    <aside class="window sidebar">
      <div class="titlebar"><span>BDCs</span><span class="meta" id="ticker-count"></span></div>
      <input id="ticker-search" placeholder="Filter by ticker or name…" />
      <div class="body" id="ticker-list">Loading…</div>
    </aside>
    <main class="window">
      <div class="titlebar"><span id="win-title">Select a BDC</span><span class="meta" id="win-meta"></span></div>
      <div class="toolbar">
        <label class="small muted" for="period-select">Filing period</label>
        <select id="period-select" style="display:none"></select>
        <input id="filter" placeholder="Filter holdings by company / industry / type…" style="display:none" />
        <span class="status" id="status">–</span>
      </div>
      <div class="tabs" id="tabs"></div>
      <div class="body" id="main"><div class="empty">Select a BDC from the list.</div></div>
    </main>
  </div>

<script>
const $ = (id) => document.getElementById(id);
const state = {
  index: null,
  profiles: {},
  peers: null,
  ticker: null,
  period: null,
  analytics: null,
  financials: null,
  profile: null,
  holdings: null,
  tab: 'overview',
  sortKey: null,
  sortDir: 1,
};

// ------------------------------------------------------------------ utils
function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
}
function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(String(v).replace(/[,$%()]/g, ''));
  return Number.isFinite(n) ? n : null;
}
// Holdings values are stored in thousands of dollars.
function fmtK(v) {
  const n = num(v);
  if (n === null) return '—';
  const abs = Math.abs(n * 1000);
  if (abs >= 1e12) return (n * 1000 / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (n * 1000 / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n * 1000 / 1e6).toFixed(1) + 'M';
  return (n * 1000).toLocaleString();
}
// Financial values are raw dollars.
function fmtMoney(v) {
  const n = num(v);
  if (n === null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(0) + 'K';
  return n.toLocaleString();
}
function fmtPct(v, digits) {
  const n = num(v);
  if (n === null) return '—';
  return n.toFixed(digits ?? 1) + '%';
}
function fmtRate(v) {
  const n = num(v);
  if (n === null) return '—';
  return n.toFixed(2) + '%';
}
function fmtNum(v) {
  const n = num(v);
  return n === null ? '—' : n.toLocaleString();
}
function durationLabel(days) {
  const d = num(days);
  if (d === null) return '';
  if (d <= 100) return 'Q';
  if (d <= 195) return 'H1';
  if (d <= 290) return '9M';
  return 'FY';
}

// ------------------------------------------------------------------ loading
async function loadTickers() {
  const res = await fetch('/api/tickers');
  state.index = await res.json();
  const [pRes] = await Promise.all([fetch('/api/profiles')]);
  state.profiles = await pRes.json();
  renderSidebar();
}
function renderSidebar() {
  const tickers = state.index.tickers || {};
  const q = ($('ticker-search').value || '').toLowerCase();
  const names = Object.keys(tickers).sort();
  const list = $('ticker-list');
  list.innerHTML = '';
  $('ticker-count').textContent = names.length + ' BDCs';
  let shown = 0;
  for (const t of names) {
    const prof = state.profiles[t] || {};
    const label = (prof.name || t) + ' ' + t;
    if (q && label.toLowerCase().indexOf(q) < 0) continue;
    shown++;
    const btn = document.createElement('button');
    btn.className = 'ticker' + (t === state.ticker ? ' active' : '');
    btn.innerHTML = '<div><b>' + t + '</b></div><div class="name">' + esc(prof.name || '') + '</div>';
    btn.onclick = () => selectTicker(t, tickers[t]);
    list.appendChild(btn);
  }
  if (!shown) list.innerHTML = '<div class="empty">No match.</div>';
}

const TABS = [
  ['overview', 'Overview'],
  ['holdings', 'Holdings'],
  ['types', 'Loan Types'],
  ['sectors', 'Sectors'],
  ['vintage', 'Vintage'],
  ['financials', 'Financials'],
  ['peers', 'Peers'],
];
function renderTabs() {
  const el = $('tabs');
  el.innerHTML = '';
  for (const [id, label] of TABS) {
    const b = document.createElement('button');
    b.className = 'tab' + (id === state.tab ? ' active' : '');
    b.textContent = label;
    b.onclick = () => { state.tab = id; renderTabs(); $('filter').style.display = id === 'holdings' ? '' : 'none'; renderMain(); };
    el.appendChild(b);
  }
}

async function selectTicker(ticker, info) {
  state.ticker = ticker;
  state.period = info && info.latest ? info.latest : (info && info.periods && info.periods.length ? info.periods[info.periods.length - 1] : null);
  state.analytics = null; state.financials = null; state.profile = null; state.holdings = null;
  document.querySelectorAll('.ticker').forEach(b => b.classList.toggle('active', b.textContent.indexOf(ticker) === 0));
  const prof = state.profiles[ticker] || {};
  $('win-title').textContent = ticker + ' — ' + (prof.name || ticker);
  $('win-meta').textContent = '';
  renderPeriodSelect(info);
  $('status').textContent = 'Loading ' + ticker + '…';
  const [aRes, fRes, pRes] = await Promise.all([
    fetch('/api/tickers/' + ticker + '/analytics'),
    fetch('/api/tickers/' + ticker + '/financials'),
    fetch('/api/tickers/' + ticker + '/profile'),
  ]);
  state.analytics = aRes.ok ? await aRes.json() : null;
  state.financials = fRes.ok ? await fRes.json() : null;
  state.profile = pRes.ok ? await pRes.json() : null;
  renderMain();
  $('status').textContent = ticker + ' · ' + (info.row_count || 0).toLocaleString() + ' holdings across ' + ((info.periods || []).length) + ' filings';
}
function renderPeriodSelect(info) {
  const sel = $('period-select');
  const periods = (info && info.periods) || [];
  sel.style.display = periods.length ? '' : 'none';
  sel.innerHTML = '';
  for (const p of periods.slice().reverse()) {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    if (p === state.period) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ------------------------------------------------------------------ main render
function renderMain() {
  if (!state.ticker) return;
  const el = $('main');
  if (state.tab === 'overview') renderOverview(el);
  else if (state.tab === 'holdings') renderHoldings(el);
  else if (state.tab === 'types') renderBreakdown(el, 'types', 'Loan Types');
  else if (state.tab === 'sectors') renderBreakdown(el, 'sectors', 'Sectors');
  else if (state.tab === 'vintage') renderVintage(el);
  else if (state.tab === 'financials') renderFinancials(el);
  else if (state.tab === 'peers') renderPeers(el);
}

function currentPeriodData() {
  if (!state.analytics || !state.analytics.periods || !state.analytics.periods.length) return null;
  const periods = state.analytics.periods;
  if (state.period) {
    const hit = periods.find(p => p.filing_date === state.period);
    if (hit) return hit;
  }
  return periods[periods.length - 1];
}

function latestDerived(metric) {
  const node = (state.financials && state.financials.derived || {})[metric];
  if (!node || !node.series) return null;
  const ends = Object.keys(node.series);
  if (!ends.length) return null;
  return node.series[ends[ends.length - 1]].val;
}
function latestStatement(label) {
  const node = (state.financials && state.financials.statements && state.financials.statements.balance_sheet || {})[label];
  if (!node || !node.series) return null;
  const ends = Object.keys(node.series);
  if (!ends.length) return null;
  return node.series[ends[ends.length - 1]].val;
}

// ------------------------------------------------------------------ overview
function renderOverview(el) {
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data for this BDC.</div>'; return; }
  const t = p.totals || {};
  const prof = state.profile || {};
  const nav = latestDerived('nav_per_share');
  const d2e = latestDerived('debt_to_equity');
  const cov = latestDerived('asset_coverage');
  const nii = latestDerived('nii_per_share');
  const price = num(prof.price && prof.price.regularMarketPrice);
  const prNav = nav !== null && price !== null ? (price / nav - 1) * 100 : null;
  const assets = latestStatement('Assets');
  const debt = latestDerived('total_debt');
  const equity = latestStatement("Stockholders' Equity") ?? latestStatement('Net Assets');
  let html = '';
  html += '<div class="cards">' +
    card('Portfolio Fair Value', fmtK(t.fair_value), 'at ' + p.filing_date) +
    card('Principal', fmtK(t.principal), 'par / principal') +
    card('Holdings', fmtNum(t.rows), t.companies + ' companies') +
    card('Wtd Avg Cash Rate', fmtRate(t.weighted_avg_cash_rate), 'fair-value weighted') +
    card('Wtd Avg Spread', fmtRate(t.weighted_avg_spread), 'over reference rate') +
    card('NAV / Share', nav === null ? '—' : '$' + num(nav).toFixed(2), prNav === null ? '' : (prNav >= 0 ? '+' : '') + prNav.toFixed(1) + '% vs price') +
    card('Debt / Equity', d2e === null ? '—' : num(d2e).toFixed(2) + '×', 'asset coverage ' + (cov === null ? '—' : num(cov).toFixed(2) + '×')) +
    card('NII / Share', nii === null ? '—' : '$' + num(nii).toFixed(2), 'latest reported quarter') +
  '</div>';

  html += '<div class="grid2"><div class="section">';
  html += '<h3>Portfolio snapshot — ' + p.filing_date + '</h3>';
  const types = (p.types || []).filter(x => x.pct > 0).slice(0, 6);
  html += bars(types, 'type', 'fair_value', 'pct', fmtK);
  html += '</div><div class="section">';
  html += '<h3>Financial snapshot — latest filings</h3>';
  html += '<table><thead><tr><th>Item</th><th class="num">Value</th></tr></thead><tbody>';
  html += row2('Total Assets', fmtMoney(assets));
  html += row2('Total Debt', fmtMoney(debt));
  html += row2('Total Equity', fmtMoney(equity));
  html += row2('NAV per Share', nav === null ? '—' : '$' + num(nav).toFixed(2));
  html += row2('Debt / Equity', d2e === null ? '—' : num(d2e).toFixed(2) + '×');
  html += row2('Asset Coverage', cov === null ? '—' : num(cov).toFixed(2) + '×');
  if (price !== null) html += row2('Market Price', '$' + price.toFixed(2));
  html += '</tbody></table></div></div>';
  el.innerHTML = html;
}
function card(label, value, note) {
  return '<div class="card"><div class="label">' + esc(label) + '</div><div class="value">' + value + '</div><div class="note">' + esc(note || '') + '</div></div>';
}
function row2(a, b) {
  return '<tr><td>' + esc(a) + '</td><td class="num">' + b + '</td></tr>';
}
function bars(items, labelKey, valKey, pctKey, fmt) {
  if (!items || !items.length) return '<div class="empty">No data.</div>';
  const max = Math.max.apply(null, items.map(i => Math.abs(num(i[pctKey]) || 0)));
  let html = '<div class="bars">';
  for (const it of items) {
    const pct = num(it[pctKey]) || 0;
    const w = max > 0 ? Math.max(1.5, Math.abs(pct) / max * 100) : 0;
    html += '<div class="bar-row"><span class="label" title="' + esc(it[labelKey]) + '">' + esc(it[labelKey]) + '</span>' +
      '<div class="bar-track"><div class="bar-fill" style="width:' + w.toFixed(1) + '%"></div></div>' +
      '<span class="val">' + fmt(it[valKey]) + ' · ' + fmtPct(pct) + '</span></div>';
  }
  return html + '</div>';
}

// ------------------------------------------------------------------ holdings
async function renderHoldings(el) {
  if (!state.holdings) {
    el.innerHTML = '<div class="empty">Loading holdings…</div>';
    const res = await fetch('/api/tickers/' + state.ticker + '/periods/' + state.period);
    const data = await res.json();
    state.holdings = data.rows || [];
  }
  const filter = ($('filter').value || '').toLowerCase();
  let rows = state.holdings;
  if (filter) rows = rows.filter(r => Object.values(r).some(v => String(v).toLowerCase().indexOf(filter) >= 0));
  const totalFv = rows.reduce((s, r) => s + (num(r.fair_value) || 0), 0);
  const cols = [
    ['company_name', 'Company', 'company'],
    ['investment_type', 'Type', ''],
    ['industry', 'Industry', ''],
    ['cash_rate', 'Cash %', 'num'],
    ['pik_rate', 'PIK %', 'num'],
    ['reference_rate', 'Ref', ''],
    ['spread', 'Spread %', 'num'],
    ['maturity_date', 'Maturity', ''],
    ['principal_amount', 'Principal', 'num'],
    ['amortized_cost', 'Amort Cost', 'num'],
    ['fair_value', 'Fair Value', 'num'],
    ['pct_portfolio', '% of FV', 'num'],
  ];
  let html = '<table><thead><tr>' + cols.map(c => '<th class="' + (c[2] === 'num' ? 'num' : '') + '">' + c[1] + '</th>').join('') + '</tr></thead><tbody>';
  const slice = rows.slice(0, 3000);
  for (const r of slice) {
    html += '<tr>';
    for (const [key, , cls] of cols) {
      let v;
      if (key === 'pct_portfolio') {
        const fv = num(r.fair_value);
        v = fv !== null && totalFv > 0 ? (fv / totalFv * 100).toFixed(1) + '%' : '—';
      } else if (key === 'cash_rate' || key === 'pik_rate' || key === 'spread') {
        v = fmtRate(r[key]);
      } else if (key === 'principal_amount' || key === 'amortized_cost' || key === 'fair_value') {
        v = fmtK(r[key]);
      } else {
        v = r[key] || '';
      }
      const title = key === 'company_name' && v ? ' title="' + esc(v) + '"' : '';
      html += '<td class="' + cls + (cls === 'company' ? ' company' : '') + '"' + title + '>' + esc(v) + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  if (rows.length > slice.length) html += '<div class="muted small" style="margin-top:6px">Showing first ' + slice.length.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' rows.</div>';
  el.innerHTML = rows.length ? html : '<div class="empty">No holdings match.</div>';
}

// ------------------------------------------------------------------ breakdowns
function renderBreakdown(el, kind, title) {
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const list = p[kind] || [];
  const key = kind === 'types' ? 'type' : 'sector';
  const valKey = kind === 'types' ? 'principal' : null;
  let html = '<div class="section"><h3>' + title + ' — ' + p.filing_date + '</h3>' + bars(list, key, 'fair_value', 'pct', fmtK) + '</div>';
  if (kind === 'types') {
    html += '<div class="section"><h3>Details</h3><table><thead><tr><th>Type</th><th class="num">Fair Value</th><th class="num">%</th><th class="num">Positions</th><th class="num">Principal</th><th class="num">Avg FV per position</th></tr></thead><tbody>';
    for (const it of list) {
      const avg = it.count ? it.fair_value / it.count : null;
      html += '<tr><td>' + esc(it.type) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td><td class="num">' + fmtNum(it.count) + '</td><td class="num">' + fmtK(it.principal) + '</td><td class="num">' + (avg === null ? '—' : fmtK(avg)) + '</td></tr>';
    }
    html += '</tbody></table></div>';
  } else {
    html += '<div class="section"><h3>Details</h3><table><thead><tr><th>Sector</th><th class="num">Fair Value</th><th class="num">%</th><th class="num">Positions</th></tr></thead><tbody>';
    for (const it of list) {
      html += '<tr><td>' + esc(it.sector) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td><td class="num">' + fmtNum(it.count) + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }
  html += historyMatrix(el, kind, title);
  el.innerHTML = html;
}

function historyMatrix(el, kind, title) {
  const periods = (state.analytics.periods || []).slice(-8);
  const key = kind === 'types' ? 'type' : 'sector';
  // union of top categories across those periods
  const counts = {};
  for (const p of periods) for (const it of (p[kind] || [])) counts[it[key]] = (counts[it[key]] || 0) + (it.pct || 0);
  const cats = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 8);
  let html = '<div class="section"><h3>Mix over time (% of fair value)</h3><table><thead><tr><th>' + (kind === 'types' ? 'Type' : 'Sector') + '</th>' +
    periods.map(p => '<th class="num">' + esc(p.filing_date.slice(0, 7)) + '</th>').join('') + '</tr></thead><tbody>';
  for (const cat of cats) {
    html += '<tr><td>' + esc(cat) + '</td>';
    for (const p of periods) {
      const it = (p[kind] || []).find(x => x[key] === cat);
      const v = it ? it.pct : 0;
      const strong = v >= 20;
      html += '<td class="num" style="background:' + (strong ? '#e8effa' : 'transparent') + '">' + (v > 0 ? v.toFixed(1) : '·') + '</td>';
    }
    html += '</tr>';
  }
  return html + '</tbody></table></div>';
}

// ------------------------------------------------------------------ vintage
function renderVintage(el) {
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const cov = p.totals && p.totals.vintage_coverage_pct;
  let html = '<div class="section"><h3>Vintage — ' + p.filing_date + '</h3>' +
    '<div class="muted small">Origination/acquisition year coverage: ' + fmtPct(cov) + ' of fair value' + (cov !== null && cov < 90 ? ' (other rows do not report an acquisition date)' : '') + '.</div><br>' +
    bars(p.vintage_years || [], 'year', 'fair_value', 'pct', fmtK) + '</div>';
  html += '<div class="grid2">';
  html += '<div class="section"><h3>Portfolio age (years since acquisition)</h3>' + bars(p.age_buckets || [], 'bucket', 'fair_value', 'pct', fmtK) + '</div>';
  html += '<div class="section"><h3>Maturity buckets</h3>' + bars(p.maturity_buckets || [], 'bucket', 'fair_value', 'pct', fmtK) + '</div>';
  html += '</div>';
  html += '<div class="section"><h3>Top 10 companies by fair value</h3><table><thead><tr><th>Company</th><th class="num">Fair Value</th></tr></thead><tbody>';
  for (const c of (p.top_companies || [])) html += '<tr><td>' + esc(c.company) + '</td><td class="num">' + fmtK(c.fair_value) + '</td></tr>';
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

// ------------------------------------------------------------------ financials
function renderFinancials(el) {
  const fin = state.financials;
  if (!fin) { el.innerHTML = '<div class="empty">No financial statement data for this BDC.</div>'; return; }
  const periods = (fin.periods || []).slice(-8);
  const derived = fin.derived || {};
  let html = '<div class="cards">';
  const nav = latestDerived('nav_per_share');
  const d2e = latestDerived('debt_to_equity');
  const cov = latestDerived('asset_coverage');
  const nii = latestDerived('nii_per_share');
  html += card('NAV / Share', nav === null ? '—' : '$' + num(nav).toFixed(2), 'latest quarter end');
  html += card('Debt / Equity', d2e === null ? '—' : num(d2e).toFixed(2) + '×', '');
  html += card('Asset Coverage', cov === null ? '—' : num(cov).toFixed(2) + '×', '');
  html += card('NII / Share', nii === null ? '—' : '$' + num(nii).toFixed(2), '');
  html += '</div>';

  const stmtInfo = [
    ['Balance Sheet', 'balance_sheet'],
    ['Income Statement', 'income_statement'],
    ['Cash Flow Statement', 'cash_flow'],
  ];
  for (const [label, key] of stmtInfo) {
    const nodes = (fin.statements || {})[key] || {};
    const labels = Object.keys(nodes);
    if (!labels.length) continue;
    html += '<div class="section"><h3>' + label + '</h3><table><thead><tr><th>Line item</th>';
    for (const pe of periods) html += '<th class="num">' + esc(pe.end.slice(0, 7)) + '<div class="small muted">' + esc(pe.form || '') + '</div></th>';
    html += '</tr></thead><tbody>';
    for (const l of labels) {
      const node = nodes[l];
      html += '<tr><td title="' + esc(node.label || l) + '">' + esc(l) + '</td>';
      for (const pe of periods) {
        const row = node.series && node.series[pe.end];
        let v = '—';
        if (row && row.val !== null && row.val !== undefined) {
          const unit = node.unit || row.unit || 'USD';
          if (unit === 'USD') v = fmtMoney(row.val);
          else if (unit === 'USD/shares') v = '$' + num(row.val).toFixed(2);
          else if (unit === 'shares') v = fmtNum(row.val);
          else if (unit === 'ratio' || unit === 'pure') v = num(row.val).toFixed(2);
          else v = num(row.val).toLocaleString();
          const d = durationLabel(row.duration_days);
          if (d) v += ' <span class="small muted">' + d + '</span>';
          if (num(row.val) < 0) v = '<span class="neg">' + v + '</span>';
        }
        html += '<td class="num">' + v + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table></div>';
  }
  el.innerHTML = html;
}

// ------------------------------------------------------------------ peers
async function renderPeers(el) {
  if (!state.peers) {
    el.innerHTML = '<div class="empty">Loading peer comparison…</div>';
    const res = await fetch('/api/peers');
    state.peers = await res.json();
  }
  const peers = (state.peers.peers || []).slice();
  if (state.sortKey) {
    const k = state.sortKey;
    peers.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * state.sortDir;
      return String(av ?? '').localeCompare(String(bv ?? '')) * state.sortDir;
    });
  }
  const cols = [
    ['ticker', 'Ticker'],
    ['period', 'Filing'],
    ['fair_value', 'Fair Value'],
    ['companies', 'Companies'],
    ['first_lien_pct', 'First Lien %'],
    ['second_lien_pct', 'Second Lien %'],
    ['subordinated_pct', 'Subord. %'],
    ['equity_pct', 'Equity %'],
    ['weighted_avg_cash_rate', 'Avg Cash %'],
    ['weighted_avg_spread', 'Avg Spread %'],
    ['median_vintage_year', 'Median Vintage'],
    ['age_0_2_pct', '<2yr %'],
    ['age_5plus_pct', '5yr+ %'],
    ['nav_per_share', 'NAV/Sh'],
    ['debt_to_equity', 'D/E'],
    ['asset_coverage', 'Coverage'],
    ['nii_per_share', 'NII/Sh'],
  ];
  let html = '<div class="muted small" style="margin-bottom:6px">Latest filing per BDC. Click a column to sort; click a row to open that BDC.</div>';
  html += '<table id="peers-table"><thead><tr>';
  for (const [key, label] of cols) {
    html += '<th class="sortable num" data-sort="' + key + '">' + label + (state.sortKey === key ? (state.sortDir === 1 ? ' ▲' : ' ▼') : '') + '</th>';
  }
  html += '</tr></thead><tbody>';
  for (const r of peers) {
    html += '<tr style="cursor:pointer" data-ticker="' + r.ticker + '">';
    for (const [key] of cols) {
      let v = r[key];
      if (key === 'fair_value') v = fmtK(v);
      else if (key === 'first_lien_pct' || key === 'second_lien_pct' || key === 'subordinated_pct' || key === 'equity_pct' || key === 'age_0_2_pct' || key === 'age_5plus_pct') v = fmtPct(v);
      else if (key === 'weighted_avg_cash_rate' || key === 'weighted_avg_spread') v = fmtRate(v);
      else if (key === 'nav_per_share' || key === 'nii_per_share') v = v === null || v === undefined ? '—' : '$' + num(v).toFixed(2);
      else if (key === 'debt_to_equity' || key === 'asset_coverage') v = v === null || v === undefined ? '—' : num(v).toFixed(2) + '×';
      else if (key === 'median_vintage_year') v = v === null || v === undefined ? '—' : v;
      html += '<td class="num">' + (v === null || v === undefined ? '—' : v) + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
  el.querySelectorAll('#peers-table th[data-sort]').forEach(th => {
    th.onclick = () => sortPeers(th.getAttribute('data-sort'));
  });
  el.querySelectorAll('#peers-table tr[data-ticker]').forEach(tr => {
    tr.onclick = () => {
      const t = tr.getAttribute('data-ticker');
      if (state.index && state.index.tickers && state.index.tickers[t]) {
        state.tab = 'overview';
        renderTabs();
        selectTicker(t, state.index.tickers[t]);
      }
    };
  });
}
function sortPeers(key) {
  if (state.sortKey === key) state.sortDir = -state.sortDir;
  else { state.sortKey = key; state.sortDir = 1; }
  renderPeers($('main'));
}

// ------------------------------------------------------------------ events
$('ticker-search').oninput = renderSidebar;
$('period-select').onchange = () => {
  state.period = $('period-select').value;
  state.holdings = null;
  $('filter').style.display = state.tab === 'holdings' ? '' : 'none';
  renderMain();
};
$('filter').oninput = () => renderHoldings($('main'));

renderTabs();
loadTickers();
</script>
</body>
</html>`;
