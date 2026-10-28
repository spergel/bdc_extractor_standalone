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

app.get("/api/tickers/:ticker/filings", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const data = await readJson(`filings/${ticker}.json`);
  if (!data) return c.json({ error: "filings not found" }, 404);
  return c.json(data);
});

app.get("/api/tickers/:ticker/credit", async (c) => {
  const ticker = c.req.param("ticker").toUpperCase();
  const data = await readJson(`credit/${ticker}.json`);
  if (!data) return c.json({ error: "credit quality not found" }, 404);
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
  a { color: var(--navy2); }
  .flagbox { background: #fbf3e3; border: 1px solid #e8d3a8; color: #8a6417; border-radius: 4px; padding: 6px 10px; font-size: 11.5px; margin-bottom: 10px; }
  .flagbox div + div { margin-top: 2px; }
  details summary { cursor: pointer; }
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
  .bar-row.clickable { cursor: pointer; border-radius: 2px; }
  .bar-row.clickable:hover { background: #eef2f8; }
  tr.clickrow { cursor: pointer; }
  tr.clickrow:hover td { background: #eef2f8 !important; }
  .card.clickable { cursor: pointer; }
  .card.clickable:hover { border-color: var(--navy2); box-shadow: inset 0 0 0 1px var(--navy2); }
  .modal-backdrop { position: fixed; inset: 0; background: rgba(20,30,50,.45); display: flex; align-items: center; justify-content: center; z-index: 50; padding: 16px; }
  .modal-backdrop[hidden] { display: none; }
  .modal { background: #fff; border-radius: 6px; max-width: 560px; width: 100%; box-shadow: 0 12px 40px rgba(0,0,0,.3); overflow: hidden; }
  .modal .mh { background: var(--navy2); color: #fff; padding: 8px 12px; font-weight: 700; display: flex; justify-content: space-between; align-items: center; }
  .modal .mh button { background: transparent; border: 0; color: #fff; font-size: 18px; cursor: pointer; line-height: 1; }
  .modal .mb { padding: 12px; }
  .modal .mf { padding: 10px 12px; border-top: 1px solid var(--line); display: flex; justify-content: flex-end; gap: 8px; background: #f7f8fa; }
  .filter-chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 6px 3px 10px; border-radius: 14px; background: #e8effa; border: 1px solid #b9cbe8; color: var(--navy2); font-size: 11.5px; margin-bottom: 8px; }
  .filter-chip button { border: 0; background: transparent; cursor: pointer; color: var(--navy2); font-size: 13px; padding: 0 2px; }
  /* peer evaluation */
  .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }
  .peer-focus { display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; background: #f8f9fb; border: 1px solid var(--line); border-radius: 5px; padding: 10px 12px; margin-bottom: 10px; }
  .peer-focus .pf-name { font-size: 15px; font-weight: 700; }
  .leaderboard { border: 1px solid var(--line); border-radius: 5px; background: #fff; }
  .leaderboard .lb-row { display: flex; align-items: center; gap: 8px; padding: 4px 8px; border-bottom: 1px solid #edf0f3; font-size: 11.5px; cursor: pointer; }
  .leaderboard .lb-row:last-child { border-bottom: 0; }
  .leaderboard .lb-row:hover { background: #eef2f8; }
  .leaderboard .lb-row.me { background: #fdf6e7; font-weight: 700; }
  .lb-rank { color: var(--muted); width: 18px; flex-shrink: 0; }
  .lb-tkr { font-weight: 700; width: 46px; flex-shrink: 0; }
  .lb-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
  .lb-val { font-variant-numeric: tabular-nums; }
  .tilt-table td.tilt.pos, .tilt-table td.pos { color: var(--good); }
  .tilt-table td.tilt.neg, .tilt-table td.neg { color: var(--bad); }
  .chart-split { display: grid; grid-template-columns: 1fr 275px; gap: 10px; align-items: start; }
  .chart-main { min-width: 0; }
  .chart-side { min-width: 0; }
  .scatter-svg { width: 100%; height: auto; display: block; }
  .scatter-dot { cursor: pointer; }
  .scatter-dot:hover { stroke: #1a2f5a; stroke-width: 2; }
  .radar-svg { width: 100%; height: auto; display: block; max-width: 360px; }
  .heatmap-wrap { max-height: 520px; overflow: auto; border: 1px solid var(--line); border-radius: 4px; }
  #peer-heatmap { border-collapse: separate; border-spacing: 0; font-size: 11px; }
  #peer-heatmap th { position: sticky; top: 0; background: #f2f4f7; z-index: 2; white-space: nowrap; }
  #peer-heatmap td, #peer-heatmap th { padding: 3px 6px; border-bottom: 1px solid #edf0f3; }
  #peer-heatmap .sticky-col { position: sticky; left: 0; background: #fff; z-index: 1; font-weight: 600; }
  #peer-heatmap thead .sticky-col { z-index: 3; background: #f2f4f7; }
  #peer-heatmap tr.heat-focus td { box-shadow: inset 0 2px 0 var(--accent), inset 0 -2px 0 var(--accent); font-weight: 700; }
  #peer-heatmap td.heat { font-variant-numeric: tabular-nums; }
  #peer-heatmap tfoot td { position: sticky; bottom: 0; background: #f7f8fa; font-weight: 700; }
  .pct-bar { display: inline-block; width: 90px; height: 7px; background: #e9ecf1; border-radius: 4px; overflow: hidden; vertical-align: middle; margin-right: 6px; }
  .pct-fill { display: block; height: 100%; background: var(--navy2); }
  .lb-bar { width: 64px; height: 7px; background: #e9ecf1; border-radius: 4px; overflow: hidden; flex-shrink: 0; }
  .lb-bar > span { display: block; height: 100%; background: var(--accent); }
  table.mini td, table.mini th { font-size: 11px; padding: 3px 6px; }
  .heatmap-table { border-collapse: separate; border-spacing: 0; font-size: 11px; }
  .heatmap-table th, .heatmap-table td { padding: 3px 6px; border-bottom: 1px solid #edf0f3; white-space: nowrap; }
  .heatmap-table th { background: #f2f4f7; position: sticky; top: 0; }
  .heatmap-table td.heat { font-variant-numeric: tabular-nums; }
  @media (max-width: 900px) { .diff-layout { grid-template-columns: 1fr; } .grid3 { grid-template-columns: 1fr; } .chart-split { grid-template-columns: 1fr; } }
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
      <button id="nav-peers">Peer Evaluation</button>
      <button id="nav-companies">Companies</button>
    </nav>
  </header>
  <div class="layout">
    <aside class="window sidebar">
      <div class="titlebar"><span id="sidebar-title">BDCs</span><span class="meta" id="ticker-count"></span></div>
      <input id="ticker-search" placeholder="Filter by ticker or name…" />
      <select id="company-sort" style="display:none; width:calc(100% - 12px); margin:0 6px 6px; padding:4px 6px; border:1px solid var(--line); border-radius:3px; font-size:11.5px; background:#fff;">
        <option value="exposure">Sort: total exposure</option>
        <option value="bdcs">Sort: # of BDCs</option>
        <option value="name">Sort: company name</option>
      </select>
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
  <div id="modal-backdrop" class="modal-backdrop" hidden>
    <div class="modal">
      <div class="mh"><span id="modal-title">Filter holdings</span><button id="modal-x" aria-label="Close">×</button></div>
      <div class="mb" id="modal-body"></div>
      <div class="mf"><button class="btn" id="modal-cancel">Cancel</button><button class="btn primary" id="modal-apply">View filtered holdings</button></div>
    </div>
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
  diff: { before: null, after: null, cacheKey: null, computed: null, selected: null, view: 'unified', filter: 'all', minDelta: '', search: '', sort: 'delta_abs' },
  hFilter: null,
  pendingFilter: null,
  companySort: 'exposure',
  peerFocus: null,
  peerCohort: 'all',
  peerShowTable: false,
  peerFocusAnalytics: {},
  peerChartMode: '3d',
  peerPreset: 'yield_leverage',
  peerX: 'weighted_avg_cash_rate',
  peerY: 'debt_to_equity',
  peerColorKey: 'unrealized_gl_pct',
  peerHeatKey: 'fair_value',
  peerHeatDir: -1,
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
// --- quarter labels: prefer fiscal quarter end, fall back to filing date ---
function quarterEndFromFiling(filingDate) {
  const d = new Date(String(filingDate || '') + 'T00:00:00');
  if (isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const candidates = [];
  const pairs = [[2, 31], [5, 30], [8, 30], [11, 31]]; // month index, day (Mar/Jun/Sep/Dec)
  for (const yy of [y, y - 1]) {
    for (const p of pairs) {
      const qe = new Date(yy, p[0], p[1]);
      if (qe <= d) candidates.push(qe);
    }
  }
  if (!candidates.length) return null;
  const max = candidates.reduce((a, b) => (a > b ? a : b));
  return max.getFullYear() + '-' + String(max.getMonth() + 1).padStart(2, '0') + '-' + String(max.getDate()).padStart(2, '0');
}
function quarterInfoFromEnd(endIso) {
  const d = new Date(String(endIso || '') + 'T00:00:00');
  if (isNaN(d.getTime())) return null;
  const m = d.getMonth() + 1, y = d.getFullYear();
  const q = m <= 3 ? 1 : m <= 6 ? 2 : m <= 9 ? 3 : 4;
  return { q: q, year: y, label: 'Q' + q + ' ' + y, short: 'Q' + q + " '" + String(y).slice(2) };
}
function quarterInfoFromFiling(filingDate) {
  const end = quarterEndFromFiling(filingDate);
  return end ? quarterInfoFromEnd(end) : null;
}
function periodLabel(filingDate, periodEnd) {
  const info = periodEnd ? quarterInfoFromEnd(periodEnd) : quarterInfoFromFiling(filingDate);
  return (info ? info.label : '') ? info.label + ' (' + filingDate + ')' : filingDate;
}
function periodLabelShort(filingDate, periodEnd) {
  const info = periodEnd ? quarterInfoFromEnd(periodEnd) : quarterInfoFromFiling(filingDate);
  return info ? info.short : String(filingDate || '').slice(0, 7);
}
function filingFor(filed) {
  const filings = (state.analytics && state.analytics.filings) || {};
  return filings[filed] || null;
}
function filingLink(filed, label) {
  const f = filingFor(filed);
  if (!f || !f.url) return esc(label);
  const tip = [f.form, f.filed ? 'filed ' + f.filed : '', f.report_date ? 'period ' + f.report_date : ''].filter(Boolean).join(' · ');
  return '<a href="' + esc(f.url) + '" target="_blank" rel="noopener" title="' + esc(tip + ' · SEC EDGAR') + '">' + esc(label) + ' ↗</a>';
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

// ------------------------------------------------------------------ holdings slice filters
const FLOATING_REFS = ['SOFR', 'LIBOR', 'Prime', 'EURIBOR', 'SONIA', 'CDOR', 'BBSY', 'CORRA', 'Base rate', 'Other'];
const FILTER_LABELS = {
  type: 'Investment type', sector: 'Sector', ref: 'Reference rate', rateType: 'Rate type',
  spread: 'Spread bucket', pik: 'PIK positions', vintage: 'Vintage year', age: 'Portfolio age',
  maturity: 'Maturity', company: 'Company',
};
function normalizeRef(raw) {
  const t = String(raw || '').trim().toUpperCase();
  if (!t || ['N/A', 'NA', 'NONE', '-', '—', '0'].indexOf(t) >= 0) return '';
  if (t.indexOf('SOFR') >= 0) return 'SOFR';
  if (t.indexOf('LIBOR') >= 0 || t.indexOf('L+') === 0 || t.indexOf('L +') === 0 || t.indexOf('L-') === 0 || t.indexOf('L -') === 0) return 'LIBOR';
  if (t.indexOf('EURIBOR') >= 0 || t.indexOf('E+') === 0 || t.indexOf('E +') === 0) return 'EURIBOR';
  if (t.indexOf('PRIME') >= 0 || t.indexOf('P+') === 0 || t.indexOf('P +') === 0) return 'Prime';
  if (t.indexOf('SONIA') >= 0) return 'SONIA';
  if (t.indexOf('CDOR') >= 0) return 'CDOR';
  if (t.indexOf('BBSY') >= 0) return 'BBSY';
  if (t.indexOf('CORRA') >= 0) return 'CORRA';
  if (t.indexOf('FIXED') >= 0) return 'Fixed';
  if (t.indexOf('BASE RATE') >= 0) return 'Base rate';
  return 'Other';
}
function cleanSectorJs(raw) {
  const t = String(raw || '').trim();
  if (!t) return 'Not Reported';
  if ('-$0123456789.,%/()'.indexOf(t.charAt(0)) >= 0) return 'Not Reported';
  const parts = t.split('/');
  if (parts.length === 3 && parts.every(x => x.trim() !== '' && !isNaN(Number(x)))) return 'Not Reported';
  const low = t.toLowerCase();
  if (low.indexOf('lien') >= 0 || low.indexOf('senior secured') >= 0 || low.indexOf('unitranche') >= 0 || low.indexOf('subordinated') >= 0) return 'Not Reported';
  if (low.indexOf('membership interest') >= 0 || low.indexOf('per share') >= 0) return 'Not Reported';
  if (['yes', 'no', 'n/a', 'none', 'na', '—', '-', 'other'].indexOf(low) >= 0) return 'Not Reported';
  const unit = low.indexOf('share') >= 0 || low.indexOf('unit') >= 0;
  const cls = low.indexOf('class') >= 0 || low.indexOf('common') >= 0 || low.indexOf('preferred') >= 0 || low.indexOf('series') >= 0;
  if (unit && cls) return 'Not Reported';
  return t;
}
function parseYearJs(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  const y4 = Number(s.slice(0, 4));
  if (s.length >= 4 && !isNaN(y4) && y4 > 1900) return y4;
  const parts = s.split('/');
  if (parts.length === 3) {
    const y = Number(parts[2]);
    if (!isNaN(y) && y > 1900) return y;
  }
  return null;
}
function spreadBucketJs(s) {
  if (s < 3) return '<3%';
  if (s < 4) return '3–4%';
  if (s < 5) return '4–5%';
  if (s < 6) return '5–6%';
  if (s < 7) return '6–7%';
  return '7%+';
}
function maturityWallJs(year) {
  if (year <= 2026) return '≤2026';
  if (year >= 2036) return '2036+';
  return String(year);
}
function ageBucketJs(acqDate, filingDate) {
  const a = new Date(acqDate);
  const f = new Date(filingDate);
  let years = null;
  if (!isNaN(a.getTime()) && !isNaN(f.getTime())) {
    years = (f.getTime() - a.getTime()) / (365.25 * 24 * 3600 * 1000);
  } else {
    const ay = parseYearJs(acqDate), fy = parseYearJs(filingDate);
    if (ay && fy) years = fy - ay;
  }
  if (years === null) return null;
  return years < 1 ? '0–1 yrs' : years < 2 ? '1–2 yrs' : years < 3 ? '2–3 yrs' : years < 4 ? '3–4 yrs' : years < 5 ? '4–5 yrs' : '5+ yrs';
}
function filterRowsByKind(kind, value, rows, filingDate) {
  if (!kind) return rows;
  if (kind === 'type') return rows.filter(r => ((r.investment_type || '').trim() || 'Not Reported') === value);
  if (kind === 'sector') return rows.filter(r => cleanSectorJs(r.industry) === value);
  if (kind === 'ref') return rows.filter(r => normalizeRef(r.reference_rate) === value);
  if (kind === 'rateType') {
    if (value === 'Floating') return rows.filter(r => FLOATING_REFS.indexOf(normalizeRef(r.reference_rate)) >= 0);
    if (value === 'Fixed') return rows.filter(r => normalizeRef(r.reference_rate) === 'Fixed');
    return rows.filter(r => !normalizeRef(r.reference_rate));
  }
  if (kind === 'spread') return rows.filter(r => { const s = num(r.spread); return s !== null && s > 0 && spreadBucketJs(s) === value; });
  if (kind === 'pik') return rows.filter(r => { const p = num(r.pik_rate); return p !== null && p > 0; });
  if (kind === 'vintage') return rows.filter(r => String(parseYearJs(r.acquisition_date)) === value);
  if (kind === 'age') return rows.filter(r => ageBucketJs(r.acquisition_date, filingDate) === value);
  if (kind === 'maturity') return rows.filter(r => { const y = parseYearJs(r.maturity_date); return y !== null && maturityWallJs(y) === value; });
  if (kind === 'company') return rows.filter(r => normName(r.company_name) === normName(value));
  return rows;
}
function filterLabel(kind, value) {
  return (FILTER_LABELS[kind] || kind) + ': ' + value;
}
async function ensureHoldings() {
  if (!state.holdings) {
    const res = await fetch('/api/tickers/' + state.ticker + '/periods/' + state.period);
    const data = res.ok ? await res.json() : { rows: [] };
    state.holdings = data.rows || [];
  }
  return state.holdings;
}
function closeModal() {
  $('modal-backdrop').hidden = true;
  state.pendingFilter = null;
}
async function openFilterModal(kind, value) {
  if (!kind || value === null || value === undefined || value === '') return;
  state.pendingFilter = { kind, value, label: filterLabel(kind, value) };
  $('modal-title').textContent = 'Filter holdings — ' + state.pendingFilter.label;
  $('modal-body').innerHTML = '<div class="empty">Computing slice…</div>';
  $('modal-backdrop').hidden = false;
  const rows = await ensureHoldings();
  const slice = filterRowsByKind(kind, value, rows, state.period);
  const totalFv = rows.reduce((s, r) => s + (num(r.fair_value) || 0), 0);
  const sliceFv = slice.reduce((s, r) => s + (num(r.fair_value) || 0), 0);
  const rateW = slice.reduce((s, r) => { const fv = num(r.fair_value) || 0; return s + (num(r.cash_rate) !== null && fv > 0 ? num(r.cash_rate) * fv : 0); }, 0);
  const rateDen = slice.reduce((s, r) => { const fv = num(r.fair_value) || 0; return s + (num(r.cash_rate) !== null && fv > 0 ? fv : 0); }, 0);
  const companies = {};
  for (const r of slice) {
    const n = (r.company_name || '').trim();
    if (n) companies[n] = (companies[n] || 0) + (num(r.fair_value) || 0);
  }
  const top = Object.keys(companies).sort((a, b) => companies[b] - companies[a]).slice(0, 5);
  let body = '<div class="cards" style="grid-template-columns:repeat(3,1fr)">' +
    card('Positions', fmtNum(slice.length), '') +
    card('Fair Value', fmtK(sliceFv), '') +
    card('% of Portfolio', fmtPct(totalFv ? sliceFv / totalFv * 100 : 0), 'of ' + fmtK(totalFv)) +
  '</div>';
  body += '<div class="small muted" style="margin-bottom:6px">Weighted avg cash rate: <b>' + (rateDen ? fmtRate(rateW / rateDen) : '—') + '</b> · filing ' + esc(state.period) + '</div>';
  if (top.length) {
    body += '<div class="small"><b>Largest positions</b></div><table><thead><tr><th>Company</th><th class="num">Fair Value</th></tr></thead><tbody>';
    for (const n of top) body += '<tr><td>' + esc(n) + '</td><td class="num">' + fmtK(companies[n]) + '</td></tr>';
    body += '</tbody></table>';
  }
  if (!slice.length) body += '<div class="empty">No positions match this slice.</div>';
  $('modal-body').innerHTML = body;
  $('modal-apply').disabled = !slice.length;
}
function applyPendingFilter() {
  const f = state.pendingFilter;
  if (!f) return;
  state.hFilter = f;
  state.pendingFilter = null;
  $('modal-backdrop').hidden = true;
  state.tab = 'holdings';
  $('filter').value = '';
  renderTabs();
  updateModeChrome();
  updateHash();
  renderMain();
}

// ------------------------------------------------------------------ deep links
function updateHash() {
  let h;
  if (state.mode === 'companies') h = '#/companies' + (state.companyId ? '/' + state.companyId : '');
  else if (state.mode === 'peers') h = '#/peers/' + (state.peerFocus || '') + '/' + (state.peerCohort || 'all');
  else if (state.tab === 'changes') {
    h = '#/bdc/' + (state.ticker || '') + '/changes';
    if (state.diff && state.diff.before) h += '/' + state.diff.before;
    if (state.diff && state.diff.after) h += '/' + state.diff.after;
  } else if (state.tab === 'holdings') {
    h = '#/bdc/' + (state.ticker || '') + '/holdings' + (state.period ? '/' + state.period : '');
    if (state.hFilter) h += '/' + encodeURIComponent(state.hFilter.kind + ':' + state.hFilter.value);
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
  if (parts[0] === 'peers') {
    state.mode = 'peers';
    state.peerFocus = (parts[1] || state.ticker || 'ARCC').toUpperCase();
    state.peerCohort = ['all', 'large', 'mid', 'small'].indexOf(parts[2]) >= 0 ? parts[2] : 'all';
    $('ticker-search').value = '';
    updateModeChrome();
    renderPeerSidebar();
    ensurePeers().then(() => { renderPeerSidebar(); renderMain(); });
    return;
  }
  state.mode = 'bdc';
  updateModeChrome();
  renderSidebar();
  if (parts[0] === 'bdc' && parts[1] && state.index && state.index.tickers[parts[1]]) {
    state.tab = TABS.some(t => t[0] === parts[2]) ? parts[2] : 'overview';
    if (state.tab === 'changes') {
      state.diff.before = parts[3] || null;
      state.diff.after = parts[4] || null;
    }
    if (parts[2] === 'holdings') {
      state.hFilter = null;
      if (parts[4]) {
        const raw = decodeURIComponent(parts[4]);
        const idx = raw.indexOf(':');
        if (idx > 0) {
          const k = raw.slice(0, idx), v = raw.slice(idx + 1);
          state.hFilter = { kind: k, value: v, label: filterLabel(k, v) };
        }
      }
    }
    const periodArg = parts[2] === 'changes' ? (parts[4] || parts[3] || null) : (parts[3] || null);
    selectTicker(parts[1], state.index.tickers[parts[1]], periodArg, true);
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
  if (state.mode === 'peers') return renderPeerSidebar();
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
  rows = rows.slice();
  if (state.companySort === 'name') rows.sort((a, b) => (a.company_name || '').localeCompare(b.company_name || ''));
  else if (state.companySort === 'bdcs') rows.sort((a, b) => (num(b.num_bdcs_invested) || 0) - (num(a.num_bdcs_invested) || 0));
  else rows.sort((a, b) => (num(b.total_exposure_millions) || 0) - (num(a.total_exposure_millions) || 0));
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
  ['valuation', 'Valuation & Coverage'],
  ['holdings', 'Holdings'],
  ['types', 'Loan Types'],
  ['sectors', 'Sectors'],
  ['rates', 'Rates & Structure'],
  ['vintage', 'Vintage & Maturity'],
  ['changes', 'QoQ Changes'],
  ['financials', 'Financials'],
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
  const isPeers = state.mode === 'peers';
  $('tabs').style.display = isBdc ? '' : 'none';
  $('nav-bdc').className = isBdc ? 'active' : '';
  $('nav-peers').className = isPeers ? 'active' : '';
  $('nav-companies').className = state.mode === 'companies' ? 'active' : '';
  const showPeriod = isBdc && state.ticker && (state.tab === 'holdings' || state.tab === 'types' || state.tab === 'sectors' || state.tab === 'rates' || state.tab === 'vintage');
  $('period-select').style.display = showPeriod ? '' : 'none';
  $('filter').style.display = isBdc && state.tab === 'holdings' ? '' : 'none';
  $('ticker-search').placeholder = isBdc ? 'Filter by ticker or name…' : (isPeers ? 'Filter peers…' : 'Search companies…');
  $('company-sort').style.display = state.mode === 'companies' ? '' : 'none';
}
function setMode(mode, silent) {
  const prevMode = state.mode;
  state.mode = mode;
  if (mode === 'companies') { state.company = null; state.companyId = state.companyId || null; }
  if (mode === 'peers') {
    state.peerFocus = (prevMode === 'bdc' && state.ticker) ? state.ticker : (state.peerFocus || state.ticker || 'ARCC');
    $('ticker-search').value = '';
    updateModeChrome();
    renderPeerSidebar();
    if (!silent) updateHash();
    ensurePeers().then(() => { renderPeerSidebar(); renderMain(); });
    return;
  }
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
    state.diff = { before: keepBefore, after: keepAfter, cacheKey: null, computed: null, selected: null, view: state.diff.view, filter: 'all', minDelta: '', search: '', sort: state.diff.sort };
    if (!fromHash) state.hFilter = null;
  }
  const periods = (info && info.periods) || [];
  state.period = period || (info && info.latest) || (periods.length ? periods[periods.length - 1] : null);
  state.analytics = null; state.financials = null; state.profile = null; state.holdings = null;
  const prof = state.profiles[ticker] || {};
  $('win-title').textContent = ticker + ' — ' + (prof.name || ticker);
  $('win-meta').textContent = periodLabel(state.period, null);
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
  $('status').textContent = ticker + ' · ' + (info.row_count || 0).toLocaleString() + ' holdings · latest ' + periodLabel(state.period, null);
}
function renderPeriodSelect(info) {
  const sel = $('period-select');
  const periods = (info && info.periods) || [];
  sel.style.display = periods.length ? '' : 'none';
  sel.innerHTML = '';
  for (const p of periods.slice().reverse()) {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = periodLabel(p, null);
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
  if (state.mode === 'peers') return renderPeerEvaluation(el);
  if (!state.ticker) { el.innerHTML = '<div class="empty">Select a BDC from the list.</div>'; return; }
  if (state.tab === 'overview') renderOverview(el);
  else if (state.tab === 'valuation') renderValuation(el);
  else if (state.tab === 'holdings') renderHoldings(el);
  else if (state.tab === 'types') renderBreakdown(el, 'types', 'Loan Types');
  else if (state.tab === 'sectors') renderBreakdown(el, 'sectors', 'Sectors');
  else if (state.tab === 'rates') renderRates(el);
  else if (state.tab === 'vintage') renderVintage(el);
  else if (state.tab === 'changes') renderChanges(el);
  else if (state.tab === 'financials') renderFinancials(el);
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
  return Object.keys(node.series).sort().map(end => ({ label: (quarterInfoFromEnd(end) || {}).short || end.slice(0, 7), value: node.series[end].val }));
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
function card(label, value, note, filterKind, filterValue) {
  const clickable = filterKind && filterValue !== undefined && filterValue !== null && filterValue !== '';
  const attrs = clickable
    ? ' clickable" data-filter-kind="' + esc(filterKind) + '" data-filter-value="' + esc(filterValue) + '" title="Click to filter holdings"'
    : '"';
  return '<div class="card' + attrs + '><div class="label">' + esc(label) + '</div><div class="value">' + value + '</div><div class="note">' + esc(note || '') + '</div></div>';
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
function signedBarChart(points, opts) {
  const o = opts || {};
  const vals = points.map(p => num(p.value)).filter(v => v !== null);
  if (!vals.length) return '<div class="chart"><div class="title">' + esc(o.title || '') + '</div><div class="muted small">Not enough history.</div></div>';
  const w = 300, h = 86, pad = 8;
  const maxAbs = Math.max.apply(null, vals.map(v => Math.abs(v)).concat([1]));
  const zero = h / 2;
  const step = (w - pad * 2) / Math.max(1, points.length);
  let bars = '';
  points.forEach((p, i) => {
    const v = num(p.value);
    if (v === null) return;
    const bh = Math.max(0.5, Math.abs(v) / maxAbs * (h / 2 - 7));
    const x = pad + i * step + 1, bw = Math.max(2, step - 2);
    const y = v >= 0 ? zero - bh : zero;
    bars += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="' + (v >= 0 ? '#1d7a4f' : '#a13333') + '" opacity=".82"><title>' + esc(p.label + ': ' + (o.fmt ? o.fmt(v) : v)) + '</title></rect>';
  });
  const last = points[points.length - 1];
  return '<div class="chart"><div class="title"><span>' + esc(o.title || '') + '</span><span class="last">' + (o.fmt ? o.fmt(num(last.value)) : '') + '</span></div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><line x1="' + pad + '" y1="' + zero + '" x2="' + (w - pad) + '" y2="' + zero + '" stroke="#c9cdd4" stroke-width="1"/>' + bars + '</svg>' +
    '<div class="small muted">' + esc(points[0].label) + ' → ' + esc(last.label) + ' · range ' + (o.fmt ? o.fmt(Math.min.apply(null, vals)) + ' to ' + o.fmt(Math.max.apply(null, vals)) : '') + '</div></div>';
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
  const val = state.analytics.valuation || {};
  const credit = state.analytics.credit || {};
  const nav = val.nav_per_share != null ? num(val.nav_per_share) : latestDerived('nav_per_share');
  const d2e = latestDerived('debt_to_equity');
  const cov = latestDerived('asset_coverage');
  const nii = latestDerived('nii_per_share');
  const price = val.price != null ? num(val.price) : num(prof.price && prof.price.regularMarketPrice);
  const prNav = val.premium_discount_pct != null ? num(val.premium_discount_pct) : (nav !== null && price !== null ? (price / nav - 1) * 100 : null);
  const assets = latestStatement('Assets');
  const debt = latestDerived('total_debt');
  const equity = latestStatement("Stockholders' Equity") !== null ? latestStatement("Stockholders' Equity") : latestStatement('Net Assets');
  const finMeta = (state.analytics.meta && state.analytics.meta.financials) || {};
  const finLatest = (state.financials && state.financials.periods && state.financials.periods.length) ? state.financials.periods[state.financials.periods.length - 1] : null;
  let html = '<div class="asof">Holdings <b>' + filingLink(p.filing_date, periodLabel(p.filing_date, p.period_end)) + '</b>' +
    (finLatest ? ' · Financials <b>' + filingLink(finLatest.filed, periodLabel(finLatest.filed || finLatest.end, finLatest.end)) + '</b>' : '') +
    (val.price_asof ? ' · Market data as of <b>' + esc(val.price_asof) + '</b>' : '') +
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
    card('Floating Rate', fmtPct(t.floating_pct), 'PIK ' + fmtPct(t.pik_share_pct) + ' of FV', 'rateType', 'Floating') +
    card('Top 10 Concentration', fmtPct(t.top10_pct), 'of fair value') +
  '</div>';

  html += '<div class="section"><h3>Market &amp; distribution</h3><div class="cards">' +
    card('Share Price', price === null ? '—' : '$' + price.toFixed(2), val.price_asof ? 'as of ' + val.price_asof : 'no market profile') +
    card('P/NAV', prNav === null ? '—' : (val.p_nav != null ? num(val.p_nav).toFixed(2) : (price / nav).toFixed(2)) + '×', prNav === null ? '' : signed(prNav) + '% vs NAV') +
    card('Dividend Yield', val.dividend_yield_pct != null ? fmtPct(val.dividend_yield_pct) : '—', val.dividend_rate != null ? '$' + num(val.dividend_rate).toFixed(2) + ' annual' : '') +
    card('NII Coverage', val.nii_coverage != null ? num(val.nii_coverage).toFixed(2) + '×' : '—', val.payout_ratio_pct != null ? 'payout ' + fmtPct(val.payout_ratio_pct) : '') +
    card('Non-Accrual (FV)', credit.non_accrual_fv_pct != null ? fmtPct(credit.non_accrual_fv_pct) : '—', credit.non_accrual_cost_pct != null ? 'cost ' + fmtPct(credit.non_accrual_cost_pct) : 'as disclosed') +
  '</div>';
  if ((val.flags || []).length) html += '<div class="flagbox">' + val.flags.map(f => '<div>⚠ ' + esc(f) + '</div>').join('') + '</div>';
  html += '</div>';

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
  const fvPoints = periods.map(x => ({ label: periodLabelShort(x.filing_date, x.period_end), value: x.totals.fair_value }));
  const yieldPoints = periods.map(x => ({ label: periodLabelShort(x.filing_date, x.period_end), value: x.totals.weighted_avg_cash_rate }));
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
  html += bars(types, 'type', 'fair_value', 'pct', fmtK, '', 'type');
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
  if (lnk) lnk.onclick = () => { state.peerFocus = state.ticker; setMode('peers'); };
}

function bars(items, labelKey, valKey, pctKey, fmt, cls, filterKind) {
  if (!items || !items.length) return '<div class="empty">No data.</div>';
  const max = Math.max.apply(null, items.map(i => Math.abs(num(i[pctKey]) || 0)));
  let html = '<div class="bars">';
  for (const it of items) {
    const pct = num(it[pctKey]) || 0;
    const w = max > 0 ? Math.max(1.5, Math.abs(pct) / max * 100) : 0;
    const clickable = !!filterKind;
    const attrs = clickable ? ' clickable" data-filter-kind="' + esc(filterKind) + '" data-filter-value="' + esc(it[labelKey]) + '" title="Click to filter holdings"' : '"';
    html += '<div class="bar-row' + attrs + '><span class="label" title="' + esc(it[labelKey]) + '">' + esc(it[labelKey]) + '</span>' +
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
  const fullFv = state.holdings.reduce((s, r) => s + (num(r.fair_value) || 0), 0);
  let rows = state.holdings;
  if (state.hFilter) rows = filterRowsByKind(state.hFilter.kind, state.hFilter.value, rows, state.period);
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
  let head = '';
  if (state.hFilter) head += '<div class="filter-chip">' + esc(state.hFilter.label) + '<button id="hfilter-clear" title="Clear filter">×</button></div>';
  head += '<div class="small muted" style="margin-bottom:6px">' + fmtNum(rows.length) + ' positions · ' + fmtK(totalFv) + ' · ' + fmtPct(fullFv ? totalFv / fullFv * 100 : 0) + ' of portfolio</div>';
  let html = '<table><thead><tr>' + cols.map(c => '<th class="' + (c[2] === 'num' ? 'num' : '') + '">' + c[1] + '</th>').join('') + '</tr></thead><tbody>';
  const slice = rows.slice(0, 3000);
  for (const r of slice) {
    html += '<tr>';
    for (const c of cols) {
      const key = c[0], cls = c[2];
      let v;
      if (key === 'pct_portfolio') {
        const fv = num(r.fair_value);
        v = fv !== null && fullFv > 0 ? (fv / fullFv * 100).toFixed(1) + '%' : '—';
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
  el.innerHTML = head + (rows.length ? html : '<div class="empty">No holdings match.</div>');
  const clear = $('hfilter-clear');
  if (clear) clear.onclick = () => { state.hFilter = null; updateHash(); renderMain(); };
}

// ------------------------------------------------------------------ breakdowns
async function renderBreakdown(el, kind, title) {
  if (kind === 'sectors') await ensurePeers();
  const p = currentPeriodData();
  if (!p) { el.innerHTML = '<div class="empty">No analytics data.</div>'; return; }
  const list = p[kind] || [];
  const key = kind === 'types' ? 'type' : 'sector';
  let html = '<div class="section"><h3>' + title + ' — ' + p.filing_date + '</h3>' + bars(list, key, 'fair_value', 'pct', fmtK, '', kind === 'types' ? 'type' : 'sector') + '</div>';
  if (kind === 'types') {
    html += '<div class="section"><h3>Details</h3><table><thead><tr><th>Type</th><th class="num">Fair Value</th><th class="num">%</th><th class="num">Positions</th><th class="num">Principal</th><th class="num">Avg FV / position</th><th class="num">Unrealized G/L</th><th class="num">G/L %</th></tr></thead><tbody>';
    for (const it of list) {
      const avg = it.count ? it.fair_value / it.count : null;
      html += '<tr class="clickrow" data-filter-kind="type" data-filter-value="' + esc(it.type) + '" title="Click to filter holdings"><td>' + esc(it.type) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td><td class="num">' + fmtNum(it.count) + '</td><td class="num">' + fmtK(it.principal) + '</td><td class="num">' + (avg === null ? '—' : fmtK(avg)) + '</td>' +
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
      html += '<tr class="clickrow" data-filter-kind="sector" data-filter-value="' + esc(it.sector) + '" title="Click to filter holdings"><td>' + esc(it.sector) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + fmtPct(it.pct) + '</td>' +
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
    periods.map(p => '<th class="num">' + esc(periodLabelShort(p.filing_date, p.period_end)) + '</th>').join('') + '</tr></thead><tbody>';
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
    card('Floating Rate', fmtPct(t.floating_pct), 'of fair value', 'rateType', 'Floating') +
    card('Fixed Rate', fmtPct(t.fixed_pct), 'reported as fixed', 'rateType', 'Fixed') +
    card('PIK Share', fmtPct(t.pik_share_pct), 'positions with PIK > 0', 'pik', 'yes') +
    card('Wtd Avg Spread', fmtRate(t.weighted_avg_spread), 'over reference') +
    card('Wtd Avg Floor', t.weighted_avg_floor === null || t.weighted_avg_floor === undefined ? '—' : fmtRate(t.weighted_avg_floor), 'where a floor is reported') +
    card('Reference Coverage', fmtPct(cov.reference_rate, 0), 'of fair value') +
  '</div>';
  html += '<div class="grid2">';
  html += '<div class="section"><h3>Rate type mix</h3>' + bars(p.rate_types || [], 'type', 'fair_value', 'pct', fmtK, '', 'rateType') +
    '<div class="small muted">Floating = reports SOFR/LIBOR/Prime/EURIBOR/etc. Not reported = no reference rate on the filing row (often equity or structured positions).</div></div>';
  html += '<div class="section"><h3>Reference rate mix</h3>' + bars(p.reference_rates || [], 'rate', 'fair_value', 'pct', fmtK, '', 'ref') + '</div>';
  html += '</div>';
  html += '<div class="section"><h3>Spread distribution (FV-weighted)</h3>' + bars(p.spread_buckets || [], 'bucket', 'fair_value', 'pct', fmtK, 'alt', 'spread') +
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
    bars(p.vintage_years || [], 'year', 'fair_value', 'pct', fmtK, '', 'vintage') + '</div>';

  const vy = (p.vintage_years || []).filter(v => v.mark_pct !== null && v.mark_pct !== undefined && (v.pct >= 0.2 || v.count >= 5));
  if (vy.length) {
    html += '<div class="section"><h3>Marks by acquisition year</h3><table><thead><tr><th>Vintage</th><th class="num">Fair Value</th><th class="num">% FV</th><th class="num">Cost</th><th class="num">Mark</th><th class="num">Positions</th></tr></thead><tbody>';
    for (const v of vy.slice().reverse()) {
      const mark = num(v.mark_pct);
      html += '<tr class="clickrow" data-filter-kind="vintage" data-filter-value="' + esc(v.year) + '" title="Click to filter holdings"><td><b>' + esc(v.year) + '</b></td>' +
        '<td class="num">' + fmtK(v.fair_value) + '</td><td class="num">' + fmtPct(v.pct) + '</td>' +
        '<td class="num">' + (v.cost === null || v.cost === undefined ? '—' : fmtK(v.cost)) + '</td>' +
        '<td class="num ' + (mark === null ? '' : (mark < 0 ? 'neg' : 'pos')) + '">' + (mark === null ? '—' : signed(mark) + '%') + '</td>' +
        '<td class="num">' + fmtNum(v.count) + '</td></tr>';
    }
    html += '</tbody></table><div class="small muted" style="margin-top:4px">Mark = (fair value − amortized cost) / amortized cost. Cohorts below 0.2% of FV and fewer than 5 positions are omitted.</div></div>';
  }
  html += '<div class="grid2">';
  html += '<div class="section"><h3>Portfolio age (years since acquisition)</h3>' + bars(p.age_buckets || [], 'bucket', 'fair_value', 'pct', fmtK, '', 'age') + '</div>';
  html += '<div class="section"><h3>Maturity wall (by year)</h3>' + bars(p.maturity_wall || [], 'year', 'fair_value', 'pct', fmtK, 'alt', 'maturity') + '</div>';
  html += '</div>';

  if ((p.maturity_wall || []).length) {
    html += '<div class="section"><h3>Maturity wall detail</h3><table><thead><tr><th>Maturity</th><th class="num">Fair Value</th><th class="num">% FV</th><th class="num">Wtd Cash Rate</th><th class="num">Floating</th><th class="num">Positions</th></tr></thead><tbody>';
    for (const w of (p.maturity_wall || [])) {
      html += '<tr class="clickrow" data-filter-kind="maturity" data-filter-value="' + esc(w.year) + '" title="Click to filter holdings"><td><b>' + esc(w.year) + '</b></td>' +
        '<td class="num">' + fmtK(w.fair_value) + '</td><td class="num">' + fmtPct(w.pct) + '</td>' +
        '<td class="num">' + (w.weighted_avg_cash_rate === null || w.weighted_avg_cash_rate === undefined ? '—' : fmtRate(w.weighted_avg_cash_rate)) + '</td>' +
        '<td class="num">' + (w.floating_pct === null || w.floating_pct === undefined ? '—' : fmtPct(w.floating_pct)) + '</td>' +
        '<td class="num">' + fmtNum(w.count) + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  html += '<div class="section"><h3>Marks vs amortized cost</h3><table><thead><tr><th>Group</th><th class="num">Fair Value</th><th class="num">Unrealized G/L</th><th class="num">G/L %</th></tr></thead><tbody>';
  for (const it of (p.types || []).slice(0, 8)) {
    html += '<tr class="clickrow" data-filter-kind="type" data-filter-value="' + esc(it.type) + '" title="Click to filter holdings"><td>' + esc(it.type) + '</td><td class="num">' + fmtK(it.fair_value) + '</td><td class="num">' + (it.unrealized_gl === null || it.unrealized_gl === undefined ? '—' : fmtK(it.unrealized_gl)) + '</td><td class="num ' + (num(it.unrealized_gl_pct) < 0 ? 'neg' : 'pos') + '">' + (it.unrealized_gl_pct === null || it.unrealized_gl_pct === undefined ? '—' : signed(it.unrealized_gl_pct) + '%') + '</td></tr>';
  }
  html += '</tbody></table><div class="small muted" style="margin-top:4px">By investment type. Cost coverage: ' + fmtPct(t.cost_coverage_pct, 0) + ' of fair value.</div></div>';

  const hist = (state.analytics.vintage_history || []).slice(-8);
  if (hist.length > 1) {
    const lastHist = hist[hist.length - 1];
    const years = Array.from(new Set(hist.flatMap(h => (h.years || []).map(y => y.year)))).filter(y => {
      const cell = (lastHist.years || []).find(x => x.year === y);
      return cell && num(cell.pct) >= 0.3;
    }).sort().reverse();
    if (years.length) {
      html += '<div class="section"><h3>Vintage mark migration (FV / cost)</h3><div style="overflow:auto"><table class="heatmap-table"><thead><tr><th>Vintage</th>' +
        hist.map(h => '<th class="num">' + esc(periodLabelShort(h.filing_date, h.period_end)) + '</th>').join('') + '</tr></thead><tbody>';
      for (const y of years) {
        html += '<tr><td><b>' + esc(y) + '</b></td>';
        for (const h of hist) {
          const cell = (h.years || []).find(x => x.year === y);
          const m = cell ? num(cell.mark_pct) : null;
          let style = '';
          if (m !== null) {
            const alpha = (0.05 + Math.min(0.24, Math.abs(m) / 40 * 0.24)).toFixed(2);
            style = m >= 0 ? 'background:rgba(29,122,79,' + alpha + ');' : 'background:rgba(161,51,51,' + alpha + ');';
          }
          html += '<td class="num heat" style="' + style + '" title="' + esc(y + ' · ' + periodLabelShort(h.filing_date, h.period_end) + ': ' + (m === null ? '—' : signed(m) + '%')) + '">' + (m === null ? '—' : signed(m) + '%') + '</td>';
        }
        html += '</tr>';
      }
      html += '</tbody></table></div><div class="small muted">Rows are acquisition years with at least 0.3% of current FV. Green = above cost, red = below cost.</div></div>';
    }
  }

  html += '<div class="grid2">';
  html += '<div class="section"><h3>Top 5 gainers</h3><table><thead><tr><th>Company</th><th class="num">G/L</th><th class="num">Fair Value</th></tr></thead><tbody>';
  for (const g of (p.top_gainers || [])) html += '<tr class="clickrow" data-filter-kind="company" data-filter-value="' + esc(g.company) + '" title="Click to filter holdings"><td>' + esc(g.company) + '</td><td class="num pos">' + fmtK(g.unrealized_gl) + '</td><td class="num">' + fmtK(g.fair_value) + '</td></tr>';
  html += '</tbody></table></div>';
  html += '<div class="section"><h3>Top 5 losers</h3><table><thead><tr><th>Company</th><th class="num">G/L</th><th class="num">Fair Value</th></tr></thead><tbody>';
  for (const g of (p.top_losers || [])) html += '<tr class="clickrow" data-filter-kind="company" data-filter-value="' + esc(g.company) + '" title="Click to filter holdings"><td>' + esc(g.company) + '</td><td class="num neg">' + fmtK(g.unrealized_gl) + '</td><td class="num">' + fmtK(g.fair_value) + '</td></tr>';
  html += '</tbody></table></div></div>';

  html += '<div class="section"><h3>Top 10 companies by fair value</h3><table><thead><tr><th>Company</th><th class="num">Fair Value</th><th class="num">Unrealized G/L</th></tr></thead><tbody>';
  for (const c of (p.top_companies || [])) html += '<tr class="clickrow" data-filter-kind="company" data-filter-value="' + esc(c.company) + '" title="Click to filter holdings"><td>' + esc(c.company) + '</td><td class="num">' + fmtK(c.fair_value) + '</td><td class="num">' + (c.unrealized_gl === null || c.unrealized_gl === undefined ? '—' : fmtK(c.unrealized_gl)) + '</td></tr>';
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
  return dates.map(d => '<option value="' + d + '"' + (d === selected ? ' selected' : '') + '>' + periodLabel(d, null) + '</option>').join('');
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
  const fvOf = c => (c.after ? c.after.fv : c.before ? c.before.fv : 0);
  const posOf = c => (c.after ? c.after.positions : c.before ? c.before.positions : 0);
  rows = rows.slice();
  const sort = state.diff.sort || 'delta_abs';
  if (sort === 'delta_desc') rows.sort((a, b) => b.deltaFv - a.deltaFv);
  else if (sort === 'delta_asc') rows.sort((a, b) => a.deltaFv - b.deltaFv);
  else if (sort === 'name') rows.sort((a, b) => a.name.localeCompare(b.name));
  else if (sort === 'fv') rows.sort((a, b) => fvOf(b) - fvOf(a));
  else if (sort === 'positions') rows.sort((a, b) => posOf(b) - posOf(a));
  else if (sort === 'changed') rows.sort((a, b) => b.changedCount - a.changedCount);
  else rows.sort((a, b) => Math.abs(b.deltaFv) - Math.abs(a.deltaFv));
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
    html += '<table class="diff-split"><thead><tr><th class="field">Field</th><th>Before: ' + esc(periodLabel(state.diff.before, null)) + '</th><th>After: ' + esc(periodLabel(state.diff.after, null)) + '</th></tr></thead><tbody>';
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
    '<span class="muted">' + esc(periodLabel(state.diff.before, null)) + ' → ' + esc(periodLabel(state.diff.after, null)) + '</span>' +
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
    '<select id="diff-sort">' +
      ['delta_abs|Sort: |Δ fair value|', 'delta_desc|Sort: largest increase|', 'delta_asc|Sort: largest decrease|', 'fv|Sort: current fair value|', 'positions|Sort: positions|', 'changed|Sort: fields changed|', 'name|Sort: company A–Z|'].map(o => {
        const parts = o.split('|');
        return '<option value="' + parts[0] + '"' + ((state.diff.sort || 'delta_abs') === parts[0] ? ' selected' : '') + '>' + parts[1] + parts[2] + '</option>';
      }).join('') +
    '</select>' +
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
  const sortSel = $('diff-sort');
  if (sortSel) sortSel.onchange = (e) => { state.diff.sort = e.target.value; renderDiffView(el); };
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

// ------------------------------------------------------------------ valuation & coverage
function renderValuation(el) {
  const a = state.analytics || {};
  const val = a.valuation || {};
  const qp = (a.quality_periods || []).slice(-12);
  const credit = a.credit || {};
  const finLatest = (state.financials && state.financials.periods && state.financials.periods.length)
    ? state.financials.periods[state.financials.periods.length - 1] : null;
  const holdingsFiled = a.meta && a.meta.latest_holdings_filing;
  const navPeriod = val.latest_period ? ((quarterInfoFromEnd(val.latest_period) || {}).label || val.latest_period) : '—';
  const finLabel = finLatest ? periodLabel(finLatest.filed || finLatest.end, finLatest.end) : '—';
  const cov = num(val.nii_coverage), pNav = num(val.p_nav), yld = num(val.dividend_yield_pct);

  let html = '<div class="asof">Market data as of <b>' + esc(val.price_asof || '—') + '</b> · NAV ' + esc(navPeriod) +
    ' · financials <b>' + filingLink(finLatest && finLatest.filed, finLabel) + '</b>' +
    ' · holdings <b>' + filingLink(holdingsFiled, periodLabel(holdingsFiled, a.meta && a.meta.latest_holdings_period_end)) + '</b></div>';

  html += '<div class="cards">' +
    card('Share Price', val.price != null ? '$' + num(val.price).toFixed(2) : '—', val.price_asof ? 'as of ' + val.price_asof : 'no market profile') +
    card('P/NAV', pNav !== null ? pNav.toFixed(2) + '×' : '—', val.premium_discount_pct != null ? signed(val.premium_discount_pct) + '% vs NAV' : '') +
    card('Dividend Yield', yld !== null ? fmtPct(yld) : '—', val.dividend_rate != null ? '$' + num(val.dividend_rate).toFixed(2) + ' annual' : '') +
    card('NII Coverage', cov !== null ? cov.toFixed(2) + '×' : '—', val.payout_ratio_pct != null ? 'payout ' + fmtPct(val.payout_ratio_pct) : '') +
    card('Distribution Rate', val.distribution_rate_pct != null ? fmtPct(val.distribution_rate_pct) : '—', 'annualized DPS / NAV') +
    card('NAV Total Return', val.nav_tr_4q_pct != null ? signed(val.nav_tr_4q_pct) + '%' : '—', val.nav_tr_1q_pct != null ? '1Q ' + signed(val.nav_tr_1q_pct) + '%' : '') +
    card('PIK Share of Income', val.pik_income_share_pct != null ? fmtPct(val.pik_income_share_pct) : '—', 'annualized PIK / total income') +
    card('Market Cap', val.market_cap != null ? fmtMoney(val.market_cap) : '—', val.beta != null ? 'beta ' + num(val.beta).toFixed(2) : '') +
    card('Non-Accrual (FV)', credit.non_accrual_fv_pct != null ? fmtPct(credit.non_accrual_fv_pct) : '—', credit.non_accrual_cost_pct != null ? 'cost ' + fmtPct(credit.non_accrual_cost_pct) : 'as disclosed') +
    card('Risk Rating', credit.risk_rating != null ? num(credit.risk_rating).toFixed(1) : '—', credit.risk_rating != null ? 'weighted avg, 1–5' : 'not disclosed') +
  '</div>';

  if (val.price == null) {
    html += '<div class="small muted" style="margin:-4px 0 10px">No market profile collected for this ticker; P/NAV and dividend yield need a share price.</div>';
  }
  if ((val.flags || []).length) {
    html += '<div class="flagbox">' + val.flags.map(f => '<div>⚠ ' + esc(f) + '</div>').join('') + '</div>';
  }

  const covPts = qp.filter(r => r.coverage !== null && r.coverage !== undefined).map(r => ({ label: (quarterInfoFromEnd(r.end) || {}).short || r.end, value: r.coverage }));
  const distPts = qp.filter(r => r.distribution_rate_pct !== null && r.distribution_rate_pct !== undefined).map(r => ({ label: (quarterInfoFromEnd(r.end) || {}).short || r.end, value: r.distribution_rate_pct }));
  const pikPts = qp.filter(r => r.pik_income_share_pct !== null && r.pik_income_share_pct !== undefined).map(r => ({ label: (quarterInfoFromEnd(r.end) || {}).short || r.end, value: r.pik_income_share_pct }));
  const trPts = qp.filter(r => r.nav_tr_pct !== null && r.nav_tr_pct !== undefined).map(r => ({ label: (quarterInfoFromEnd(r.end) || {}).short || r.end, value: r.nav_tr_pct }));

  html += '<div class="section"><h3>Distribution quality trends</h3><div class="grid2" style="grid-template-columns:repeat(auto-fit,minmax(250px,1fr))">';
  html += lineChart(covPts, { title: 'NII coverage of dividends', fmt: v => v.toFixed(2) + '×' });
  html += lineChart(distPts, { title: 'Distribution rate on NAV', fmt: v => v.toFixed(1) + '%' });
  html += lineChart(pikPts, { title: 'PIK share of investment income', fmt: v => v.toFixed(1) + '%' });
  html += signedBarChart(trPts, { title: 'Quarterly NAV total return', fmt: v => signed(v) + '%' });
  html += '</div></div>';

  if (qp.length) {
    html += '<div class="section"><h3>Quarterly distribution quality</h3><table><thead><tr>' +
      '<th>Quarter</th><th>Filing</th><th class="num">NAV / Sh</th><th class="num">NII / Sh</th><th class="num">DPS</th>' +
      '<th class="num">NII Coverage</th><th class="num">Dist. Rate</th><th class="num">NAV TR</th><th class="num">PIK Share</th>' +
      '</tr></thead><tbody>';
    for (const q of qp.slice().reverse()) {
      const qLabel = (quarterInfoFromEnd(q.end) || {}).label || q.end;
      html += '<tr><td>' + esc(qLabel) + '</td>' +
        '<td>' + (q.filed ? filingLink(q.filed, q.form || 'filing') : '—') + '</td>' +
        '<td class="num">' + (q.nav_ps != null ? '$' + num(q.nav_ps).toFixed(2) : '—') + '</td>' +
        '<td class="num">' + (q.nii_ps_q != null ? '$' + num(q.nii_ps_q).toFixed(2) : '—') + '</td>' +
        '<td class="num">' + (q.dps_q != null ? '$' + num(q.dps_q).toFixed(2) : '—') + '</td>' +
        '<td class="num ' + (num(q.coverage) !== null && num(q.coverage) < 1 ? 'neg' : '') + '">' + (q.coverage != null ? num(q.coverage).toFixed(2) + '×' : '—') + '</td>' +
        '<td class="num">' + (q.distribution_rate_pct != null ? fmtPct(q.distribution_rate_pct) : '—') + '</td>' +
        '<td class="num ' + (num(q.nav_tr_pct) < 0 ? 'neg' : 'pos') + '">' + (q.nav_tr_pct != null ? signed(q.nav_tr_pct) + '%' : '—') + '</td>' +
        '<td class="num">' + (q.pik_income_share_pct != null ? fmtPct(q.pik_income_share_pct) : '—') + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  html += '<div class="section"><h3>Credit quality — as disclosed</h3>';
  if (credit.filing) {
    const conf = credit.confidence || 'none';
    const badge = conf === 'high' ? 'good' : (conf === 'medium' ? 'warn' : 'bad');
    html += '<div class="small muted" style="margin-bottom:6px">From ' + filingLink(credit.filing.filed, (credit.filing.form || 'filing') + ' filed ' + (credit.filing.filed || '—')) +
      ' · extraction confidence <span class="badge ' + badge + '">' + esc(conf) + '</span></div>';
    html += '<div class="cards">' +
      card('Non-Accrual at Cost', credit.non_accrual_cost_pct != null ? fmtPct(credit.non_accrual_cost_pct) : '—', 'of total investments') +
      card('Non-Accrual at FV', credit.non_accrual_fv_pct != null ? fmtPct(credit.non_accrual_fv_pct) : '—', 'of total investments') +
      card('Non-Accrual Count', credit.non_accrual_count != null ? fmtNum(credit.non_accrual_count) : '—', 'issuers / investments') +
      card('Weighted Avg Rating', credit.risk_rating != null ? num(credit.risk_rating).toFixed(1) : '—', '1 = lowest risk, 5 = highest') +
    '</div>';
    const cs = credit.snippets || {};
    if (cs.non_accrual) html += '<details style="margin-top:6px"><summary class="linkish">Source disclosure — non-accrual</summary><div class="small muted" style="margin-top:4px;line-height:1.45">' + esc(cs.non_accrual) + '</div></details>';
    if (cs.risk_rating) html += '<details style="margin-top:4px"><summary class="linkish">Source disclosure — risk rating</summary><div class="small muted" style="margin-top:4px;line-height:1.45">' + esc(cs.risk_rating) + '</div></details>';
    if ((credit.notes || []).length) html += '<div class="small muted" style="margin-top:4px">' + (credit.notes || []).map(esc).join(' · ') + '</div>';
  } else {
    html += '<div class="muted small">No structured credit-quality disclosure extracted for this BDC. ' +
      (finLatest ? 'See the latest filing: ' + filingLink(finLatest.filed, (finLatest.form || 'filing') + ' filed ' + finLatest.filed) : '') + '</div>';
  }
  html += '</div>';

  const detailRows = [
    ['Share price', val.price != null ? '$' + num(val.price).toFixed(2) : '—'],
    ['Price as of', val.price_asof || '—'],
    ['52-week range', val.fifty_two_week_low != null && val.fifty_two_week_high != null ? '$' + num(val.fifty_two_week_low).toFixed(2) + ' – $' + num(val.fifty_two_week_high).toFixed(2) : '—'],
    ['Market cap', val.market_cap != null ? fmtMoney(val.market_cap) : '—'],
    ['Beta', val.beta != null ? num(val.beta).toFixed(2) : '—'],
    ['NAV / share (reported)', val.nav_per_share != null ? '$' + num(val.nav_per_share).toFixed(2) : '—'],
    ['P/NAV (Yahoo book)', val.p_nav_yahoo != null ? num(val.p_nav_yahoo).toFixed(2) + '×' : '—'],
    ['Annual dividend', val.dividend_rate != null ? '$' + num(val.dividend_rate).toFixed(2) : '—'],
  ];
  let detail = '<div class="section"><h3>Market & valuation detail</h3><table><tbody>';
  for (const r of detailRows) detail += '<tr><td>' + esc(r[0]) + '</td><td class="num">' + esc(r[1]) + '</td></tr>';
  detail += '</tbody></table></div>';

  let sources = '<div class="section"><h3>Sources & lineage</h3><table><tbody>';
  if (holdingsFiled) sources += '<tr><td>Holdings</td><td>' + filingLink(holdingsFiled, (filingFor(holdingsFiled) || {}).form + ' filed ' + holdingsFiled) + '</td></tr>';
  if (finLatest) sources += '<tr><td>Financial statements</td><td>' + filingLink(finLatest.filed, (finLatest.form || 'filing') + ' filed ' + (finLatest.filed || '—')) + '</td></tr>';
  if (credit.filing) sources += '<tr><td>Credit quality</td><td>' + filingLink(credit.filing.filed, (credit.filing.form || 'filing') + ' filed ' + (credit.filing.filed || '—')) + '</td></tr>';
  if (val.price_asof) sources += '<tr><td>Market data</td><td class="small muted">Yahoo Finance profile, as of ' + esc(val.price_asof) + '</td></tr>';
  if (val.coverage_period) sources += '<tr><td>Coverage quarter</td><td class="small muted">' + esc((quarterInfoFromEnd(val.coverage_period) || {}).label || val.coverage_period) + '</td></tr>';
  sources += '</tbody></table></div>';

  html += '<div class="grid2">' + detail + sources + '</div>';
  el.innerHTML = html;
}

// ------------------------------------------------------------------ financials

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
    for (const pe of periods) html += '<th class="num">' + filingLink(pe.filed, (quarterInfoFromEnd(pe.end) || {}).label || pe.end) + '<div class="small muted">' + esc([pe.form, pe.filed].filter(Boolean).join(' · ')) + '</div></th>';
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

// ------------------------------------------------------------------ peer evaluation
function peerByTicker(t) {
  return ((state.peers && state.peers.peers) || []).find(p => p.ticker === t) || null;
}
function cohortPeers() {
  const peers = ((state.peers && state.peers.peers) || []).slice();
  const c = state.peerCohort || 'all';
  if (c === 'large') return peers.filter(p => (num(p.fair_value) || 0) >= 5000000);
  if (c === 'mid') return peers.filter(p => (num(p.fair_value) || 0) >= 1000000 && (num(p.fair_value) || 0) < 5000000);
  if (c === 'small') return peers.filter(p => (num(p.fair_value) || 0) < 1000000);
  return peers;
}
function medianOf(values) {
  const vals = values
    .filter(v => v !== null && v !== undefined && v !== '' && !isNaN(Number(v)))
    .map(Number)
    .sort((a, b) => a - b);
  if (!vals.length) return null;
  const n = vals.length, m = Math.floor(n / 2);
  return n % 2 ? vals[m] : (vals[m - 1] + vals[m]) / 2;
}
const PEER_METRICS = [
  { key: 'fair_value', label: 'Portfolio fair value', fmt: fmtK, higher: true },
  { key: 'first_lien_pct', label: 'First lien weight', fmt: fmtPct, higher: true },
  { key: 'second_lien_pct', label: 'Second lien weight', fmt: fmtPct, higher: true },
  { key: 'equity_pct', label: 'Equity & structured', fmt: fmtPct, higher: false },
  { key: 'weighted_avg_cash_rate', label: 'Weighted cash rate', fmt: fmtRate, higher: true },
  { key: 'weighted_avg_spread', label: 'Weighted spread', fmt: fmtRate, higher: true },
  { key: 'pik_share_pct', label: 'PIK share', fmt: fmtPct, higher: false },
  { key: 'floating_pct', label: 'Floating-rate weight', fmt: fmtPct, higher: true },
  { key: 'top10_pct', label: 'Top-10 concentration', fmt: fmtPct, higher: false },
  { key: 'debt_to_equity', label: 'Debt / equity', fmt: v => v === null || v === undefined ? '—' : num(v).toFixed(2) + '×', higher: false },
  { key: 'asset_coverage', label: 'Asset coverage', fmt: v => v === null || v === undefined ? '—' : num(v).toFixed(2) + '×', higher: true },
  { key: 'nii_per_share', label: 'NII / share', fmt: v => v === null || v === undefined ? '—' : '$' + num(v).toFixed(2), higher: true },
  { key: 'unrealized_gl_pct', label: 'Unrealized G/L', fmt: v => v === null || v === undefined ? '—' : signed(v) + '%', higher: true },
  { key: 'median_vintage_year', label: 'Median vintage', fmt: v => v === null || v === undefined ? '—' : String(v), higher: true },
  { key: 'age_0_2_pct', label: 'Acquired < 2 yrs', fmt: fmtPct, higher: true },
  { key: 'age_5plus_pct', label: 'Age 5+ yrs', fmt: fmtPct, higher: false },
  { key: 'total_net_change', label: 'QoQ net FV change', fmt: fmtK, higher: true },
  { key: 'p_nav', label: 'P/NAV (lower = cheaper)', fmt: v => v === null || v === undefined ? '—' : num(v).toFixed(2) + '×', higher: false },
  { key: 'dividend_yield_pct', label: 'Dividend yield', fmt: fmtPct, higher: true },
  { key: 'nii_coverage', label: 'NII coverage', fmt: v => v === null || v === undefined ? '—' : num(v).toFixed(2) + '×', higher: true },
  { key: 'payout_ratio_pct', label: 'Payout ratio', fmt: fmtPct, higher: false },
  { key: 'pik_income_share_pct', label: 'PIK share of income', fmt: fmtPct, higher: false },
  { key: 'nav_tr_4q_pct', label: 'NAV total return (4Q)', fmt: v => v === null || v === undefined ? '—' : signed(v) + '%', higher: true },
  { key: 'non_accrual_fv_pct', label: 'Non-accrual (FV)', fmt: fmtPct, higher: false },
  { key: 'risk_rating', label: 'Risk rating (1 = low)', fmt: v => v === null || v === undefined ? '—' : num(v).toFixed(1), higher: false },
  { key: 'market_cap', label: 'Market cap', fmt: fmtMoney, higher: true },
];
function peerMetricDef(key) {
  const found = PEER_METRICS.find(m => m.key === key);
  if (found) return found;
  if (key === 'companies') return { key: 'companies', label: 'Companies', fmt: fmtNum, higher: true };
  return { key: key, label: key, fmt: v => String(v), higher: true };
}
const PEER_PRESETS = [
  { id: 'yield_leverage', label: 'Yield vs leverage', x: 'weighted_avg_cash_rate', y: 'debt_to_equity', color: 'unrealized_gl_pct', colorLabel: 'unrealized G/L (red negative / green positive)' },
  { id: 'mix_yield', label: 'Mix vs yield', x: 'first_lien_pct', y: 'weighted_avg_cash_rate', color: 'pik_share_pct', colorLabel: 'PIK share' },
  { id: 'size_concentration', label: 'Size vs concentration', x: 'fair_value', y: 'top10_pct', color: 'first_lien_pct', colorLabel: 'first lien weight' },
  { id: 'vintage_marks', label: 'Vintage vs marks', x: 'median_vintage_year', y: 'unrealized_gl_pct', color: 'age_5plus_pct', colorLabel: 'age 5+ share' },
  { id: 'structure_pik', label: 'Structure vs PIK', x: 'floating_pct', y: 'pik_share_pct', color: 'equity_pct', colorLabel: 'equity weight' },
  { id: 'coverage_nii', label: 'Coverage vs NII', x: 'asset_coverage', y: 'nii_per_share', color: 'debt_to_equity', colorLabel: 'leverage' },
  { id: 'valuation_coverage', label: 'P/NAV vs coverage', x: 'p_nav', y: 'nii_coverage', color: 'dividend_yield_pct', colorLabel: 'dividend yield' },
  { id: 'valuation_yield', label: 'P/NAV vs yield', x: 'p_nav', y: 'dividend_yield_pct', color: 'nii_coverage', colorLabel: 'NII coverage' },
  { id: 'credit_valuation', label: 'Non-accrual vs P/NAV', x: 'non_accrual_fv_pct', y: 'p_nav', color: 'risk_rating', colorLabel: 'risk rating' },
];
const RADAR_DIMS = [
  { key: 'weighted_avg_cash_rate', label: 'Yield', higher: true },
  { key: 'first_lien_pct', label: 'First lien', higher: true },
  { key: 'floating_pct', label: 'Floating', higher: true },
  { key: 'asset_coverage', label: 'Coverage', higher: true },
  { key: 'debt_to_equity', label: 'Low leverage', higher: false },
  { key: 'nii_coverage', label: 'NII coverage', higher: true },
  { key: 'p_nav', label: 'P/NAV', higher: false },
  { key: 'unrealized_gl_pct', label: 'Marks', higher: true },
  { key: 'median_vintage_year', label: 'Freshness', higher: true },
];
const HEAT_COLS = [
  ['fair_value', 'Fair Value', true, fmtK],
  ['first_lien_pct', '1st Lien', true, fmtPct],
  ['weighted_avg_cash_rate', 'Cash %', true, fmtRate],
  ['weighted_avg_spread', 'Spread', true, fmtRate],
  ['pik_share_pct', 'PIK', false, fmtPct],
  ['floating_pct', 'Floating', true, fmtPct],
  ['top10_pct', 'Top10', false, fmtPct],
  ['debt_to_equity', 'D/E', false, v => num(v).toFixed(2) + '×'],
  ['asset_coverage', 'Cover', true, v => num(v).toFixed(2) + '×'],
  ['nii_per_share', 'NII', true, v => '$' + num(v).toFixed(2)],
  ['unrealized_gl_pct', 'G/L', true, v => signed(v) + '%'],
  ['age_5plus_pct', '5y+', false, fmtPct],
  ['total_net_change', 'QoQ Δ', true, fmtK],
  ['p_nav', 'P/NAV', false, v => num(v).toFixed(2) + '×'],
  ['dividend_yield_pct', 'Yield', true, fmtPct],
  ['nii_coverage', 'NII Cov', true, v => num(v).toFixed(2) + '×'],
  ['non_accrual_fv_pct', 'NonAccr', false, fmtPct],
  ['risk_rating', 'Risk', false, v => num(v).toFixed(1)],
];
function percentileRank(key, value, cohort, higher) {
  const vals = cohort.map(p => num(p[key])).filter(v => v !== null);
  if (vals.length < 2) return 50;
  const worse = vals.filter(v => higher ? v < value : v > value).length;
  return Math.round(worse / (vals.length - 1) * 100);
}
function heatStyle(key, value, cohort, higher) {
  if (value === null) return '';
  const pct = percentileRank(key, value, cohort, higher);
  const alpha = 0.05 + Math.abs(pct - 50) / 50 * 0.22;
  return pct >= 50
    ? 'background:rgba(29,122,79,' + alpha.toFixed(2) + ');'
    : 'background:rgba(161,51,51,' + alpha.toFixed(2) + ');';
}
function peerColor(key, v, cohort) {
  if (v === null) return '#9fb0c9';
  if (key === 'unrealized_gl_pct') return Number(v) < 0 ? '#a13333' : '#1d7a4f';
  const vals = cohort.map(p => num(p[key])).filter(x => x !== null);
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const t = (Number(v) - lo) / ((hi - lo) || 1);
  return t < 0.34 ? '#9fb0c9' : t < 0.67 ? '#c08a2d' : '#7a4b8f';
}
function scatterChart() {
  const cohort = cohortPeers();
  const defX = peerMetricDef(state.peerX), defY = peerMetricDef(state.peerY);
  const pts = cohort.map(p => ({ p: p, x: num(p[state.peerX]), y: num(p[state.peerY]) })).filter(d => d.x !== null && d.y !== null);
  if (pts.length < 2) return '<div class="empty">Not enough data for this view in the cohort.</div>';
  const xs = pts.map(d => d.x), ys = pts.map(d => d.y);
  const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  const padX = (maxX - minX) * 0.08 || 1, padY = (maxY - minY) * 0.08 || 1;
  const x0 = minX - padX, x1 = maxX + padX, y0 = minY - padY, y1 = maxY + padY;
  const W = 780, H = 430, L = 64, R = 18, T = 16, B = 44;
  const sx = v => L + (v - x0) / (x1 - x0) * (W - L - R);
  const sy = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const medX = medianOf(xs), medY = medianOf(ys);
  const tickX = state.peerX === 'median_vintage_year' ? (v => String(Math.round(v))) : defX.fmt;
  const tickY = state.peerY === 'median_vintage_year' ? (v => String(Math.round(v))) : defY.fmt;
  const maxSize = Math.max.apply(null, cohort.map(p => num(p.fair_value) || 0).concat([1]));
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="scatter-svg" role="img" aria-label="Peer positioning scatter">';
  svg += '<rect x="' + L + '" y="' + T + '" width="' + (W - L - R) + '" height="' + (H - T - B) + '" fill="#fbfcfd" stroke="#dde1e7"/>';
  for (let i = 0; i <= 4; i++) {
    const xv = x0 + (x1 - x0) * i / 4;
    const yv = y0 + (y1 - y0) * i / 4;
    const x = sx(xv), y = sy(yv);
    svg += '<line x1="' + x.toFixed(1) + '" y1="' + T + '" x2="' + x.toFixed(1) + '" y2="' + (H - B) + '" stroke="#e6e9ee"/>';
    svg += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) + '" stroke="#e6e9ee"/>';
    svg += '<text x="' + x.toFixed(1) + '" y="' + (H - B + 14) + '" text-anchor="middle" font-size="9.5" fill="#7b8490">' + esc(tickX(xv)) + '</text>';
    svg += '<text x="' + (L - 8) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end" font-size="9.5" fill="#7b8490">' + esc(tickY(yv)) + '</text>';
  }
  svg += '<line x1="' + sx(medX).toFixed(1) + '" y1="' + T + '" x2="' + sx(medX).toFixed(1) + '" y2="' + (H - B) + '" stroke="#c08a2d" stroke-dasharray="4 3" stroke-width="1"/>';
  svg += '<line x1="' + L + '" y1="' + sy(medY).toFixed(1) + '" x2="' + (W - R) + '" y2="' + sy(medY).toFixed(1) + '" stroke="#c08a2d" stroke-dasharray="4 3" stroke-width="1"/>';
  svg += '<text x="' + (sx(medX) + 4).toFixed(1) + '" y="' + (T + 11) + '" font-size="9" fill="#a87722">median ' + esc(defX.label.toLowerCase()) + '</text>';
  svg += '<text x="' + (L + 4) + '" y="' + (sy(medY) - 4).toFixed(1) + '" font-size="9" fill="#a87722">median ' + esc(defY.label.toLowerCase()) + '</text>';
  for (const d of pts) {
    const focus = d.p.ticker === state.peerFocus;
    let r = 4.3;
    if (state.peerChartMode === '3d') r = 4 + Math.sqrt(Math.max(0, num(d.p.fair_value) || 0) / maxSize) * 9;
    const color = state.peerChartMode === '3d' ? peerColor(state.peerColorKey, num(d.p[state.peerColorKey]), cohort) : '#24457c';
    const tip = d.p.ticker + ': ' + defX.label + ' ' + defX.fmt(d.x) + ' · ' + defY.label + ' ' + defY.fmt(d.y) + ' · FV ' + fmtK(d.p.fair_value);
    svg += '<circle class="scatter-dot' + (focus ? ' focus' : '') + '" cx="' + sx(d.x).toFixed(1) + '" cy="' + sy(d.y).toFixed(1) + '" r="' + (focus ? r + 3 : r).toFixed(1) + '" fill="' + color + '" fill-opacity="' + (focus ? 1 : 0.72) + '" stroke="' + (focus ? '#c08a2d' : '#ffffff') + '" stroke-width="' + (focus ? 2.2 : 0.8) + '" data-focus="' + d.p.ticker + '"><title>' + esc(tip) + '</title></circle>';
    if (focus) {
      const lx = Math.min(sx(d.x) + r + 7, W - 62);
      svg += '<text x="' + lx.toFixed(1) + '" y="' + (sy(d.y) + 3.5).toFixed(1) + '" font-size="10.5" font-weight="700" fill="#1a2f5a">' + esc(d.p.ticker) + '</text>';
    }
  }
  svg += '<text x="' + (L + (W - L - R) / 2) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10.5" fill="#5c6570">' + esc(defX.label) + ' →</text>';
  const cy = T + (H - T - B) / 2;
  svg += '<text x="15" y="' + cy + '" transform="rotate(-90 15 ' + cy + ')" text-anchor="middle" font-size="10.5" fill="#5c6570">' + esc(defY.label) + ' →</text>';
  return svg + '</svg>';
}
function nearestPeersHtml() {
  const cohort = cohortPeers();
  const focus = peerByTicker(state.peerFocus);
  if (!focus) return '';
  const fx = num(focus[state.peerX]), fy = num(focus[state.peerY]);
  if (fx === null || fy === null) return '<div class="empty">Focus BDC has no data on these axes.</div>';
  const defX = peerMetricDef(state.peerX), defY = peerMetricDef(state.peerY);
  const pts = cohort.map(p => ({ p: p, x: num(p[state.peerX]), y: num(p[state.peerY]) })).filter(d => d.x !== null && d.y !== null);
  const xs = pts.map(d => d.x), ys = pts.map(d => d.y);
  const rx = (Math.max.apply(null, xs) - Math.min.apply(null, xs)) || 1;
  const ry = (Math.max.apply(null, ys) - Math.min.apply(null, ys)) || 1;
  const rows = pts.filter(d => d.p.ticker !== focus.ticker).map(d => {
    const dx = (d.x - fx) / rx, dy = (d.y - fy) / ry;
    return { p: d.p, x: d.x, y: d.y, dist: Math.sqrt(dx * dx + dy * dy) };
  }).sort((a, b) => a.dist - b.dist).slice(0, 6);
  let html = '<div class="small muted" style="margin-bottom:4px">Closest peers in this view</div><table class="mini"><thead><tr><th>Ticker</th><th class="num">' + esc(defX.label) + '</th><th class="num">' + esc(defY.label) + '</th></tr></thead><tbody>';
  for (const r of rows) {
    html += '<tr class="clickrow" data-focus="' + r.p.ticker + '"><td><b>' + r.p.ticker + '</b></td><td class="num">' + esc(defX.fmt(r.x)) + '</td><td class="num">' + esc(defY.fmt(r.y)) + '</td></tr>';
  }
  return html + '</tbody></table>';
}
function radarSvg() {
  const cohort = cohortPeers();
  const focus = peerByTicker(state.peerFocus);
  const cx = 180, cy = 165, R = 120;
  const n = RADAR_DIMS.length;
  const dims = RADAR_DIMS.map((d, i) => {
    const v = focus ? num(focus[d.key]) : null;
    const pct = v === null ? 0 : percentileRank(d.key, v, cohort, d.higher);
    const ang = -Math.PI / 2 + i * 2 * Math.PI / n;
    return { d: d, v: v, pct: pct, ang: ang, x: cx + Math.cos(ang) * pct / 100 * R, y: cy + Math.sin(ang) * pct / 100 * R,
      lx: cx + Math.cos(ang) * (R + 20), ly: cy + Math.sin(ang) * (R + 20),
      ax: cx + Math.cos(ang) * R, ay: cy + Math.sin(ang) * R };
  });
  let svg = '<svg viewBox="0 0 360 330" class="radar-svg" role="img" aria-label="Percentile profile radar">';
  for (const ring of [25, 50, 75, 100]) {
    const pts = [];
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + i * 2 * Math.PI / n;
      pts.push((cx + Math.cos(a) * R * ring / 100).toFixed(1) + ',' + (cy + Math.sin(a) * R * ring / 100).toFixed(1));
    }
    svg += '<polygon points="' + pts.join(' ') + '" fill="none" stroke="' + (ring === 50 ? '#c08a2d' : '#dfe3e9') + '" stroke-dasharray="' + (ring === 50 ? '4 3' : '0') + '"/>';
  }
  for (const d of dims) {
    svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + d.ax.toFixed(1) + '" y2="' + d.ay.toFixed(1) + '" stroke="#e3e6eb"/>';
    const anchor = Math.cos(d.ang) > 0.3 ? 'start' : (Math.cos(d.ang) < -0.3 ? 'end' : 'middle');
    svg += '<text x="' + d.lx.toFixed(1) + '" y="' + (d.ly + 3).toFixed(1) + '" text-anchor="' + anchor + '" font-size="9.5" fill="#5c6570">' + esc(d.d.label) + '</text>';
  }
  svg += '<polygon points="' + dims.map(d => d.x.toFixed(1) + ',' + d.y.toFixed(1)).join(' ') + '" fill="rgba(36,69,124,.18)" stroke="#24457c" stroke-width="2"/>';
  for (const d of dims) {
    svg += '<circle cx="' + d.x.toFixed(1) + '" cy="' + d.y.toFixed(1) + '" r="3.2" fill="#24457c"><title>' + esc(d.d.label + ': ' + (d.v === null ? 'no data' : peerMetricDef(d.d.key).fmt(d.v)) + ' · percentile ' + d.pct) + '</title></circle>';
  }
  svg += '<text x="' + cx + '" y="' + (cy + 4) + '" text-anchor="middle" font-size="9" fill="#8a929c">50th pct ring</text>';
  return svg + '</svg>';
}
function radarTableHtml() {
  const cohort = cohortPeers();
  const focus = peerByTicker(state.peerFocus);
  let html = '<table class="mini"><thead><tr><th>Metric</th><th class="num">Focus</th><th class="num">Median</th><th>Percentile</th></tr></thead><tbody>';
  for (const d of RADAR_DIMS) {
    const v = focus ? num(focus[d.key]) : null;
    const med = medianOf(cohort.map(p => num(p[d.key])).filter(x => x !== null));
    const pct = v === null ? 0 : percentileRank(d.key, v, cohort, d.higher);
    const fmt = peerMetricDef(d.key).fmt;
    html += '<tr><td>' + esc(d.label) + '</td><td class="num">' + (v === null ? '—' : esc(fmt(v))) + '</td>' +
      '<td class="num">' + (med === null ? '—' : esc(fmt(med))) + '</td>' +
      '<td>' + (v === null ? '<span class="small muted">no data</span>' : '<div class="pct-bar"><div class="pct-fill" style="width:' + pct + '%"></div></div><span class="small muted">' + pct + '</span>') + '</td></tr>';
  }
  return html + '</tbody></table>';
}
function peerHeatmapHtml() {
  const cohort = cohortPeers().slice();
  const focus = state.peerFocus;
  const key = state.peerHeatKey || 'fair_value';
  const dir = state.peerHeatDir || -1;
  cohort.sort((a, b) => {
    const av = num(a[key]), bv = num(b[key]);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return (av - bv) * dir;
  });
  let html = '<div class="heatmap-wrap"><table id="peer-heatmap"><thead><tr><th class="sticky-col">Ticker</th>';
  for (const c of HEAT_COLS) {
    html += '<th class="num sortable" data-heat="' + c[0] + '">' + c[1] + (key === c[0] ? (dir === -1 ? ' ▼' : ' ▲') : '') + '</th>';
  }
  html += '</tr></thead><tbody>';
  for (const r of cohort) {
    html += '<tr class="clickrow' + (r.ticker === focus ? ' heat-focus' : '') + '" data-focus="' + r.ticker + '"><td class="sticky-col"><b>' + r.ticker + '</b></td>';
    for (const c of HEAT_COLS) {
      const v = num(r[c[0]]);
      if (v === null) { html += '<td class="num">—</td>'; continue; }
      const style = heatStyle(c[0], v, cohort, c[2]);
      const pct = percentileRank(c[0], v, cohort, c[2]);
      html += '<td class="num heat" style="' + style + '" title="' + esc(c[1] + ' percentile ' + pct) + '">' + esc(c[3](v)) + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody><tfoot><tr><td class="sticky-col">Median</td>';
  for (const c of HEAT_COLS) {
    const med = medianOf(cohort.map(p => num(p[c[0]])).filter(x => x !== null));
    html += '<td class="num">' + (med === null ? '—' : esc(c[3](med))) + '</td>';
  }
  return html + '</tr></tfoot></table></div>';
}
function leaderboard(title, key, higher, fmt) {
  const list = cohortPeers()
    .filter(p => p[key] !== null && p[key] !== undefined && !isNaN(Number(p[key])))
    .sort((a, b) => higher ? Number(b[key]) - Number(a[key]) : Number(a[key]) - Number(b[key]));
  const top = list.slice(0, 5);
  const focusRank = list.findIndex(p => p.ticker === state.peerFocus) + 1;
  const focus = peerByTicker(state.peerFocus);
  const max = Math.max.apply(null, top.map(p => Math.abs(Number(p[key]))).concat([1]));
  let html = '<div class="section"><h3>' + esc(title) + '</h3><div class="leaderboard">';
  for (let i = 0; i < top.length; i++) {
    const p = top[i];
    const w = Math.max(2, Math.abs(Number(p[key])) / max * 100);
    html += '<div class="lb-row' + (p.ticker === state.peerFocus ? ' me' : '') + '" data-focus="' + p.ticker + '">' +
      '<span class="lb-rank">' + (i + 1) + '</span><span class="lb-tkr">' + p.ticker + '</span>' +
      '<span class="lb-name">' + esc((state.profiles[p.ticker] || {}).name || '') + '</span>' +
      '<span class="lb-bar"><span style="width:' + w.toFixed(1) + '%"></span></span>' +
      '<span class="lb-val">' + esc(fmt(p[key])) + '</span></div>';
  }
  if (focus && focusRank > 5) {
    const fv = Number(focus[key]);
    const w = Math.max(2, Math.abs(fv) / max * 100);
    html += '<div class="lb-row me" data-focus="' + focus.ticker + '"><span class="lb-rank">' + focusRank + '</span>' +
      '<span class="lb-tkr">' + focus.ticker + '</span><span class="lb-name">' + esc((state.profiles[focus.ticker] || {}).name || '') + '</span>' +
      '<span class="lb-bar"><span style="width:' + Math.min(100, w).toFixed(1) + '%"></span></span>' +
      '<span class="lb-val">' + esc(fmt(focus[key])) + '</span></div>';
  }
  return html + '</div></div>';
}
function peerCell(key, v, row) {
  if (key === 'period') return row ? periodLabelShort(row.period, row.period_end) : '—';
  if (v === null || v === undefined || v === '') return '—';
  if (key === 'ticker') return esc(v);
  if (key === 'fair_value' || key === 'total_net_change') return fmtK(v);
  if (key === 'p_nav' || key === 'nii_coverage') return v === null || v === undefined ? '—' : num(v).toFixed(2) + '×';
  if (key === 'dividend_yield_pct' || key === 'non_accrual_fv_pct' || key === 'payout_ratio_pct') return fmtPct(v);
  if (key === 'risk_rating') return v === null || v === undefined ? '—' : num(v).toFixed(1);
  if (key === 'nav_tr_4q_pct') return v === null || v === undefined ? '—' : '<span class="' + (num(v) < 0 ? 'neg' : 'pos') + '">' + signed(v) + '%</span>';
  if (key === 'market_cap') return fmtMoney(v);
  if (key === 'first_lien_pct' || key === 'second_lien_pct' || key === 'subordinated_pct' || key === 'equity_pct' || key === 'pik_share_pct' || key === 'floating_pct' || key === 'top10_pct' || key === 'age_0_2_pct' || key === 'age_5plus_pct') return fmtPct(v);
  if (key === 'unrealized_gl_pct') return '<span class="' + (num(v) < 0 ? 'neg' : 'pos') + '">' + signed(v) + '%</span>';
  if (key === 'weighted_avg_cash_rate' || key === 'weighted_avg_spread') return fmtRate(v);
  if (key === 'nav_per_share' || key === 'nii_per_share') return v === null ? '—' : '$' + num(v).toFixed(2);
  if (key === 'debt_to_equity' || key === 'asset_coverage') return v === null ? '—' : num(v).toFixed(2) + '×';
  if (key === 'balance_check_ok') return v === true ? '<span class="badge good">✓</span>' : (v === false ? '<span class="badge bad">✗</span>' : '—');
  if (key === 'median_vintage_year' || key === 'companies' || key === 'fin_periods') return String(v);
  return esc(String(v));
}
function peerTableHtml() {
  const cohort = cohortPeers().slice();
  const cols = [
    ['ticker', 'Ticker'], ['period', 'Filing'], ['fair_value', 'Fair Value'], ['companies', 'Cos'],
    ['first_lien_pct', '1st Lien %'], ['second_lien_pct', '2nd Lien %'], ['subordinated_pct', 'Subord %'],
    ['equity_pct', 'Equity %'], ['weighted_avg_cash_rate', 'Cash %'], ['weighted_avg_spread', 'Spread %'],
    ['pik_share_pct', 'PIK %'], ['floating_pct', 'Floating %'], ['top10_pct', 'Top10 %'],
    ['unrealized_gl_pct', 'G/L %'], ['median_vintage_year', 'Vintage'], ['age_0_2_pct', '<2yr %'],
    ['age_5plus_pct', '5yr+ %'], ['nav_per_share', 'NAV/Sh'], ['debt_to_equity', 'D/E'],
    ['asset_coverage', 'Cover'], ['nii_per_share', 'NII/Sh'], ['total_net_change', 'QoQ Δ FV'],
    ['p_nav', 'P/NAV'], ['dividend_yield_pct', 'Yield'], ['nii_coverage', 'NII Cov'],
    ['non_accrual_fv_pct', 'NonAccr'], ['risk_rating', 'Risk'],
    ['balance_check_ok', 'Bal'], ['fin_periods', 'Fin Qs'],
  ];
  if (state.sortKey) {
    const k = state.sortKey;
    cohort.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * state.sortDir;
      return String(av === null || av === undefined ? '' : av).localeCompare(String(bv === null || bv === undefined ? '' : bv)) * state.sortDir;
    });
  }
  const med = {};
  for (const c of cols) med[c[0]] = medianOf(cohort.map(p => p[c[0]]));
  let html = '<div class="muted small" style="margin-bottom:6px">' + cohort.length + ' BDCs in cohort. Click a column to sort, a row to set the peer focus.</div>';
  html += '<table id="peers-table"><thead><tr>';
  for (const c of cols) html += '<th class="sortable num" data-sort="' + c[0] + '">' + c[1] + (state.sortKey === c[0] ? (state.sortDir === 1 ? ' ▲' : ' ▼') : '') + '</th>';
  html += '</tr></thead><tbody>';
  for (const r of cohort) {
    html += '<tr style="cursor:pointer" data-focus="' + r.ticker + '">';
    for (const c of cols) html += '<td class="num">' + peerCell(c[0], r[c[0]], r) + '</td>';
    html += '</tr>';
  }
  html += '</tbody><tfoot><tr><td>Median</td><td>—</td>';
  for (const c of cols.slice(2)) html += '<td class="num">' + peerCell(c[0], med[c[0]], null) + '</td>';
  return html + '</tr></tfoot></table>';
}
function bindPeerTable(el) {
  el.querySelectorAll('#peers-table th[data-sort]').forEach(th => {
    th.onclick = () => {
      const key = th.getAttribute('data-sort');
      if (state.sortKey === key) state.sortDir = -state.sortDir; else { state.sortKey = key; state.sortDir = 1; }
      renderPeerEvaluation($('main'));
    };
  });
}
async function ensurePeerFocusAnalytics() {
  const t = state.peerFocus;
  if (!t) return null;
  if (!state.peerFocusAnalytics) state.peerFocusAnalytics = {};
  if (state.peerFocusAnalytics[t]) return state.peerFocusAnalytics[t];
  const res = await fetch('/api/tickers/' + t + '/analytics');
  const data = res.ok ? await res.json() : null;
  state.peerFocusAnalytics[t] = data;
  return data;
}
async function renderPeerEvaluation(el) {
  await ensurePeers();
  const peers = state.peers.peers || [];
  if (!peers.length) { el.innerHTML = '<div class="empty">No peer data.</div>'; return; }
  if (!state.peerFocus || !peers.some(p => p.ticker === state.peerFocus)) {
    state.peerFocus = (state.ticker && peers.some(p => p.ticker === state.ticker)) ? state.ticker : peers[0].ticker;
  }
  const focus = peerByTicker(state.peerFocus);
  const cohort = cohortPeers();
  const analytics = await ensurePeerFocusAnalytics();
  const prof = state.profiles[state.peerFocus] || {};
  const latest = analytics && analytics.periods && analytics.periods.length ? analytics.periods[analytics.periods.length - 1] : null;

  let html = '<div class="peer-focus">' +
    '<div><div class="pf-name">' + state.peerFocus + ' — ' + esc(prof.name || '') + '</div>' +
    '<div class="small muted">Latest holdings ' + esc(periodLabel(focus.period, focus.period_end)) +
    (focus.fin_latest ? ' · financials through ' + esc((quarterInfoFromEnd(focus.fin_latest) || {}).label || focus.fin_latest) : '') +
    ' · ' + (focus.fin_periods || 0) + ' financial quarters</div></div>' +
    '<div class="small" style="display:flex;gap:14px;flex-wrap:wrap;align-items:center">' +
      '<span>FV <b>' + fmtK(focus.fair_value) + '</b></span>' +
      '<span>Companies <b>' + fmtNum(focus.companies) + '</b></span>' +
      '<span>NAV <b>' + (focus.nav_per_share === null || focus.nav_per_share === undefined ? '—' : '$' + num(focus.nav_per_share).toFixed(2)) + '</b></span>' +
      '<span>D/E <b>' + (focus.debt_to_equity === null || focus.debt_to_equity === undefined ? '—' : num(focus.debt_to_equity).toFixed(2) + '×') + '</b></span>' +
      '<span>NII <b>' + (focus.nii_per_share === null || focus.nii_per_share === undefined ? '—' : '$' + num(focus.nii_per_share).toFixed(2)) + '</b></span>' +
      '<span>P/NAV <b>' + (focus.p_nav == null ? '—' : num(focus.p_nav).toFixed(2) + '×') + '</b></span>' +
      '<span>Yield <b>' + (focus.dividend_yield_pct == null ? '—' : fmtPct(focus.dividend_yield_pct)) + '</b></span>' +
      '<span>Coverage <b>' + (focus.nii_coverage == null ? '—' : num(focus.nii_coverage).toFixed(2) + '×') + '</b></span>' +
    '</div>' +
    '<button class="btn" id="peer-open-bdc">Open BDC view</button>' +
  '</div>';

  html += '<div class="diff-bar">' +
    ['all|All BDCs', 'large|Large ($5B+)', 'mid|Mid ($1–5B)', 'small|Small (<$1B)'].map(o => {
      const parts = o.split('|');
      return '<button class="chip-btn' + ((state.peerCohort || 'all') === parts[0] ? ' active' : '') + '" data-cohort="' + parts[0] + '">' + parts[1] + '</button>';
    }).join('') +
    '<span class="small muted">' + cohort.length + ' BDCs in cohort</span>' +
  '</div>';

  // Positioning scatter
  const preset = PEER_PRESETS.find(p => p.id === state.peerPreset);
  const colorLabel = preset ? preset.colorLabel : peerMetricDef(state.peerColorKey).label;
  const plottedCount = cohort.filter(p => num(p[state.peerX]) !== null && num(p[state.peerY]) !== null).length;
  html += '<div class="section"><h3>Peer positioning</h3>';
  html += '<div class="diff-bar">' +
    PEER_PRESETS.map(p => '<button class="chip-btn' + (state.peerPreset === p.id ? ' active' : '') + '" data-preset="' + p.id + '">' + esc(p.label) + '</button>').join('') +
    '<span style="flex:1"></span>' +
    '<button class="chip-btn' + (state.peerChartMode === '2d' ? ' active' : '') + '" data-chart-mode="2d">2D dots</button>' +
    '<button class="chip-btn' + (state.peerChartMode === '3d' ? ' active' : '') + '" data-chart-mode="3d">3D bubbles</button>' +
  '</div>';
  html += '<div class="diff-bar">' +
    '<label class="small muted">X <select id="peer-x">' + PEER_METRICS.concat([{ key: 'companies', label: 'Companies' }]).map(m => '<option value="' + m.key + '"' + (state.peerX === m.key ? ' selected' : '') + '>' + esc(m.label) + '</option>').join('') + '</select></label>' +
    '<label class="small muted">Y <select id="peer-y">' + PEER_METRICS.concat([{ key: 'companies', label: 'Companies' }]).map(m => '<option value="' + m.key + '"' + (state.peerY === m.key ? ' selected' : '') + '>' + esc(m.label) + '</option>').join('') + '</select></label>' +
    '<span class="small muted">' + (state.peerChartMode === '3d' ? 'Bubble size = portfolio fair value · Color = ' + esc(colorLabel) : '2D view · dot color = focus BDC in gold') + ' · ' + plottedCount + ' of ' + cohort.length + ' plotted (missing axis data omitted)</span>' +
  '</div>';
  html += '<div class="chart-split"><div class="chart-main">' + scatterChart() + '</div><div class="chart-side">' + nearestPeersHtml() + '</div></div>';
  html += '</div>';

  // Radar profile
  html += '<div class="section"><h3>Percentile profile</h3>';
  html += '<div class="chart-split" style="grid-template-columns:360px 1fr"><div class="chart-main">' + radarSvg() + '</div><div class="chart-side">' + radarTableHtml() + '</div></div>';
  html += '<div class="small muted">Percentile within the ' + cohort.length + '-BDC cohort; the dashed ring is the 50th percentile. Higher is better on every axis (leverage is inverted).</div></div>';

  // Heatmap
  html += '<div class="section"><h3>Peer scorecard heatmap</h3><div class="small muted" style="margin-bottom:4px">Green = better percentile in the cohort, red = worse. Click a row to focus that BDC, a column to sort.</div>' + peerHeatmapHtml() + '</div>';

  // Leaderboards
  html += '<div class="grid3">';
  html += leaderboard('Highest cash rate', 'weighted_avg_cash_rate', true, fmtRate);
  html += leaderboard('Most first-lien', 'first_lien_pct', true, fmtPct);
  html += leaderboard('Lowest leverage', 'debt_to_equity', false, v => num(v).toFixed(2) + '×');
  html += leaderboard('Newest portfolio', 'median_vintage_year', true, v => String(v));
  html += leaderboard('Least concentrated', 'top10_pct', false, fmtPct);
  html += leaderboard('Largest portfolios', 'fair_value', true, fmtK);
  html += '</div>';

  // Sector tilt
  if (latest) {
    const medMap = {};
    for (const s of (state.peers.sector_medians || [])) medMap[s.sector] = s.median_pct;
    html += '<div class="section"><h3>Sector tilt vs peer median — ' + esc(periodLabel(latest.filing_date, latest.period_end)) + '</h3>';
    html += '<table class="tilt-table"><thead><tr><th>Sector</th><th class="num">Focus %</th><th class="num">Peer median</th><th class="num">Tilt</th><th>Focus weight</th></tr></thead><tbody>';
    for (const s of (latest.sectors || []).slice(0, 10)) {
      const med = medMap[s.sector];
      const tilt = med === undefined ? null : s.pct - med;
      const w = Math.max(2, Math.min(100, s.pct / 40 * 100));
      html += '<tr><td>' + esc(s.sector) + '</td><td class="num">' + fmtPct(s.pct) + '</td>' +
        '<td class="num">' + (med === undefined ? '—' : fmtPct(med)) + '</td>' +
        '<td class="num ' + (tilt === null ? '' : (tilt >= 0 ? 'pos' : 'neg')) + '">' + (tilt === null ? '—' : signed(tilt) + ' pts') + '</td>' +
        '<td><div class="bar-track" style="height:10px"><div class="bar-fill" style="width:' + w.toFixed(1) + '%"></div></div></td></tr>';
    }
    html += '</tbody></table><div class="small muted" style="margin-top:4px">Peer median = median sector weight across BDCs reporting that sector.</div></div>';
  }

  // Raw table
  html += '<div class="section"><h3>Full data table <button class="btn" id="peer-toggle-table" style="margin-left:8px">' + (state.peerShowTable ? 'Hide' : 'Show') + '</button></h3>';
  if (state.peerShowTable) html += '<div id="peer-table-wrap">' + peerTableHtml() + '</div>';
  html += '</div>';

  el.innerHTML = html;

  $('peer-open-bdc').onclick = () => {
    if (state.index && state.index.tickers[state.peerFocus]) {
      state.tab = 'overview';
      selectTicker(state.peerFocus, state.index.tickers[state.peerFocus]);
      renderTabs();
    }
  };
  el.querySelectorAll('[data-cohort]').forEach(b => {
    b.onclick = () => { state.peerCohort = b.getAttribute('data-cohort'); updateHash(); renderPeerEvaluation(el); };
  });
  el.querySelectorAll('[data-preset]').forEach(b => {
    b.onclick = () => {
      const p = PEER_PRESETS.find(x => x.id === b.getAttribute('data-preset'));
      if (!p) return;
      state.peerPreset = p.id; state.peerX = p.x; state.peerY = p.y; state.peerColorKey = p.color;
      renderPeerEvaluation(el);
    };
  });
  el.querySelectorAll('[data-chart-mode]').forEach(b => {
    b.onclick = () => { state.peerChartMode = b.getAttribute('data-chart-mode'); renderPeerEvaluation(el); };
  });
  const xSel = $('peer-x'), ySel = $('peer-y');
  if (xSel) xSel.onchange = (e) => { state.peerX = e.target.value; state.peerPreset = 'custom'; renderPeerEvaluation(el); };
  if (ySel) ySel.onchange = (e) => { state.peerY = e.target.value; state.peerPreset = 'custom'; renderPeerEvaluation(el); };
  el.querySelectorAll('#peer-heatmap th[data-heat]').forEach(th => {
    th.onclick = () => {
      const key = th.getAttribute('data-heat');
      const upd = HEAT_COLS.find(c => c[0] === key);
      if (state.peerHeatKey === key) state.peerHeatDir = -state.peerHeatDir;
      else { state.peerHeatKey = key; state.peerHeatDir = upd && upd[2] ? -1 : 1; }
      renderPeerEvaluation(el);
    };
  });
  el.querySelectorAll('[data-focus]').forEach(n => {
    n.onclick = () => selectPeerFocus(n.getAttribute('data-focus'));
  });
  const toggle = $('peer-toggle-table');
  if (toggle) toggle.onclick = () => { state.peerShowTable = !state.peerShowTable; renderPeerEvaluation(el); };
  if (state.peerShowTable) bindPeerTable(el);
}
function renderPeerSidebar() {
  const list = $('ticker-list');
  $('sidebar-title').textContent = 'Peer focus';
  if (!state.peers) {
    list.innerHTML = '<div class="empty">Loading peers…</div>';
    ensurePeers().then(() => renderPeerSidebar());
    return;
  }
  const peers = (state.peers.peers || []).slice().sort((a, b) => (num(b.fair_value) || 0) - (num(a.fair_value) || 0));
  const q = ($('ticker-search').value || '').toLowerCase();
  const rows = q ? peers.filter(p => (p.ticker + ' ' + ((state.profiles[p.ticker] || {}).name || '')).toLowerCase().indexOf(q) >= 0) : peers;
  $('ticker-count').textContent = peers.length + ' BDCs';
  list.innerHTML = '';
  for (const p of rows) {
    const btn = document.createElement('button');
    btn.className = 'ticker' + (p.ticker === state.peerFocus ? ' active' : '');
    btn.innerHTML = '<div><b>' + p.ticker + '</b> <span class="small muted">' + fmtK(p.fair_value) + '</span></div>' +
      '<div class="name">' + esc((state.profiles[p.ticker] || {}).name || '') + '</div>';
    btn.onclick = () => selectPeerFocus(p.ticker);
    list.appendChild(btn);
  }
  if (!rows.length) list.innerHTML = '<div class="empty">No match.</div>';
}
function selectPeerFocus(ticker) {
  state.mode = 'peers';
  state.peerFocus = ticker;
  updateModeChrome();
  updateHash();
  renderPeerSidebar();
  renderPeerEvaluation($('main'));
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
$('nav-peers').onclick = () => setMode('peers');
$('nav-companies').onclick = () => { state.companyId = null; setMode('companies'); };
$('company-sort').onchange = () => { state.companySort = $('company-sort').value; renderCompanySidebar(); };
$('modal-x').onclick = closeModal;
$('modal-cancel').onclick = closeModal;
$('modal-apply').onclick = applyPendingFilter;
$('modal-backdrop').addEventListener('click', (e) => { if (e.target === $('modal-backdrop')) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('modal-backdrop').hidden) closeModal(); });
$('main').addEventListener('click', (e) => {
  if (!e.target || !e.target.closest) return;
  const t = e.target.closest('[data-filter-kind]');
  if (!t) return;
  const kind = t.getAttribute('data-filter-kind');
  const value = t.getAttribute('data-filter-value');
  if (kind && value !== null) openFilterModal(kind, value);
});
window.addEventListener('hashchange', () => applyHash());

renderTabs();
loadTickers();
</script>
</body>
</html>`;
