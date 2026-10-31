import { serve } from "@hono/node-server";
import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import app, { setDataReader } from "./index.ts";

const PORT = Number(process.env.PORT || 8787);
const DATA_DIR = resolve(process.cwd(), "public/data");

setDataReader(
  async (rel) => {
    const path = resolve(DATA_DIR, rel);
    if (!path.startsWith(DATA_DIR)) return null;
    try {
      return (await readFile(path)).toString("utf8");
    } catch {
      return null;
    }
  },
  async () => (await stat(DATA_DIR)).mtime.toISOString(),
);

serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`BDC app running at http://localhost:${info.port}`);
  console.log(`Serving data from ${DATA_DIR}`);
});
