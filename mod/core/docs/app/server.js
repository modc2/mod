#!/usr/bin/env node
/**
 * docs app — a zero-dependency server that renders the protocol doc pages at
 * /docs. No build step, no npm deps: it serves the markdown under ../docs and a
 * single client-rendered viewer (index.html). Light by design.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || process.env.APP_PORT || "50191", 10);
const BASE = process.env.BASE_PATH || "/docs";
const DOCS = path.join(__dirname, "..", "docs");
const INDEX = path.join(__dirname, "index.html");

const pages = () =>
  fs.existsSync(DOCS)
    ? fs.readdirSync(DOCS).filter((f) => f.endsWith(".md")).map((f) => f.slice(0, -3)).sort()
    : [];

http
  .createServer((req, res) => {
    let url = req.url.split("?")[0];
    if (url === BASE) url = "/";
    else if (url.startsWith(BASE + "/")) url = url.slice(BASE.length);

    if (url === "/_pages") {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify(pages()));
    }
    if (url.startsWith("/_page/")) {
      const name = decodeURIComponent(url.slice("/_page/".length)).replace(/[^a-zA-Z0-9_.-]/g, "");
      const fn = name.endsWith(".md") ? name : name + ".md";
      const p = path.join(DOCS, fn);
      if (!p.startsWith(DOCS) || !fs.existsSync(p)) {
        res.writeHead(404, { "content-type": "text/plain" });
        return res.end("not found");
      }
      res.writeHead(200, { "content-type": "text/markdown; charset=utf-8" });
      return res.end(fs.readFileSync(p));
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
