#!/usr/bin/env node
/**
 * cshare app — zero-dependency server for the compute marketplace console.
 * Serves the single-page console (index.html) at /cshare and proxies
 * /cshare/api/* to the cshare API so the page is same-origin whether it is
 * reached directly (:50291/cshare) or through the gateway (/cshare).
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || "50291", 10);
const BASE = process.env.BASE_PATH || "/cshare";
const API = new URL(process.env.API_URL || "http://localhost:50290");
const INDEX = path.join(__dirname, "index.html");

http
  .createServer((req, res) => {
    let url = req.url;
    if (url === BASE) url = "/";
    else if (url.startsWith(BASE + "/")) url = url.slice(BASE.length);

    if (url.startsWith("/api/") || url === "/api") {
      const target = url === "/api" ? "/" : url.slice("/api".length);
      const proxy = http.request(
        { hostname: API.hostname, port: API.port, path: target,
          method: req.method, headers: { ...req.headers, host: API.host } },
        (up) => {
          res.writeHead(up.statusCode, up.headers);
          up.pipe(res);
        }
      );
      proxy.on("error", () => {
        res.writeHead(502, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "cshare api unreachable" }));
      });
      req.pipe(proxy);
      return;
    }

    if (url === "/health") {
      res.writeHead(200);
      return res.end("ok");
    }
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(fs.readFileSync(INDEX));
  })
  .listen(PORT, "0.0.0.0", () =>
    console.log(`cshare app on :${PORT} base ${BASE} → api ${API.href}`));
