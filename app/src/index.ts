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

// Company-level data (cross-BDC exposures + profiles)
let companiesCache: { header: string[]; rows: Record<string, string>[] } | null = null;
async function loadCompanies() {
  if (!companiesCache) {
    const csv = await readDataFile("company_exposures.csv");
    companiesCache = csv ? parseCsv(csv) : { header: [], rows: [] };
  }
  return companiesCache;
}

let companyDetailCache: any = null;
let companyProfilesCache: any = null;

app.get("/api/companies", async (c) => {
  const data = await loadCompanies();
  return c.json({ count: data.rows.length, companies: data.rows });
});

app.get("/api/companies/:id", async (c) => {
  const id = c.req.param("id");
  const data = await loadCompanies();
  const exposure = data.rows.find((r) => r.company_id === id) ?? null;
  if (!companyDetailCache) companyDetailCache = (await readJson<any>("company_detail.json")) ?? {};
  if (!companyProfilesCache) companyProfilesCache = (await readJson<any>("company_profiles.json")) ?? {};
  return c.json({
    company_id: id,
    exposure,
    detail: companyDetailCache?.detail?.[id] ?? null,
    profile: companyProfilesCache?.profiles?.[id] ?? null,
  });
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
    align-items: center;
    gap: 14px;
    border-bottom: 3px solid var(--accent);
  }
  header.top h1 { font-size: 15px; margin: 0; font-weight: 700; letter-spacing: .2px; }
  header.top .sub { font-size: 11px; opacity: .8; }
  header.top nav { margin-left: auto; display: flex; gap: 4px; }
  header.top nav button {
    background: transparent; color: #d9e2f2; border: 1px solid #4a6591;
    border-radius: 3px; padding: 4px 12px; font-size: 12px; cursor: pointer;
  }
  header.top nav button.active { background: #fff; color: var(--navy2); font-weight: 700; border-color: #fff; }
  .layout { display: grid; grid-template-columns: 250px 1fr; gap: 10px; padding: 10px; height: calc(100% - 42px); }
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
  .toolbar input { flex: 1; min-width: 120px; }
  .status { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .btn { background: #f4f5f7; border: 1px solid var(--line); border-radius: 3px; padding: 4px 10px; cursor: pointer; font-size: 11.5px; }
  .btn:hover { background: #e8ebef; }
  .btn.primary { background: var(--navy2); color: #fff; border-color: var(--navy2); }
  .tabs { display: flex; gap: 2px; padding: 0 8px; background: #f2f3f5; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .tab { padding: 7px 11px; cursor: pointer; font-size: 12px; border: 1px solid transparent; border-bottom: none; background: transparent; color: var(--muted); }
  .tab:hover { color: var(--text); }
  .tab.active { background: var(--panel); color: var(--navy2); font-weight: 700; border-color: var(--line); border-radius: 4px 4px 0 0; position: relative; top: 1px; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 12px; }
  th, td { border: 1px solid var(--line); padding: 3px 7px; white-space: nowrap; text-align: left; }
  th { background: #eef1f5; position: sticky; top: 0; cursor: default; }
  th.sortable { cursor: pointer; }
  th.sortable:hover { background: #dfe4ea; }
  tfoot td { background: #f2f4f7; font-weight: 700; }
  tr:nth-child(even) td { background: #fafbfc; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.company { max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-bottom: 10px; }
  .card { background: #f6f7f9; border: 1px solid var(--line); border-radius: 4px; padding: 8px 10px; }
  .card .label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .card .value { font-size: 17px; font-weight: 700; margin-top: 2px; }
  .card .note { font-size: 10.5px; color: var(--muted); margin-top: 1px; }
  .section { margin-bottom: 14px; }
  .section h3 { font-size: 12.5px; margin: 0 0 6px; color: var(--navy2); display: flex; align-items: center; gap: 8px; }
  .bars { display: flex; flex-direction: column; gap: 3px; }
  .bar-row { display: grid; grid-template-columns: 170px 1fr 150px; gap: 6px; align-items: center; font-size: 11.5px; }
  .bar-row .label { text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { background: #eef1f5; border: 1px solid var(--line); border-radius: 2px; height: 14px; overflow: hidden; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, var(--navy2), #4f7bc0); }
  .bar-fill.alt { background: linear-gradient(90deg, #c08a2d, #e0b45f); }
  .bar-fill.neg { background: linear-gradient(90deg, #a13333, #d07070); }
  .bar-row .val { color: var(--muted); white-space: nowrap; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
  @media (max-width: 1100px) { .grid3 { grid-template-columns: 1fr; } }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  .muted { color: var(--muted); }
  .small { font-size: 10.5px; }
  .pos { color: var(--good); }
  .neg { color: var(--bad); }
  .empty { padding: 20px; color: var(--muted); }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 10.5px; background: #eef1f5; border: 1px solid var(--line); margin-right: 4px; }
  .rank { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10.5px; background: #eef1f5; border: 1px solid var(--line); margin: 2px 4px 2px 0; }
  .rank b { color: var(--navy2); }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 10.5px; border: 1px solid var(--line); background: #eef1f5; }
  .badge.good { background: #e7f4ec; border-color: #b9dcc6; color: var(--good); }
  .badge.warn { background: #fbf3e3; border-color: #e8d3a8; color: #8a6417; }
  .badge.bad { background: #f8e8e8; border-color: #e5bcbc; color: var(--bad); }
  .chart { background: #fff; border: 1px solid var(--line); border-radius: 4px; padding: 6px 8px; }
  .chart .title { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); display: flex; justify-content: space-between; }
  .chart svg { width: 100%; height: 74px; display: block; }
  .chart .last { font-size: 13px; font-weight: 700; color: var(--navy2); }
  .cov-row { display: grid; grid-template-columns: 130px 1fr 44px; gap: 6px; align-items: center; font-size: 11px; margin-bottom: 3px; }
  .cov-track { background: #eef1f5; border: 1px solid var(--line); border-radius: 2px; height: 10px; overflow: hidden; }
  .cov-fill { height: 100%; background: #6a8fc7; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
  .filters input { padding: 3px 6px; border: 1px solid var(--line); border-radius: 3px; font-size: 11.5px; width: 110px; }
  .profile { background: #f8f9fb; border: 1px solid var(--line); border-radius: 4px; padding: 10px; margin-bottom: 12px; line-height: 1.45; }
  .profile .desc { margin-top: 6px; color: #333; }
  .linkish { color: var(--navy2); cursor: pointer; text-decoration: underline; }
  .asof { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
  /* GitHub-style diff */
  .diff-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
  .diff-bar select { padding: 4px 6px; border: 1px solid var(--line); border-radius: 3px; font-size: 12px; background: #fff; }
  .diff-bar input { padding: 3px 6px; border: 1px solid var(--line); border-radius: 3px; font-size: 11.5px; width: 140px; }
  .diff-summary { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; font-size: 12px; margin-bottom: 8px; padding: 6px 10px; background: #f6f7f9; border: 1px solid var(--line); border-radius: 4px; }
  .diff-summary .add { color: var(--good); font-weight: 700; }
  .diff-summary .rem { color: var(--bad); font-weight: 700; }
  .diff-summary .mod { color: #b07a12; font-weight: 700; }
  .diff-layout { display: grid; grid-template-columns: 340px 1fr; gap: 10px; align-items: start; }
  .diff-list { border: 1px solid var(--line); border-radius: 4px; max-height: 64vh; overflow: auto; background: #fff; }
  .diff-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #edf0f3; cursor: pointer; font-size: 12px; }
  .diff-item:hover { background: #f5f7fa; }
  .diff-item.active { background: #e8effa; box-shadow: inset 3px 0 0 var(--navy2); }
  .diff-item .sign { width: 14px; font-weight: 700; text-align: center; flex-shrink: 0; }
  .diff-item .nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .diff-item .amt { color: var(--muted); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .sign.add { color: var(--good); }
  .sign.rem { color: var(--bad); }
  .sign.mod { color: #b07a12; }
  .diff-detail { border: 1px solid var(--line); border-radius: 4px; background: #fff; min-height: 220px; overflow: hidden; }
  .diff-detail .hd { padding: 8px 10px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; }
  .diff-detail .hd .t { font-weight: 700; font-size: 13px; }
  .diff-pane { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11.5px; max-height: 56vh; overflow: auto; }
  .diff-line { display: flex; align-items: stretch; min-height: 20px; }
  .diff-line .gutter { width: 24px; text-align: center; user-select: none; color: #8a929c; background: #f7f8fa; border-right: 1px solid #edf0f3; flex-shrink: 0; }
  .diff-line .txt { padding: 1px 8px; white-space: pre-wrap; flex: 1; }
  .diff-line.minus .txt { background: #fdecec; color: #8a1f1f; }
  .diff-line.minus .gutter { background: #f8dede; color: #8a1f1f; }
  .diff-line.plus .txt { background: #e8f5ec; color: #1d6b3f; }
  .diff-line.plus .gutter { background: #d7efdf; color: #1d6b3f; }
  .diff-line.ctx .txt { color: #555; }
  .diff-line .lab { display: inline-block; min-width: 150px; color: #6b7480; }
  .diff-split { width: 100%; }
  .diff-split th, .diff-split td { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11.5px; padding: 2px 8px; border-bottom: 1px solid #edf0f3; vertical-align: top; white-space: pre-wrap; word-break: break-word; }
  .diff-split th { background: #f2f4f7; position: static; }
  .diff-split td.before.changed { background: #fdecec; color: #8a1f1f; }
  .diff-split td.after.changed { background: #e8f5ec; color: #1d6b3f; }
  .diff-split td.field { color: #6b7480; width: 150px; }
  .diff-empty { padding: 16px; color: var(--muted); }
  .chip-btn { border: 1px solid var(--line); background: #f6f7f9; border-radius: 12px; padding: 2px 10px; font-size: 11px; cursor: pointer; color: var(--text); }
  .chip-btn:hover { background: #e8ebef; }
  .chip-btn.active { background: var(--navy2); color: #fff; border-color: var(--navy2); }
  @media (max-width: 900px) { .diff-layout { grid-template-columns: 1fr; } }
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
    <nav>
      <button id="nav-bdc" class="active">BDC View</button>
      <button id="nav-companies">Companies</button>
    </nav>
  </header>
  <div class="layout">
    <aside class="window sidebar">
      <div class="titlebar"><span id="sidebar-title">BDCs</span><span class="meta" id="ticker-count"></span></div>
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
        <button class="btn" id="btn-export" title="Export the visible table(s) as CSV">Export CSV</button>
        <button class="btn" id="btn-link" title="Copy a shareable link to this view">Copy link</button>
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
  companies: null,
  companyId: null,
  company: null,
  mode: 'bdc',
  ticker: null,
  period: null,
  analytics: null,
  financials: null,
  profile: null,
  holdings: null,
  tab: 'overview',
  sortKey: null,
  sortDir: 1,
  peerFilters: { q: '', minFv: '', maxDe: '', minFirstLien: '' },
  diff: { before: null, after: null, cacheKey: null, computed: null, selected: null, view: 'unified', filter: 'all', minDelta: '', search: '' },
};

// ------------------------------------------------------------------ utils
function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
}
function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(String(v).replace(/[,$%()×x]/g, ''));
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
// Company exposures are in millions.
function fmtM(v) {
  const n = num(v);
  if (n === null) return '—';
  if (Math.abs(n) >= 1000) return '$' + (n / 1000).toFixed(2) + 'B';
  return '$' + n.toFixed(1) + 'M';
}
function fmtPct(v, digits) {
  const n = num(v);
  if (n === null) return '—';
  return n.toFixed(digits === undefined ? 1 : digits) + '%';
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
function signed(v, digits) {
  const n = num(v);
  if (n === null) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(digits === undefined ? 1 : digits);
}
function csvEscape(v) {
  const s = String(v ?? '');
  return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 100);
}

// ------------------------------------------------------------------ deep links
function updateHash() {
  let h;
  if (state.mode === 'companies') h = '#/companies' + (state.companyId ? '/' + state.companyId : '');
  else if (state.tab === 'peers') h = '#/peers';
  else if (state.tab === 'changes') {
    h = '#/bdc/' + (state.ticker || '') + '/changes';
    if (state.diff && state.diff.before) h += '/' + state.diff.before;
    if (state.diff && state.diff.after) h += '/' + state.diff.after;
  } else h = '#/bdc/' + (state.ticker || '') + '/' + state.tab + (state.period ? '/' + state.period : '');
  if (location.hash !== h) history.replaceState(null, '', h);
}
function applyHash() {
  const parts = location.hash.replace(/^#\\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'companies') {
    state.mode = 'companies';
    updateModeChrome();
    renderSidebar();
    if (parts[1]) selectCompany(parts[1], true);
    else renderMain();
    return;
  }
  state.mode = 'bdc';
  updateModeChrome();
  renderSidebar();
  if (parts[0] === 'peers') {
    state.tab = 'peers';
    if (!state.ticker) {
      const names = Object.keys((state.index && state.index.tickers) || {}).sort();
      if (names.length) selectTicker(names[0], state.index.tickers[names[0]], null, true);
    }
    renderTabs(); updateModeChrome(); renderMain();
    return;
  }
  if (parts[0] === 'bdc' && parts[1] && state.index && state.index.tickers[parts[1]]) {
    state.tab = TABS.some(t => t[0] === parts[2]) ? parts[2] : 'overview';
    if (state.tab === 'changes') {
      state.diff.before = parts[3] || null;
      state.diff.after = parts[4] || null;
    }
    selectTicker(parts[1], state.index.tickers[parts[1]], parts[4] || parts[3] || null, true);
    return;
  }
  const names = Object.keys((state.index && state.index.tickers) || {}).sort();
  if (names.length) { state.tab = 'overview'; selectTicker(names[0], state.index.tickers[names[0]], null, false); }
}

// ------------------------------------------------------------------ loading
async function loadTickers() {
  const res = await fetch('/api/tickers');
  state.index = await res.json();
  const pRes = await fetch('/api/profiles');
  state.profiles = await pRes.json();
  renderSidebar();
  applyHash();
}
function renderSidebar() {
  if (state.mode === 'companies') return renderCompanySidebar();
  const tickers = state.index.tickers || {};
  const q = ($('ticker-search').value || '').toLowerCase();
  const names = Object.keys(tickers).sort();
  const list = $('ticker-list');
  list.innerHTML = '';
  $('sidebar-title').textContent = 'BDCs';
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

async function renderCompanySidebar() {
  const list = $('ticker-list');
  $('sidebar-title').textContent = 'Companies';
  if (!state.companies) {
    list.innerHTML = '<div class="empty">Loading companies…</div>';
    const res = await fetch('/api/companies');
    const data = await res.json();
    state.companies = data.companies || [];
    state.companiesById = {};
    for (const c of state.companies) state.companiesById[c.company_id] = c;
  }
  const q = ($('ticker-search').value || '').toLowerCase();
  let rows = state.companies;
  if (q) rows = rows.filter(c => (c.company_name || '').toLowerCase().indexOf(q) >= 0);
  rows = rows.slice().sort((a, b) => (num(b.total_exposure_millions) || 0) - (num(a.total_exposure_millions) || 0));
  $('ticker-count').textContent = rows.length + ' companies';
  list.innerHTML = '';
  const slice = rows.slice(0, 400);
  for (const c of slice) {
    const btn = document.createElement('button');
    btn.className = 'ticker' + (c.company_id === state.companyId ? ' active' : '');
    btn.innerHTML = '<div><b>' + esc(c.company_name || c.company_id) + '</b></div>' +
      '<div class="name">' + fmtM(c.total_exposure_millions) + ' · ' + esc(c.num_bdcs_invested || '') + ' BDCs</div>';
    btn.onclick = () => selectCompany(c.company_id);
    list.appendChild(btn);
  }
  if (!slice.length) list.innerHTML = '<div class="empty">No match.</div>';
  else if (rows.length > slice.length) list.insertAdjacentHTML('beforeend', '<div class="muted small" style="padding:4px">Showing top ' + slice.length + ' of ' + rows.length + ' (use search).</div>');
}

const TABS = [
  ['overview', 'Overview'],
  ['holdings', 'Holdings'],
  ['types', 'Loan Types'],
  ['sectors', 'Sectors'],
  ['rates', 'Rates & Structure'],
  ['vintage', 'Vintage & Maturity'],
  ['changes', 'QoQ Changes'],
  ['financials', 'Financials'],
  ['peers', 'Peers'],
];
function renderTabs() {
  const el = $('tabs');
  el.innerHTML = '';
  for (const t of TABS) {
    const id = t[0], label = t[1];
    const b = document.createElement('button');
    b.className = 'tab' + (id === state.tab ? ' active' : '');
    b.textContent = label;
    b.onclick = () => {
      state.tab = id;
      if (id !== 'holdings') state.holdings = null;
      renderTabs(); updateModeChrome(); updateHash(); renderMain();
    };
    el.appendChild(b);
  }
}
function updateModeChrome() {
  const isBdc = state.mode === 'bdc';
  $('tabs').style.display = isBdc ? '' : 'none';
  $('nav-bdc').className = isBdc ? 'active' : '';
  $('nav-companies').className = isBdc ? '' : 'active';
  const showPeriod = isBdc && state.ticker && (state.tab === 'holdings' || state.tab === 'types' || state.tab === 'sectors' || state.tab === 'rates' || state.tab === 'vintage');
  $('period-select').style.display = showPeriod ? '' : 'none';
  $('filter').style.display = isBdc && state.tab === 'holdings' ? '' : 'none';
  $('ticker-search').placeholder = isBdc ? 'Filter by ticker or name…' : 'Search companies…';
}
function setMode(mode, silent) {
  state.mode = mode;
  if (mode === 'companies') { state.company = null; state.companyId = state.companyId || null; }
  updateModeChrome();
  renderSidebar();
  if (mode === 'bdc') {
    if (!state.ticker) {
      const names = Object.keys((state.index && state.index.tickers) || {}).sort();
      if (names.length) { selectTicker(names[0], state.index.tickers[names[0]], null, silent); return; }
    }
    const prof = state.profiles[state.ticker] || {};
    $('win-title').textContent = state.ticker + ' — ' + (prof.name || state.ticker);
    if (!silent) updateHash();
    renderMain();
  } else {
    if (!silent) updateHash();
    renderMain();
  }
}

async function selectTicker(ticker, info, period, fromHash) {
  const prevTicker = state.ticker;
  state.mode = 'bdc';
  state.ticker = ticker;
  if (prevTicker && prevTicker !== ticker) {
    const keepBefore = state.diff.before;
    const keepAfter = state.diff.after;
    state.diff = { before: keepBefore, after: keepAfter, cacheKey: null, computed: null, selected: null, view: state.diff.view, filter: 'all', minDelta: '', search: '' };
  }
  const periods = (info && info.periods) || [];
  state.period = period || (info && info.latest) || (periods.length ? periods[periods.length - 1] : null);
  state.analytics = null; state.financials = null; state.profile = null; state.holdings = null;
  const prof = state.profiles[ticker] || {};
  $('win-title').textContent = ticker + ' — ' + (prof.name || ticker);
  $('win-meta').textContent = '';
  renderPeriodSelect(info);
  renderTabs();
  updateModeChrome();
  $('status').textContent = 'Loading ' + ticker + '…';
  const [aRes, fRes, pRes] = await Promise.all([
    fetch('/api/tickers/' + ticker + '/analytics'),
    fetch('/api/tickers/' + ticker + '/financials'),
    fetch('/api/tickers/' + ticker + '/profile'),
  ]);
  state.analytics = aRes.ok ? await aRes.json() : null;
  state.financials = fRes.ok ? await fRes.json() : null;
  state.profile = pRes.ok ? await pRes.json() : null;
  if (!fromHash) updateHash();
  renderSidebar();
  renderMain();
  $('status').textContent = ticker + ' · ' + (info.row_count || 0).toLocaleString() + ' holdings across ' + periods.length + ' filings';
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

async function selectCompany(id, fromHash) {
  state.mode = 'companies';
  state.companyId = id;
  state.company = null;
  updateModeChrome();
  $('win-title').textContent = (state.companiesById && state.companiesById[id] && state.companiesById[id].company_name) || 'Company';
  $('win-meta').textContent = '';
  $('status').textContent = 'Loading company…';
  const res = await fetch('/api/companies/' + encodeURIComponent(id));
  state.company = res.ok ? await res.json() : null;
  if (!fromHash) updateHash();
  renderSidebar();
  renderMain();
}

// ------------------------------------------------------------------ main render
function renderMain() {
  const el = $('main');
  if (state.mode === 'companies') return renderCompany(el);
  if (!state.ticker) { el.innerHTML = '<div class="empty">Select a BDC from the list.</div>'; return; }
  if (state.tab === 'overview') renderOverview(el);
  else if (state.tab === 'holdings') renderHoldings(el);
  else if (state.tab === 'types') renderBreakdown(el, 'types', 'Loan Types');
  else if (state.tab === 'sectors') renderBreakdown(el, 'sectors', 'Sectors');
  else if (state.tab === 'rates') renderRates(el);
  else if (state.tab === 'vintage') renderVintage(el);
  else if (state.tab === 'changes') renderChanges(el);
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
function previousPeriodData() {
  if (!state.analytics || !state.analytics.periods) return null;
  const periods = state.analytics.periods;
  const cur = currentPeriodData();
  const i = cur ? periods.indexOf(cur) : periods.length - 1;
  return i > 0 ? periods[i - 1] : null;
}
function latestDerived(metric) {
  const node = (state.financials && state.financials.derived || {})[metric];
  if (!node || !node.series) return null;
  const ends = Object.keys(node.series);
  if (!ends.length) return null;
  return node.series[ends[ends.length - 1]].val;
}
function derivedSeries(metric) {
  const node = (state.financials && state.financials.derived || {})[metric];
  if (!node || !node.series) return [];
  return Object.keys(node.series).sort().map(end => ({ label: end.slice(0, 7), value: node.series[end].val }));
}
function latestStatement(label) {
  const node = (state.financials && state.financials.statements && state.financials.statements.balance_sheet || {})[label];
  if (!node || !node.series) return null;
  const ends = Object.keys(node.series).sort();
  if (!ends.length) return null;
  return node.series[ends[ends.length - 1]].val;
}
async function ensurePeers() {
  if (state.peers) return state.peers;
  try {
    const res = await fetch('/api/peers');
    state.peers = res.ok ? await res.json() : { peers: [], medians: {}, sector_medians: [] };
  } catch (e) {
    state.peers = { peers: [], medians: {}, sector_medians: [] };
  }
  return state.peers;
}
function peerRow(ticker) {
  const peers = (state.peers && state.peers.peers) || [];
  return peers.find(p => p.ticker === ticker) || null;
}
function rankInfo(value, key, higherIsBetter) {
  const peers = (state.peers && state.peers.peers) || [];
  const vals = peers.map(p => p[key]).filter(v => v !== null && v !== undefined);
  if (value === null || value === undefined || !vals.length) return null;
  const better = higherIsBetter === false ? vals.filter(v => v < value).length + 1 : vals.filter(v => v > value).length + 1;
  return { rank: better, total: vals.length };
}

// ------------------------------------------------------------------ overview
function card(label, value, note) {
  return '<div class="card"><div class="label">' + esc(label) + '</div><div class="value">' + value + '</div><div class="note">' + esc(note || '') + '</div></div>';
}
function row2(a, b) {
  return '<tr><td>' + esc(a) + '</td><td class="num">' + b + '</td></tr>';
}
function lineChart(points, opts) {
  const o = opts || {};
  const vals = points.map(p => num(p.value)).filter(v => v !== null);
  if (vals.length < 2) return '<div class="chart"><div class="title">' + esc(o.title || '') + '</div><div class="muted small">Not enough history.</div></div>';
  const w = 300, h = 74, pad = 6;
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = max - min || 1;
  const step = (w - pad * 2) / (points.length - 1);
  let path = '';
  let dots = '';
  points.forEach((p, i) => {
    const v = num(p.value);
    if (v === null) return;
    const x = pad + i * step;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    path += (path ? ' L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
    dots += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="1.6" fill="#24457c"><title>' + esc(p.label) + ': ' + (o.fmt ? o.fmt(v) : v) + '</title></circle>';
  });
  const last = points[points.length - 1];
  return '<div class="chart"><div class="title"><span>' + esc(o.title || '') + '</span><span class="last">' + (o.fmt ? o.fmt(num(last.value)) : last.value) + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
    '<path d="' + path + '" fill="none" stroke="#24457c" stroke-width="1.8" />' + dots +
    '</svg><div class="small muted">' + esc(points[0].label) + ' → ' + esc(last.label) + ' · range ' + (o.fmt ? o.fmt(min) + '–' + o.fmt(max) : min + '–' + max) + '</div></div>';
}
function coverageBar(label, pct) {
  const p = num(pct) || 0;
  return '<div class="cov-row"><span>' + esc(label) + '</span><div class="cov-track"><div class="cov-fill" style="width:' + Math.min(100, p).toFixed(1) + '%"></div></div><span class="num">' + p.toFixed(0) + '%</span></div>';
}
async function renderOverview(el) {
  await ensurePeers();
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
  const equity = latestStatement("Stockholders' Equity") !== null ? latestStatement("Stockholders' Equity") : latestStatement('Net Assets');
  const finMeta = (state.analytics.meta && state.analytics.meta.financials) || {};
  const finLatest = (state.financials && state.financials.periods && state.financials.periods.length) ? state.financials.periods[state.financials.periods.length - 1] : null;
  let html = '<div class="asof">Holdings as of <b>' + esc(p.filing_date) + '</b> (period end ' + esc(p.period_end) + ')' +
    (finLatest ? ' · Financials latest <b>' + esc(finLatest.end) + '</b> (filed ' + esc(finLatest.filed || '—') + ')' : '') +
    (finMeta.balance_check_ok === true ? ' · <span class="badge good">balance ✓</span>' : (finMeta.balance_check_ok === false ? ' · <span class="badge bad">balance check failed</span>' : '')) +
    '</div>';

  html += '<div class="cards">' +
    card('Portfolio Fair Value', fmtK(t.fair_value), p.filing_date) +
    card('Principal', fmtK(t.principal), 'par / principal') +
    card('Holdings', fmtNum(t.rows), t.companies + ' companies') +
    card('Wtd Avg Cash Rate', fmtRate(t.weighted_avg_cash_rate), 'FV-weighted') +
    card('Wtd Avg Spread', fmtRate(t.weighted_avg_spread), 'over reference') +
    card('NAV / Share', nav === null ? '—' : '$' + num(nav).toFixed(2), prNav === null ? '' : (prNav >= 0 ? '+' : '') + prNav.toFixed(1) + '% vs price') +
    card('Debt / Equity', d2e === null ? '—' : num(d2e).toFixed(2) + '×', 'coverage ' + (cov === null ? '—' : num(cov).toFixed(2) + '×')) +
    card('Unrealized G/L', t.unrealized_gl_pct === null || t.unrealized_gl_pct === undefined ? '—' : signed(t.unrealized_gl_pct) + '%',
      'on ' + fmtPct(t.cost_coverage_pct, 0) + ' of FV at cost') +
    card('Floating Rate', fmtPct(t.floating_pct), 'PIK ' + fmtPct(t.pik_share_pct) + ' of FV') +
    card('Top 10 Concentration', fmtPct(t.top10_pct), 'of fair value') +
  '</div>';

  // Peer rank chips
  const peers = state.peers && state.peers.peers;
  if (peers && peers.length) {
    const pr = peerRow(state.ticker) || {};
    const r1 = rankInfo(pr.first_lien_pct, 'first_lien_pct', true);
    const r2 = rankInfo(pr.weighted_avg_cash_rate, 'weighted_avg_cash_rate', true);
    const r3 = rankInfo(pr.debt_to_equity, 'debt_to_equity', false);
    const r4 = rankInfo(pr.median_vintage_year, 'median_vintage_year', true);
    const chips = [];
    if (r1) chips.push('<span class="rank">First lien <b>#' + r1.rank + '</b> of ' + r1.total + '</span>');
    if (r2) chips.push('<span class="rank">Avg cash rate <b>#' + r2.rank + '</b> of ' + r2.total + '</span>');
    if (r3) chips.push('<span class="rank">Leverage <b>#' + r3.rank + '</b> of ' + r3.total + ' (low = better)</span>');
    if (r4) chips.push('<span class="rank">Median vintage <b>' + pr.median_vintage_year + '</b></span>');
    html += '<div class="section"><h3>Peer context</h3>' + chips.join('') + '<div class="small muted" style="margin-top:4px">Ranks across ' + peers.length + ' BDCs at the latest filing per BDC. <span class="linkish" id="lnk-peers">Open peer table →</span></div></div>';
  }

  // Trends
  const periods = (state.analytics.periods || []).slice(-16);
  const fvPoints = periods.map(x => ({ label: x.filing_date.slice(0, 7), value: x.totals.fair_value }));
  const yieldPoints = periods.map(x => ({ label: x.filing_date.slice(0, 7), value: x.totals.weighted_avg_cash_rate }));
  html += '<div class="section"><h3>Trends</h3><div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(250px,1fr))">';
  html += lineChart(fvPoints, { title: 'Portfolio fair value', fmt: fmtK });
  html += lineChart(yieldPoints, { title: 'Weighted avg cash rate', fmt: v => v.toFixed(2) + '%' });
  html += lineChart(derivedSeries('debt_to_equity').slice(-16), { title: 'Debt / equity', fmt: v => v.toFixed(2) + '×' });
  html += lineChart(derivedSeries('nav_per_share').slice(-16), { title: 'NAV per share', fmt: v => '$' + v.toFixed(2) });
  html += lineChart(derivedSeries('nii_per_share').slice(-16), { title: 'NII per share', fmt: v => '$' + v.toFixed(2) });
  html += lineChart(derivedSeries('asset_coverage').slice(-16), { title: 'Asset coverage', fmt: v => v.toFixed(2) + '×' });
  html += '</div></div>';

  // Composition + snapshot
  html += '<div class="grid2"><div class="section">';
  html += '<h3>Portfolio snapshot — ' + p.filing_date + '</h3>';
  const types = (p.types || []).filter(x => x.pct > 0).slice(0, 6);
  html += bars(types, 'type', 'fair_value', 'pct', fmtK);
  html += '</div><div class="section">';
  html += '<h3>Financial snapshot</h3><table><tbody>';
  html += row2('Total Assets', fmtMoney(assets));
  html += row2('Total Debt', fmtMoney(debt));
  html += row2('Total Equity', fmtMoney(equity));
  html += row2('NAV per Share', nav === null ? '—' : '$' + num(nav).toFixed(2));
  html += row2('Debt / Equity', d2e === null ? '—' : num(d2e).toFixed(2) + '×');
  html += row2('Asset Coverage', cov === null ? '—' : num(cov).toFixed(2) + '×');
  if (price !== null) html += row2('Market Price', '$' + price.toFixed(2));
  html += '</tbody></table></div></div>';

  // Data & freshness
  const covg = p.coverage || {};
  html += '<div class="section"><h3>Data coverage <span class="small muted">(share of fair value with the field populated)</span></h3><div class="grid3">';
  html += '<div>' + coverageBar('Investment type', covg.investment_type) + coverageBar('Industry', covg.industry) + coverageBar('Acquisition date', covg.acquisition_date) + coverageBar('Maturity date', covg.maturity_date) + '</div>';
  html += '<div>' + coverageBar('Reference rate', covg.reference_rate) + coverageBar('Spread', covg.spread) + coverageBar('Cash rate', covg.cash_rate) + coverageBar('PIK rate > 0', covg.pik_rate) + '</div>';
  html += '<div>' + coverageBar('Amortized cost', covg.amortized_cost) + coverageBar('Principal', covg.principal) + coverageBar('Floor rate > 0', covg.floor) +
    '<div class="small muted" style="margin-top:6px">Financials: ' + (finMeta.period_count || 0) + ' quarters' + (finMeta.has_cash_flow ? ' incl. cash flow' : ' (no cash flow)') +
    (finMeta.balance_check_ok === false ? ' · <span class="neg">balance check failed</span>' : '') + '</div></div>';
  html += '</div></div>';
  el.innerHTML = html;
  const lnk = $('lnk-peers');
  if (lnk) lnk.onclick = () => { state.tab = 'peers'; renderTabs(); updateModeChrome(); updateHash(); renderMain(); };
}

function bars(items, labelKey, valKey, pctKey, fmt, cls) {
  if (!items || !items.length) return '<div class="empty">No data.</div>';
  const max = Math.max.apply(null, items.map(i => Math.abs(num(i[pctKey]) || 0)));
  let html = '<div class="bars">';
  for (const it of items) {
    const pct = num(it[pctKey]) || 0;
    const w = max > 0 ? Math.max(1.5, Math.abs(pct) / max * 100) : 0;
    html += '<div class="bar-row"><span class="label" title="' + esc(it[labelKey]) + '">' + esc(it[labelKey]) + '</span>' +
      '<div class="bar-track"><div class="bar-fill ' + (cls || '') + '" style="width:' + w.toFixed(1) + '%"></div></div>' +
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
    for (const c of cols) {
      const key = c[0], cls = c[2];
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
async function renderBreakdown(el, kind, title) {
  if (kind === 'sectors') await ensurePeers();
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const list = p[kind] || [];
  const key = kind === 'types' ? 'type' : 'sector';
  let html = '<div class="section"><h3>' + title + ' — ' + p.filing_date + '</h3>' + bars(list, key, 'fair_value', 'pct', fmtK) + '</div>';
  if (kind === 'types') {
    html += '<div class="section"><h3>Details</h3><table><thead><tr><th>Type</th><th class="num">Fair Value</th><th class="num">%</th><th class="num">Positions</th><th class="num">Principal</th><th class="num">Avg FV / position</th><th class="num">Unrealized G/L</th><th class="num">G/L %</th></tr></thead><tbody>';
    for (const it of list) {
      const avg = it.count ? it.fair_value / it.count : null;
      html += '<tr><td>' + esc(it.type) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td><td class="num">' + fmtNum(it.count) + '</td><td class="num">' + fmtK(it.principal) + '</td><td class="num">' + (avg === null ? '—' : fmtK(avg)) + '</td>' +
        '<td class="num">' + (it.unrealized_gl === null || it.unrealized_gl === undefined ? '—' : fmtK(it.unrealized_gl)) + '</td>' +
        '<td class="num ' + (num(it.unrealized_gl_pct) < 0 ? 'neg' : 'pos') + '">' + (it.unrealized_gl_pct === null || it.unrealized_gl_pct === undefined ? '—' : signed(it.unrealized_gl_pct) + '%') + '</td></tr>';
    }
    html += '</tbody></table></div>';
  } else {
    const medians = {};
    for (const s of ((state.peers && state.peers.sector_medians) || [])) medians[s.sector] = s;
    html += '<div class="section"><h3>Details</h3><table><thead><tr><th>Sector</th><th class="num">Fair Value</th><th class="num">%</th><th class="num">Peer median</th><th class="num">Tilt</th><th class="num">Positions</th><th class="num">Unrealized G/L</th><th class="num">G/L %</th></tr></thead><tbody>';
    for (const it of list) {
      const med = medians[it.sector];
      const tilt = med ? it.pct - med.median_pct : null;
      html += '<tr><td>' + esc(it.sector) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td>' +
        '<td class="num">' + (med ? fmtPct(med.median_pct) : '—') + '</td>' +
        '<td class="num ' + (tilt !== null && tilt < 0 ? 'neg' : 'pos') + '">' + (tilt === null ? '—' : signed(tilt) + ' pts') + '</td>' +
        '<td class="num">' + fmtNum(it.count) + '</td>' +
        '<td class="num">' + (it.unrealized_gl === null || it.unrealized_gl === undefined ? '—' : fmtK(it.unrealized_gl)) + '</td>' +
        '<td class="num ' + (num(it.unrealized_gl_pct) < 0 ? 'neg' : 'pos') + '">' + (it.unrealized_gl_pct === null || it.unrealized_gl_pct === undefined ? '—' : signed(it.unrealized_gl_pct) + '%') + '</td></tr>';
    }
    html += '</tbody></table><div class="small muted" style="margin-top:4px">Peer median = median sector weight across BDCs reporting that sector. Tilt = this BDC minus peer median (percentage points).</div></div>';
  }
  html += historyMatrix(kind);
  el.innerHTML = html;
}

function historyMatrix(kind) {
  const periods = (state.analytics.periods || []).slice(-8);
  const key = kind === 'types' ? 'type' : 'sector';
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
      html += '<td class="num" style="background:' + (v >= 20 ? '#e8effa' : 'transparent') + '">' + (v > 0 ? v.toFixed(1) : '·') + '</td>';
    }
    html += '</tr>';
  }
  return html + '</tbody></table></div>';
}

// ------------------------------------------------------------------ rates & structure
function renderRates(el) {
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const t = p.totals || {};
  const cov = p.coverage || {};
  let html = '<div class="cards">' +
    card('Floating Rate', fmtPct(t.floating_pct), 'of fair value') +
    card('Fixed Rate', fmtPct(t.fixed_pct), 'reported as fixed') +
    card('PIK Share', fmtPct(t.pik_share_pct), 'positions with PIK > 0') +
    card('Wtd Avg Spread', fmtRate(t.weighted_avg_spread), 'over reference') +
    card('Wtd Avg Floor', t.weighted_avg_floor === null || t.weighted_avg_floor === undefined ? '—' : fmtRate(t.weighted_avg_floor), 'where a floor is reported') +
    card('Reference Coverage', fmtPct(cov.reference_rate, 0), 'of fair value') +
  '</div>';
  html += '<div class="grid2">';
  html += '<div class="section"><h3>Rate type mix</h3>' + bars(p.rate_types || [], 'type', 'fair_value', 'pct', fmtK) +
    '<div class="small muted">Floating = reports SOFR/LIBOR/Prime/EURIBOR/etc. Not reported = no reference rate on the filing row (often equity or structured positions).</div></div>';
  html += '<div class="section"><h3>Reference rate mix</h3>' + bars(p.reference_rates || [], 'rate', 'fair_value', 'pct', fmtK) + '</div>';
  html += '</div>';
  html += '<div class="section"><h3>Spread distribution (FV-weighted)</h3>' + bars(p.spread_buckets || [], 'bucket', 'fair_value', 'pct', fmtK, 'alt') +
    '<div class="small muted">Spread reported for ' + fmtPct(cov.spread, 0) + ' of fair value; cash rate for ' + fmtPct(cov.cash_rate, 0) + '.</div></div>';
  html += '<div class="section"><h3>Coverage detail</h3><div class="grid3">' +
    '<div>' + coverageBar('Reference rate', cov.reference_rate) + coverageBar('Cash rate', cov.cash_rate) + '</div>' +
    '<div>' + coverageBar('Spread', cov.spread) + coverageBar('Floor > 0', cov.floor) + '</div>' +
    '<div>' + coverageBar('PIK rate > 0', cov.pik_rate) + '</div></div></div>';
  el.innerHTML = html;
}

// ------------------------------------------------------------------ vintage & maturity
function renderVintage(el) {
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const t = p.totals || {};
  const cov = p.coverage || {};
  let html = '<div class="cards">' +
    card('Unrealized G/L', t.unrealized_gl === null || t.unrealized_gl === undefined ? '—' : fmtK(t.unrealized_gl), 'vs amortized cost') +
    card('Unrealized G/L %', t.unrealized_gl_pct === null || t.unrealized_gl_pct === undefined ? '—' : signed(t.unrealized_gl_pct) + '%', 'on ' + fmtPct(t.cost_coverage_pct, 0) + ' of FV') +
    card('Vintage Coverage', fmtPct(t.vintage_coverage_pct), 'of fair value') +
    card('Maturity Coverage', fmtPct(cov.maturity_date, 0), 'of fair value') +
  '</div>';

  html += '<div class="section"><h3>Vintage — ' + p.filing_date + '</h3>' +
    bars(p.vintage_years || [], 'year', 'fair_value', 'pct', fmtK) + '</div>';
  html += '<div class="grid2">';
  html += '<div class="section"><h3>Portfolio age (years since acquisition)</h3>' + bars(p.age_buckets || [], 'bucket', 'fair_value', 'pct', fmtK) + '</div>';
  html += '<div class="section"><h3>Maturity wall (by year)</h3>' + bars(p.maturity_wall || [], 'year', 'fair_value', 'pct', fmtK, 'alt') + '</div>';
  html += '</div>';

  html += '<div class="section"><h3>Marks vs amortized cost</h3><table><thead><tr><th>Group</th><th class="num">Fair Value</th><th class="num">Unrealized G/L</th><th class="num">G/L %</th></tr></thead><tbody>';
  for (const it of (p.types || []).slice(0, 8)) {
    html += '<tr><td>' + esc(it.type) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + (it.unrealized_gl === null || it.unrealized_gl === undefined ? '—' : fmtK(it.unrealized_gl)) + '</td><td class="num ' + (num(it.unrealized_gl_pct) < 0 ? 'neg' : 'pos') + '">' + (it.unrealized_gl_pct === null || it.unrealized_gl_pct === undefined ? '—' : signed(it.unrealized_gl_pct) + '%') + '</td></tr>';
  }
  html += '</tbody></table><div class="small muted" style="margin-top:4px">By investment type. Cost coverage: ' + fmtPct(t.cost_coverage_pct, 0) + ' of fair value.</div></div>';

  html += '<div class="grid2">';
  html += '<div class="section"><h3>Top 5 gainers</h3><table><thead><tr><th>Company</th><th class="num">G/L</th><th class="num">Fair Value</th></tr></thead><tbody>';
  for (const g of (p.top_gainers || [])) html += '<tr><td>' + esc(g.company) + '</td><td class="num pos">' + fmtK(g.unrealized_gl) + '</td><td class="num">' + fmtK(g.fair_value) + '</td></tr>';
  html += '</tbody></table></div>';
  html += '<div class="section"><h3>Top 5 losers</h3><table><thead><tr><th>Company</th><th class="num">G/L</th><th class="num">Fair Value</th></tr></thead><tbody>';
  for (const g of (p.top_losers || [])) html += '<tr><td>' + esc(g.company) + '</td><td class="num neg">' + fmtK(g.unrealized_gl) + '</td><td class="num">' + fmtK(g.fair_value) + '</td></tr>';
  html += '</tbody></table></div></div>';

  html += '<div class="section"><h3>Top 10 companies by fair value</h3><table><thead><tr><th>Company</th><th class="num">Fair Value</th><th class="num">Unrealized G/L</th></tr></thead><tbody>';
  for (const c of (p.top_companies || [])) html += '<tr><td>' + esc(c.company) + '</td><td class="num">' + fmtK(c.fair_value) + '</td><td class="num">' + (c.unrealized_gl === null || c.unrealized_gl === undefined ? '—' : fmtK(c.unrealized_gl)) + '</td></tr>';
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

// ------------------------------------------------------------------ QoQ changes (GitHub-style diff)
function normName(name) {
  return String(name || '')
    .toLowerCase()
    .split('&').join(' and ')
    .replace(/[^a-z0-9 ]+/g, ' ')
    .split(' ').filter(Boolean).join(' ')
    .trim();
}
function aggregateCompanies(rows) {
  const map = new Map();
  for (const r of rows) {
    const key = normName(r.company_name) || '(unknown)';
    let c = map.get(key);
    if (!c) {
      c = { key, name: (r.company_name || '(unknown)').trim(), fv: 0, principal: 0, cost: 0,
            rateSum: 0, rateW: 0, spreadSum: 0, spreadW: 0, positions: 0, pik: 0,
            types: new Set(), industries: new Set(), maturities: new Set() };
      map.set(key, c);
    }
    const fv = num(r.fair_value) || 0;
    c.fv += fv;
    c.principal += num(r.principal_amount) || 0;
    c.cost += num(r.amortized_cost) || 0;
    c.positions += 1;
    const rate = num(r.cash_rate);
    if (rate !== null && rate > 0 && fv > 0) { c.rateSum += rate * fv; c.rateW += fv; }
    const spread = num(r.spread);
    if (spread !== null && spread > 0 && fv > 0) { c.spreadSum += spread * fv; c.spreadW += fv; }
    const pik = num(r.pik_rate);
    if (pik !== null && pik > 0) c.pik += 1;
    if (r.investment_type) c.types.add(r.investment_type.trim());
    if (r.industry) c.industries.add(r.industry.trim());
    const my = (r.maturity_date || '').slice(0, 4);
    if (my.length === 4 && !isNaN(Number(my))) c.maturities.add(my);
  }
  for (const c of map.values()) {
    c.rate = c.rateW ? c.rateSum / c.rateW : null;
    c.spread = c.spreadW ? c.spreadSum / c.spreadW : null;
  }
  return map;
}
function matRange(set) {
  const ys = Array.from(set).map(Number).filter(y => y > 1900).sort((a, b) => a - b);
  if (!ys.length) return '';
  const lo = ys[0], hi = ys[ys.length - 1];
  return lo === hi ? String(lo) : lo + '–' + hi;
}
function fmtRateOrDash(v) { return v === null || v === undefined ? '—' : Number(v).toFixed(2) + '%'; }
function fmtGl(v) { return v === null || v === undefined ? '—' : fmtK(v); }
function companySide(x) {
  if (!x) return null;
  return {
    fv: x.fv, principal: x.principal, cost: x.cost,
    gl: x.cost ? x.fv - x.cost : null,
    positions: x.positions, rate: x.rate, spread: x.spread,
    types: Array.from(x.types).join(', '),
    industries: Array.from(x.industries).join(', '),
    maturity: matRange(x.maturities),
    pikPositions: x.pik,
  };
}
function diffFields(before, after) {
  const A = companySide(before), B = companySide(after);
  const spec = [
    ['fv', 'Fair value', fmtK],
    ['principal', 'Principal', fmtK],
    ['cost', 'Amortized cost', fmtK],
    ['gl', 'Unrealized G/L', fmtGl],
    ['positions', 'Positions', fmtNum],
    ['rate', 'Wtd avg cash rate', fmtRateOrDash],
    ['spread', 'Wtd avg spread', fmtRateOrDash],
    ['types', 'Investment type', v => v || '—'],
    ['industries', 'Industry', v => v || '—'],
    ['maturity', 'Maturity range', v => v || '—'],
    ['pikPositions', 'PIK positions', fmtNum],
  ];
  return spec.map(s => {
    const av = A ? A[s[0]] : null, bv = B ? B[s[0]] : null;
    return { key: s[0], label: s[1], fmt: s[2], before: av, after: bv, changed: String(av === null || av === undefined ? '' : av) !== String(bv === null || bv === undefined ? '' : bv) };
  });
}
function computeDiff(beforeRows, afterRows) {
  const A = aggregateCompanies(beforeRows), B = aggregateCompanies(afterRows);
  const keys = new Set(Array.from(A.keys()).concat(Array.from(B.keys())));
  const out = [];
  let added = 0, removed = 0, modified = 0, unchanged = 0, totalBefore = 0, totalAfter = 0;
  for (const k of keys) {
    const a = A.get(k) || null, b = B.get(k) || null;
    const fields = diffFields(a, b);
    const deltaFv = (b ? b.fv : 0) - (a ? a.fv : 0);
    const changedCount = fields.filter(f => f.changed).length;
    let status = 'unchanged';
    if (!a) { status = 'added'; added++; }
    else if (!b) { status = 'removed'; removed++; }
    else if (changedCount) { status = 'modified'; modified++; }
    else unchanged++;
    totalBefore += a ? a.fv : 0;
    totalAfter += b ? b.fv : 0;
    out.push({ key: k, name: (b ? b.name : a.name), status, before: a, after: b, fields, deltaFv, changedCount });
  }
  out.sort((x, y) => Math.abs(y.deltaFv) - Math.abs(x.deltaFv));
  return { companies: out, summary: { added, removed, modified, unchanged, totalBefore, totalAfter, net: totalAfter - totalBefore } };
}
function periodOptions(selected) {
  const dates = ((state.analytics && state.analytics.periods) || []).map(p => p.filing_date).slice().reverse();
  return dates.map(d => '<option value="' + d + '"' + (d === selected ? ' selected' : '') + '>' + d + '</option>').join('');
}
function diffFiltered() {
  const d = state.diff.computed;
  if (!d) return [];
  let rows = d.companies;
  if (!state.diff.showUnchanged) rows = rows.filter(c => c.status !== 'unchanged');
  const f = state.diff.filter;
  if (f === 'added' || f === 'removed') rows = rows.filter(c => c.status === f);
  else if (f === 'increased') rows = rows.filter(c => c.status === 'modified' && c.deltaFv > 0);
  else if (f === 'decreased') rows = rows.filter(c => c.status === 'modified' && c.deltaFv < 0);
  const q = (state.diff.search || '').toLowerCase();
  if (q) rows = rows.filter(c => c.name.toLowerCase().indexOf(q) >= 0);
  const min = num(state.diff.minDelta);
  if (min) rows = rows.filter(c => Math.abs(c.deltaFv) >= min * 1000);
  return rows;
}
function statusBadge(status) {
  if (status === 'added') return '<span class="badge good">added</span>';
  if (status === 'removed') return '<span class="badge bad">removed</span>';
  if (status === 'modified') return '<span class="badge warn">modified</span>';
  return '<span class="badge">unchanged</span>';
}
function diffLine(sign, label, value, fmt) {
  const cls = sign === '+' ? 'plus' : sign === '-' ? 'minus' : 'ctx';
  return '<div class="diff-line ' + cls + '"><span class="gutter">' + (sign === ' ' ? '' : sign) + '</span><span class="txt"><span class="lab">' + esc(label) + '</span>' + esc(fmt(value)) + '</span></div>';
}
function renderDiffDetail(c) {
  if (!c) return '<div class="diff-empty">Select a company from the list.</div>';
  const view = state.diff.view;
  const side = c.after || c.before;
  let html = '<div class="hd"><div><div class="t">' + esc(c.name) + '</div><div class="small muted">' +
    esc(Array.from(side.types).join(', ')) + ' · ' + esc(Array.from(side.industries).join(', ')) + '</div></div>' +
    '<div class="small">' + statusBadge(c.status) + ' <b class="' + (c.deltaFv < 0 ? 'neg' : 'pos') + '">' + (c.deltaFv >= 0 ? '+' : '') + fmtK(c.deltaFv) + '</b></div></div>';
  html += '<div class="diff-bar" style="padding:6px 10px; margin:0; border-bottom:1px solid var(--line)">' +
    '<button class="chip-btn' + (view === 'unified' ? ' active' : '') + '" data-view="unified">Unified</button>' +
    '<button class="chip-btn' + (view === 'split' ? ' active' : '') + '" data-view="split">Split</button>' +
    '<span class="small muted">' + c.changedCount + ' field(s) changed</span></div>';
  if (view === 'split') {
    html += '<table class="diff-split"><thead><tr><th class="field">Field</th><th>Before (' + esc(state.diff.before) + ')</th><th>After (' + esc(state.diff.after) + ')</th></tr></thead><tbody>';
    for (const f of c.fields) {
      html += '<tr><td class="field">' + esc(f.label) + '</td>' +
        '<td class="before' + (f.changed && f.before !== null && f.before !== undefined ? ' changed' : '') + '">' + esc(f.before === null || f.before === undefined ? '—' : f.fmt(f.before)) + '</td>' +
        '<td class="after' + (f.changed && f.after !== null && f.after !== undefined ? ' changed' : '') + '">' + esc(f.after === null || f.after === undefined ? '—' : f.fmt(f.after)) + '</td></tr>';
    }
    html += '</tbody></table>';
  } else {
    html += '<div class="diff-pane">';
    for (const f of c.fields) {
      if (c.status === 'added') html += diffLine('+', f.label, f.after, f.fmt);
      else if (c.status === 'removed') html += diffLine('-', f.label, f.before, f.fmt);
      else if (f.changed) html += diffLine('-', f.label, f.before, f.fmt) + diffLine('+', f.label, f.after, f.fmt);
      else html += diffLine(' ', f.label, f.after, f.fmt);
    }
    html += '</div>';
  }
  return html;
}
function renderDiffView(el) {
  const d = state.diff.computed;
  if (!d) return;
  const s = d.summary;
  const rows = diffFiltered();
  if (!rows.some(c => c.key === state.diff.selected)) state.diff.selected = rows.length ? rows[0].key : null;
  let html = '<div class="diff-bar">' +
    '<span>Compare</span><select id="diff-before">' + periodOptions(state.diff.before) + '</select>' +
    '<b>→</b><select id="diff-after">' + periodOptions(state.diff.after) + '</select>' +
    '<button class="btn" id="diff-swap" title="Swap before and after">⇄ Swap</button>' +
    '<span style="flex:1"></span>' +
    '<button class="btn" id="diff-export">Export diff CSV</button>' +
  '</div>';
  html += '<div class="diff-summary">' +
    '<span class="add">+' + s.added + ' added</span>' +
    '<span class="rem">−' + s.removed + ' removed</span>' +
    '<span class="mod">~' + s.modified + ' modified</span>' +
    '<span>net fair value <b class="' + (s.net < 0 ? 'neg' : 'pos') + '">' + fmtK(s.net) + '</b></span>' +
    '<span class="muted">' + fmtK(s.totalBefore) + ' → ' + fmtK(s.totalAfter) + '</span>' +
    '<span class="muted">' + s.unchanged + ' unchanged hidden</span>' +
  '</div>';
  html += '<div class="diff-bar">' +
    ['all', 'added', 'removed', 'increased', 'decreased'].map(f =>
      '<button class="chip-btn' + (state.diff.filter === f ? ' active' : '') + '" data-filter="' + f + '">' +
      f.charAt(0).toUpperCase() + f.slice(1) + '</button>').join('') +
    '<input id="diff-search" placeholder="Filter company…" value="' + esc(state.diff.search) + '">' +
    '<input id="diff-min" placeholder="Min |Δ| $M" value="' + esc(state.diff.minDelta) + '">' +
    '<label class="small muted" style="display:flex;align-items:center;gap:4px"><input type="checkbox" id="diff-unchanged"' + (state.diff.showUnchanged ? ' checked' : '') + ' style="width:auto"> show unchanged</label>' +
  '</div>';
  html += '<div class="diff-layout"><div class="diff-list" id="diff-list">';
  if (!rows.length) html += '<div class="empty">No companies match these filters.</div>';
  for (const c of rows) {
    const sign = c.status === 'added' ? '+' : c.status === 'removed' ? '−' : '~';
    const cls = c.status === 'added' ? 'add' : c.status === 'removed' ? 'rem' : 'mod';
    const delta = c.deltaFv;
    html += '<div class="diff-item' + (c.key === state.diff.selected ? ' active' : '') + '" data-key="' + esc(c.key) + '">' +
      '<span class="sign ' + cls + '">' + sign + '</span>' +
      '<span class="nm" title="' + esc(c.name) + '">' + esc(c.name) + '</span>' +
      '<span class="amt ' + (delta < 0 ? 'neg' : 'pos') + '">' + (delta >= 0 ? '+' : '') + fmtK(delta) + '</span></div>';
  }
  html += '</div><div class="diff-detail" id="diff-detail">' + renderDiffDetail(rows.find(c => c.key === state.diff.selected)) + '</div></div>';
  el.innerHTML = html;

  $('diff-before').onchange = (e) => {
    state.diff.before = e.target.value;
    state.diff.computed = null; state.diff.cacheKey = null;
    renderChanges(el);
  };
  $('diff-after').onchange = (e) => {
    state.diff.after = e.target.value;
    state.diff.computed = null; state.diff.cacheKey = null;
    renderChanges(el);
  };
  $('diff-swap').onclick = () => {
    const b = state.diff.before;
    state.diff.before = state.diff.after;
    state.diff.after = b;
    state.diff.computed = null; state.diff.cacheKey = null;
    renderChanges(el);
  };
  $('diff-export').onclick = exportDiffCsv;
  el.querySelectorAll('[data-filter]').forEach(b => {
    b.onclick = () => { state.diff.filter = b.getAttribute('data-filter'); renderDiffView(el); };
  });
  el.querySelectorAll('[data-view]').forEach(b => {
    b.onclick = () => { state.diff.view = b.getAttribute('data-view'); renderDiffView(el); };
  });
  el.querySelectorAll('.diff-item[data-key]').forEach(row => {
    row.onclick = () => { state.diff.selected = row.getAttribute('data-key'); renderDiffView(el); };
  });
  const refocus = (id, update) => {
    const input = $(id);
    if (!input) return;
    input.oninput = (e) => {
      update(e.target.value);
      renderDiffView(el);
      const again = $(id);
      if (again) { again.focus(); const v = again.value; again.value = ''; again.value = v; }
    };
  };
  refocus('diff-search', v => { state.diff.search = v; });
  refocus('diff-min', v => { state.diff.minDelta = v; });
  const unch = $('diff-unchanged');
  if (unch) unch.onchange = (e) => { state.diff.showUnchanged = e.target.checked; renderDiffView(el); };
}
function exportDiffCsv() {
  const d = state.diff.computed;
  if (!d) return;
  const NL = String.fromCharCode(10);
  const rows = diffFiltered();
  const lines = ['Company,Status,Before Fair Value,After Fair Value,Delta Fair Value,Changed Fields'];
  for (const c of rows) {
    lines.push([
      c.name, c.status,
      c.before ? c.before.fv : '', c.after ? c.after.fv : '', c.deltaFv,
      c.fields.filter(f => f.changed).map(f => f.label).join('; '),
    ].map(csvEscape).join(','));
  }
  downloadText(state.ticker + '-changes-' + state.diff.before + '-to-' + state.diff.after + '.csv', lines.join(NL));
}
async function renderChanges(el) {
  const periods = (state.analytics && state.analytics.periods) || [];
  if (!periods.length) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const dates = periods.map(p => p.filing_date);
  let after = (state.diff.after && dates.indexOf(state.diff.after) >= 0) ? state.diff.after
    : (state.period && dates.indexOf(state.period) >= 0 ? state.period : dates[dates.length - 1]);
  const ai = dates.indexOf(after);
  let before = (state.diff.before && dates.indexOf(state.diff.before) >= 0 && state.diff.before !== after)
    ? state.diff.before : dates[Math.max(0, ai - 1)];
  if (!before || before === after) {
    el.innerHTML = '<div class="empty">Need at least two filing periods to compare.</div>';
    return;
  }
  state.diff.before = before;
  state.diff.after = after;
  const cacheKey = state.ticker + '|' + before + '|' + after;
  if (state.diff.cacheKey !== cacheKey || !state.diff.computed) {
    el.innerHTML = '<div class="empty">Loading holdings for ' + esc(before) + ' and ' + esc(after) + '…</div>';
    const [bRes, aRes] = await Promise.all([
      fetch('/api/tickers/' + state.ticker + '/periods/' + before),
      fetch('/api/tickers/' + state.ticker + '/periods/' + after),
    ]);
    const bData = bRes.ok ? await bRes.json() : { rows: [] };
    const aData = aRes.ok ? await aRes.json() : { rows: [] };
    state.diff.computed = computeDiff(bData.rows || [], aData.rows || []);
    state.diff.cacheKey = cacheKey;
    if (!state.diff.selected || !state.diff.computed.companies.some(c => c.key === state.diff.selected)) {
      const first = state.diff.computed.companies.filter(c => c.status !== 'unchanged')[0] || state.diff.computed.companies[0];
      state.diff.selected = first ? first.key : null;
    }
  }
  updateHash();
  if (state.diff.computed) renderDiffView(el);
}

// ------------------------------------------------------------------ financials
function renderFinancials(el) {
  const fin = state.financials;
  if (!fin) { el.innerHTML = '<div class="empty">No financial statement data for this BDC.</div>'; return; }
  const periods = (fin.periods || []).slice(-8);
  const derived = fin.derived || {};
  let html = '<div class="section"><h3>Trends</h3><div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(250px,1fr))">';
  html += lineChart(derivedSeries('nav_per_share'), { title: 'NAV per share', fmt: v => '$' + v.toFixed(2) });
  html += lineChart(derivedSeries('nii_per_share'), { title: 'NII per share', fmt: v => '$' + v.toFixed(2) });
  html += lineChart(derivedSeries('debt_to_equity'), { title: 'Debt / equity', fmt: v => v.toFixed(2) + '×' });
  html += lineChart(derivedSeries('asset_coverage'), { title: 'Asset coverage', fmt: v => v.toFixed(2) + '×' });
  html += lineChart(derivedSeries('total_debt'), { title: 'Total debt', fmt: v => fmtMoney(v) });
  html += '</div></div>';

  html += '<div class="cards">';
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
  for (const s of stmtInfo) {
    const label = s[0], key = s[1];
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
function peerFiltered() {
  const f = state.peerFilters;
  let peers = ((state.peers && state.peers.peers) || []).slice();
  if (f.q) peers = peers.filter(p => (p.ticker + ' ' + ((state.profiles[p.ticker] || {}).name || '')).toLowerCase().indexOf(f.q.toLowerCase()) >= 0);
  if (f.minFv) peers = peers.filter(p => (num(p.fair_value) || 0) >= num(f.minFv) * 1000);
  if (f.maxDe) peers = peers.filter(p => p.debt_to_equity === null || p.debt_to_equity === undefined || num(p.debt_to_equity) <= num(f.maxDe));
  if (f.minFirstLien) peers = peers.filter(p => (num(p.first_lien_pct) || 0) >= num(f.minFirstLien));
  return peers;
}
async function renderPeers(el) {
  await ensurePeers();
  const med = state.peers.medians || {};
  const peers = peerFiltered();
  if (state.sortKey) {
    const k = state.sortKey;
    peers.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * state.sortDir;
      return String(av === null || av === undefined ? '' : av).localeCompare(String(bv === null || bv === undefined ? '' : bv)) * state.sortDir;
    });
  }
  const cols = [
    ['ticker', 'Ticker'],
    ['period', 'Filing'],
    ['fair_value', 'Fair Value'],
    ['companies', 'Cos'],
    ['first_lien_pct', '1st Lien %'],
    ['second_lien_pct', '2nd Lien %'],
    ['subordinated_pct', 'Subord %'],
    ['equity_pct', 'Equity %'],
    ['weighted_avg_cash_rate', 'Cash %'],
    ['weighted_avg_spread', 'Spread %'],
    ['pik_share_pct', 'PIK %'],
    ['floating_pct', 'Floating %'],
    ['top10_pct', 'Top10 %'],
    ['unrealized_gl_pct', 'G/L %'],
    ['median_vintage_year', 'Vintage'],
    ['age_0_2_pct', '<2yr %'],
    ['age_5plus_pct', '5yr+ %'],
    ['nav_per_share', 'NAV/Sh'],
    ['debt_to_equity', 'D/E'],
    ['asset_coverage', 'Cover'],
    ['nii_per_share', 'NII/Sh'],
    ['total_net_change', 'QoQ Δ FV'],
    ['balance_check_ok', 'Bal'],
    ['fin_periods', 'Fin Qs'],
  ];
  const f = state.peerFilters;
  let html = '<div class="filters">' +
    '<input id="pf-q" placeholder="Ticker / name" value="' + esc(f.q) + '" />' +
    '<input id="pf-fv" placeholder="Min FV ($B)" value="' + esc(f.minFv) + '" />' +
    '<input id="pf-de" placeholder="Max D/E" value="' + esc(f.maxDe) + '" />' +
    '<input id="pf-1l" placeholder="Min 1st lien %" value="' + esc(f.minFirstLien) + '" />' +
    '<button class="btn" id="pf-reset">Reset</button>' +
    '<span class="small muted">' + peers.length + ' of ' + ((state.peers.peers || []).length) + ' BDCs</span>' +
  '</div>';
  html += '<div class="muted small" style="margin-bottom:6px">Latest filing per BDC. Click a column to sort, a row to open the BDC. Peer medians are in the footer.</div>';
  html += '<table id="peers-table"><thead><tr>';
  for (const c of cols) html += '<th class="sortable num" data-sort="' + c[0] + '">' + c[1] + (state.sortKey === c[0] ? (state.sortDir === 1 ? ' ▲' : ' ▼') : '') + '</th>';
  html += '</tr></thead><tbody>';
  for (const r of peers) {
    html += '<tr style="cursor:pointer" data-ticker="' + r.ticker + '">';
    for (const c of cols) {
      const key = c[0];
      let v = r[key];
      if (key === 'fair_value') v = fmtK(v);
      else if (key === 'total_net_change') v = v === null || v === undefined ? '—' : fmtK(v);
      else if (key === 'first_lien_pct' || key === 'second_lien_pct' || key === 'subordinated_pct' || key === 'equity_pct' || key === 'pik_share_pct' || key === 'floating_pct' || key === 'top10_pct' || key === 'age_0_2_pct' || key === 'age_5plus_pct') v = fmtPct(v);
      else if (key === 'unrealized_gl_pct') v = v === null || v === undefined ? '—' : '<span class="' + (num(v) < 0 ? 'neg' : 'pos') + '">' + signed(v) + '%</span>';
      else if (key === 'weighted_avg_cash_rate' || key === 'weighted_avg_spread') v = fmtRate(v);
      else if (key === 'nav_per_share' || key === 'nii_per_share') v = v === null || v === undefined ? '—' : '$' + num(v).toFixed(2);
      else if (key === 'debt_to_equity' || key === 'asset_coverage') v = v === null || v === undefined ? '—' : num(v).toFixed(2) + '×';
      else if (key === 'balance_check_ok') v = v === true ? '<span class="badge good">✓</span>' : (v === false ? '<span class="badge bad">✗</span>' : '—');
      else if (key === 'median_vintage_year') v = v === null || v === undefined ? '—' : v;
      html += '<td class="num">' + (v === null || v === undefined ? '—' : v) + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody><tfoot><tr><td>Median</td><td>—</td>' +
    '<td class="num">' + (med.fair_value === null || med.fair_value === undefined ? '—' : fmtK(med.fair_value)) + '</td>' +
    '<td class="num">—</td>' +
    '<td class="num">' + fmtPct(med.first_lien_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.second_lien_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.subordinated_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.equity_pct) + '</td>' +
    '<td class="num">' + fmtRate(med.weighted_avg_cash_rate) + '</td>' +
    '<td class="num">' + fmtRate(med.weighted_avg_spread) + '</td>' +
    '<td class="num">' + fmtPct(med.pik_share_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.floating_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.top10_pct) + '</td>' +
    '<td class="num">' + (med.unrealized_gl_pct === null || med.unrealized_gl_pct === undefined ? '—' : signed(med.unrealized_gl_pct) + '%') + '</td>' +
    '<td class="num">' + (med.median_vintage_year === null || med.median_vintage_year === undefined ? '—' : med.median_vintage_year) + '</td>' +
    '<td class="num">' + fmtPct(med.age_0_2_pct) + '</td>' +
    '<td class="num">' + fmtPct(med.age_5plus_pct) + '</td>' +
    '<td class="num">' + (med.nav_per_share === null || med.nav_per_share === undefined ? '—' : '$' + num(med.nav_per_share).toFixed(2)) + '</td>' +
    '<td class="num">' + (med.debt_to_equity === null || med.debt_to_equity === undefined ? '—' : num(med.debt_to_equity).toFixed(2) + '×') + '</td>' +
    '<td class="num">' + (med.asset_coverage === null || med.asset_coverage === undefined ? '—' : num(med.asset_coverage).toFixed(2) + '×') + '</td>' +
    '<td class="num">' + (med.nii_per_share === null || med.nii_per_share === undefined ? '—' : '$' + num(med.nii_per_share).toFixed(2)) + '</td>' +
    '<td class="num">—</td><td class="num">—</td><td class="num">—</td></tr></tfoot></table>';

  html += '<div class="section" style="margin-top:14px"><h3>Peer sector medians</h3>' + bars((state.peers.sector_medians || []).slice(0, 12).map(s => ({ sector: s.sector, median_pct: s.median_pct, bdc_count: s.bdc_count })), 'sector', 'median_pct', 'median_pct', v => fmtPct(v)) + '</div>';

  el.innerHTML = html;
  el.querySelectorAll('#peers-table th[data-sort]').forEach(th => {
    th.onclick = () => {
      const key = th.getAttribute('data-sort');
      if (state.sortKey === key) state.sortDir = -state.sortDir; else { state.sortKey = key; state.sortDir = 1; }
      renderPeers($('main'));
    };
  });
  el.querySelectorAll('#peers-table tr[data-ticker]').forEach(tr => {
    tr.onclick = () => {
      const t = tr.getAttribute('data-ticker');
      if (state.index && state.index.tickers && state.index.tickers[t]) {
        state.tab = 'overview';
        selectTicker(t, state.index.tickers[t]);
        renderTabs();
      }
    };
  });
  const bind = (id, key) => {
    const input = $(id);
    if (!input) return;
    input.onchange = () => { state.peerFilters[key] = input.value.trim(); renderPeers($('main')); };
  };
  bind('pf-q', 'q'); bind('pf-fv', 'minFv'); bind('pf-de', 'maxDe'); bind('pf-1l', 'minFirstLien');
  const reset = $('pf-reset');
  if (reset) reset.onclick = () => { state.peerFilters = { q: '', minFv: '', maxDe: '', minFirstLien: '' }; renderPeers($('main')); };
}

// ------------------------------------------------------------------ companies view
function renderCompany(el) {
  if (!state.company) { el.innerHTML = '<div class="empty">Select a company from the list.</div>'; return; }
  const c = state.company;
  const exp = c.exposure || {};
  const detail = c.detail || {};
  const prof = c.profile || {};
  const name = exp.company_name || prof.canonical_name || c.company_id;
  $('win-title').textContent = name;
  $('win-meta').textContent = exp.num_bdcs_invested ? exp.num_bdcs_invested + ' BDCs' : '';
  const byBdc = detail.by_bdc || {};
  const byMat = detail.by_maturity || {};
  const byType = detail.by_investment_type || {};
  const total = num(exp.total_exposure_millions) || Object.keys(byBdc).reduce((s, k) => s + (num(byBdc[k]) || 0), 0);

  let html = '<div class="cards">' +
    card('Total Exposure', fmtM(total), 'across all BDCs') +
    card('BDCs Invested', fmtNum(exp.num_bdcs_invested), '') +
    card('Avg Interest Rate', exp.avg_interest_rate ? fmtRate(exp.avg_interest_rate) : '—', '') +
    card('Most Common Type', esc(exp.most_common_investment_type || '—'), '') +
    card('Primary Industry', esc(exp.primary_industry || prof.industry || '—'), '') +
  '</div>';

  if (prof.description || prof.website || prof.location) {
    html += '<div class="profile"><b>Profile</b>';
    if (prof.description) html += '<div class="desc">' + esc(prof.description) + '</div>';
    const bits = [];
    if (prof.website) bits.push((prof.website.indexOf('http') === 0 ? '<a href="' + esc(prof.website) + '" target="_blank" rel="noopener">' + esc(prof.website) + '</a>' : esc(prof.website)));
    if (prof.location) bits.push(esc(prof.location));
    if (prof.employee_range) bits.push(esc(prof.employee_range) + ' employees');
    if (prof.source) bits.push('<span class="small muted">source: ' + esc(prof.source) + '</span>');
    if (bits.length) html += '<div class="small" style="margin-top:6px">' + bits.join(' · ') + '</div>';
    html += '</div>';
  } else {
    html += '<div class="profile muted small">No profile data yet for this company.</div>';
  }

  html += '<div class="grid2">';
  html += '<div class="section"><h3>BDC exposure</h3><table><thead><tr><th>BDC</th><th class="num">Exposure</th><th class="num">% of total</th></tr></thead><tbody>';
  const bdcRows = Object.keys(byBdc).map(k => ({ ticker: k, amount: num(byBdc[k]) || 0 })).sort((a, b) => b.amount - a.amount);
  for (const r of bdcRows) {
    const pct = total ? (r.amount / total * 100).toFixed(1) + '%' : '—';
    html += '<tr><td><span class="linkish" data-bdc="' + esc(r.ticker) + '">' + esc(r.ticker) + '</span></td><td class="num">' + fmtM(r.amount) + '</td><td class="num">' + pct + '</td></tr>';
  }
  if (!bdcRows.length) html += '<tr><td colspan="3" class="muted">No BDC detail available.</td></tr>';
  html += '</tbody></table></div>';

  html += '<div class="section"><h3>By investment type</h3>' +
    bars(Object.keys(byType).map(k => ({ type: k, amount: byType[k], pct: total ? byType[k] / total * 100 : 0 })), 'type', 'amount', 'pct', fmtM) +
    '<h3 style="margin-top:12px">By maturity</h3>' +
    bars(Object.keys(byMat).sort().map(k => ({ bucket: k, amount: byMat[k], pct: total ? byMat[k] / total * 100 : 0 })), 'bucket', 'amount', 'pct', fmtM, 'alt') +
  '</div></div>';

  html += '<div class="small muted">Exposure data from company_exposures.csv / company_detail.json; profiles from company_profiles.json. <span class="linkish" id="co-back">Back to BDCs</span></div>';
  el.innerHTML = html;

  el.querySelectorAll('[data-bdc]').forEach(a => {
    a.onclick = () => {
      const t = a.getAttribute('data-bdc');
      if (state.index && state.index.tickers && state.index.tickers[t]) {
        state.tab = 'overview';
        selectTicker(t, state.index.tickers[t]);
        renderTabs();
      }
    };
  });
  const back = $('co-back');
  if (back) back.onclick = () => setMode('bdc');
}

// ------------------------------------------------------------------ CSV export
function tableToCsv(table) {
  const lines = [];
  const headCells = table.querySelectorAll('thead th');
  if (headCells.length) lines.push(Array.from(headCells).map(th => csvEscape(th.textContent.trim())).join(','));
  table.querySelectorAll('tbody tr').forEach(tr => {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => csvEscape(td.textContent.trim()));
    if (cells.length) lines.push(cells.join(','));
  });
  return lines.join('\\n');
}
function exportMainCsv() {
  if (state.mode === 'bdc' && state.tab === 'changes') { exportDiffCsv(); return; }
  const tables = $('main').querySelectorAll('table');
  if (!tables.length) { alert('Nothing to export in this view.'); return; }
  const parts = [];
  tables.forEach((t, i) => { parts.push((i ? '\\n\\n' : '') + tableToCsv(t)); });
  const base = state.mode === 'companies' ? (state.companyId || 'company') : (state.ticker + '-' + state.tab + (state.period ? '-' + state.period : ''));
  downloadText(base + '.csv', parts.join(''));
}

// ------------------------------------------------------------------ events
$('ticker-search').oninput = renderSidebar;
$('period-select').onchange = () => {
  state.period = $('period-select').value;
  state.holdings = null;
  updateHash();
  renderMain();
};
$('filter').oninput = () => renderHoldings($('main'));
$('btn-export').onclick = exportMainCsv;
$('btn-link').onclick = () => {
  const url = location.href;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(() => { $('status').textContent = 'Link copied'; }, () => { prompt('Copy this link:', url); });
  } else {
    prompt('Copy this link:', url);
  }
};
$('nav-bdc').onclick = () => setMode('bdc');
$('nav-companies').onclick = () => { state.companyId = null; setMode('companies'); };
window.addEventListener('hashchange', () => applyHash());

renderTabs();
loadTickers();
</script>
</body>
</html>`;
