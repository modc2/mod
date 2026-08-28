#!/usr/bin/env node
/**
 * encrypt console — a zero-dependency static server + API proxy.
 *
 * No build step and no npm deps: it serves index.html and forwards everything
 * under `${BASE}/_api/*` to the FastAPI gateway. Proxying rather than calling
 * the API cross-origin means the browser talks to one origin, so it works the
 * same on localhost:50381 and behind the gateway at /encrypt.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || process.env.APP_PORT || "50381", 10);
const API_PORT = parseInt(process.env.API_PORT || "50380", 10);
const API_HOST = process.env.API_HOST || "127.0.0.1";
const BASE = process.env.BASE_PATH || "/encrypt";
const INDEX = path.join(__dirname, "index.html");

const proxy = (req, res, upstreamPath) => {
  const headers = { ...req.headers, host: `${API_HOST}:${API_PORT}` };
  const up = http.request(
    { host: API_HOST, port: API_PORT, path: upstreamPath, method: req.method, headers },
    (r) => {
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    }
  );
  up.on("error", (e) => {
    if (res.headersSent) return res.destroy();
    res.writeHead(503, { "content-type": "application/json" });
    res.end(JSON.stringify({
      detail: `encrypt api unreachable on :${API_PORT} (${e.code}) — start it with 'm encrypt/serve_api'`,
    }));
  });
  req.pipe(up);
};

http
  .createServer((req, res) => {
    let url = req.url;
    if (url === BASE) url = "/";
    else if (url.startsWith(BASE + "/")) url = url.slice(BASE.length);

    if (url.startsWith("/_api/")) return proxy(req, res, url.slice("/_api".length));
    if (url === "/health") {
      res.writeHead(200, { "content-type": "text/plain" });
      return res.end("ok");
    }
    // Everything else is the single-page console.
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(fs.readFileSync(INDEX));
  })
  .listen(PORT, "0.0.0.0", () =>
    console.log(`encrypt console on :${PORT} base ${BASE} → api :${API_PORT}`)
  );
