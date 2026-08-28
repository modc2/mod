#!/usr/bin/env node
/**
 * docs app — a zero-dependency server that renders the protocol doc pages at
 * /docs. No build step, no npm deps: it serves the markdown under ../docs and a
 * single client-rendered viewer (index.html). Light by design.
 *
 * Every page docs/<name>.md may have a plain-language twin docs/simple/<name>.md.
 * /_pages reports which pages have one; /_page/<name>?v=simple serves the twin
 * (falling back to the technical page if it doesn't exist).
 *
 * POST /docs/mcp is proxied to the module's MCP server (api/mcp.py) so agents
 * reach it on the same public route humans read the docs on.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || process.env.APP_PORT || "50191", 10);
const BASE = process.env.BASE_PATH || "/docs";
const MCP_PORT = parseInt(process.env.MCP_PORT || "50192", 10);
const DOCS = path.join(__dirname, "..", "docs");
const SIMPLE = path.join(DOCS, "simple");
const INDEX = path.join(__dirname, "index.html");

// Pipe an MCP JSON-RPC request through to api/mcp.py; a dead server becomes a
// JSON-RPC error so the calling agent reads a reason instead of a socket hangup.
const proxyMcp = (req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    // Buffered, not piped: MCP messages are tiny and this lets us send an
    // explicit content-length (the upstream reads bodies by length, not chunks).
    const body = Buffer.concat(chunks);
    const up = http.request(
      { host: "127.0.0.1", port: MCP_PORT, path: "/mcp", method: "POST",
        headers: { "content-type": "application/json", "content-length": body.length } },
      (r) => {
        res.writeHead(r.statusCode, { "content-type": r.headers["content-type"] || "application/json" });
        r.pipe(res);
      }
    );
    up.on("error", (e) => {
      if (res.headersSent) return res.destroy();
      res.writeHead(503, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32000,
        message: `docs mcp server unreachable on :${MCP_PORT} (${e.code}) — start it with 'm pm/start docs target=api'` } }));
    });
    up.end(body);
  });
};

const pages = () =>
  fs.existsSync(DOCS)
    ? fs.readdirSync(DOCS).filter((f) => f.endsWith(".md")).map((f) => f.slice(0, -3)).sort()
        .map((name) => ({ name, simple: fs.existsSync(path.join(SIMPLE, name + ".md")) }))
    : [];

http
  .createServer((req, res) => {
    const [rawPath, query] = req.url.split("?");
    let url = rawPath;
    if (url === BASE) url = "/";
    else if (url.startsWith(BASE + "/")) url = url.slice(BASE.length);

    if (url === "/mcp") {
      if (req.method !== "POST") {
        res.writeHead(405, { "content-type": "text/plain" });
        return res.end("POST JSON-RPC 2.0 messages to this endpoint");
      }
      return proxyMcp(req, res);
    }
    if (url === "/_pages") {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify(pages()));
    }
    if (url.startsWith("/_page/")) {
      const name = decodeURIComponent(url.slice("/_page/".length)).replace(/[^a-zA-Z0-9_.-]/g, "");
      const fn = name.endsWith(".md") ? name : name + ".md";
      const wantSimple = /(^|&)v=simple(&|$)/.test(query || "");
      const simplePath = path.join(SIMPLE, fn);
      const techPath = path.join(DOCS, fn);
      const served = wantSimple && fs.existsSync(simplePath) ? simplePath : techPath;
      if (!techPath.startsWith(DOCS) || !fs.existsSync(techPath)) {
        res.writeHead(404, { "content-type": "text/plain" });
        return res.end("not found");
      }
      res.writeHead(200, {
        "content-type": "text/markdown; charset=utf-8",
        "x-docs-variant": served === simplePath ? "simple" : "tech",
      });
      return res.end(fs.readFileSync(served));
    }
    if (url === "/health") {
      res.writeHead(200);
      return res.end("ok");
    }
    // Everything else → the single-page viewer.
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(fs.readFileSync(INDEX));
  })
  .listen(PORT, "0.0.0.0", () => console.log(`docs app on :${PORT} base ${BASE}`));
