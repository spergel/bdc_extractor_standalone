import app, { setDataReader } from "./index.ts";

/**
 * Cloudflare Worker entry point.
 *
 * Portfolio data is deployed as Worker static assets. API routes read those
 * assets through the ASSETS binding; static requests are served by Cloudflare
 * before the Worker runs.
 */
export default {
  async fetch(request: Request, env: any, ctx: any): Promise<Response> {
    setDataReader(async (rel: string) => {
      if (rel.includes("..")) return null;
      const url = new URL(request.url);
      url.pathname = `/data/${rel}`;
      const res = await env.ASSETS.fetch(new Request(url.toString(), request));
      return res.ok ? await res.text() : null;
    });
    return app.fetch(request, env, ctx);
  },
};
