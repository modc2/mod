#!/usr/bin/env node
/**
 * hub app — the public front door of the module catalog, served at /hub.
 *
 * Zero dependencies, no build step (same shape as core/docs/app): it walks the
 * repo tree (orbit/ + core/) itself and serves a single client-rendered viewer
 * (index.html) plus a few JSON/markdown endpoints. It deliberately does NOT
 * proxy the loopback catalog api (:50520) — that surface is raw and unrouted;
 * this one applies the same privacy rule the caddy router uses (a module with
 * an enabled record under ~/.mod/build/private/ is simply absent).
 *
 * The whitepaper is not duplicated here: /_wp reads core/docs' whitepaper.md
 * (engineer) and docs/simple/whitepaper.md (human twin), so docs stays the one
 * home of the text.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || process.env.APP_PORT || "50521", 10);
const BASE = process.env.BASE_PATH || "/hub";
const HOME = process.env.HOME || "/root";
const REPO = process.env.MOD_REPO || path.join(HOME, "mod", "mod");
const PRIVATE_DIR = path.join(HOME, ".mod", "build", "private");
const DOCS = path.join(REPO, "core", "docs", "docs");
const INDEX = path.join(__dirname, "index.html");
const GROUPS = ["orbit", "core"];

// Same rule as orbit/caddy _is_private: any read failure means "not private".
const isPrivate = (name) => {
  const safe = name.replace(/[^a-zA-Z0-9\-_]/g, "");
  if (!safe) return false;
  try {
    return !!JSON.parse(fs.readFileSync(path.join(PRIVATE_DIR, safe + ".json"))).enabled;
  } catch {
    return false;
  }
};

// A module's config.json may sit at <mod>/config.json or <mod>/<name>/config.json.
const desc = (dir, name) => {
  for (const p of [path.join(dir, "config.json"), path.join(dir, name, "config.json")]) {
    try {
      return JSON.parse(fs.readFileSync(p)).description || "";
    } catch {}
  }
  return "";
};

const modules = () => {
  const out = [];
  for (const g of GROUPS) {
    const base = path.join(REPO, g);
    let names;
    try {
      names = fs.readdirSync(base).sort();
    } catch {
      continue;
    }
    for (const name of names) {
      if (name.startsWith(".") || name.startsWith("_")) continue;
      const d = path.join(base, name);
      let st;
      try {
        st = fs.statSync(d);
      } catch {
        continue;
      }
      if (!st.isDirectory() || isPrivate(name)) continue;
      out.push({
        name,
        group: g,
        description: desc(d, name),
        readme: fs.existsSync(path.join(d, "README.md")),
        skill: fs.existsSync(path.join(d, "skill.md")),
      });
    }
  }
  return out;
};

const modDir = (name) => {
  const safe = name.replace(/[^a-zA-Z0-9._\-]/g, "");
  if (!safe || safe.startsWith(".") || safe.startsWith("_") || isPrivate(safe)) return null;
  for (const g of GROUPS) {
    const d = path.join(REPO, g, safe);
    if (d.startsWith(path.join(REPO, g)) && fs.existsSync(d) && fs.statSync(d).isDirectory())
      return { dir: d, group: g, name: safe };
  }
  return null;
};

const WP_FALLBACK = `# MOD\n\nEvery app on this box is a **module**: a small directory of code that is at once a package, a live API and a web app. \`/{mod}\` is its app, \`/{mod}/api\` its API.\n\nThe full whitepaper lives in the docs module — open **/docs** and flip to Human mode.`;

const send = (res, code, type, body) => {
  res.writeHead(code, { "content-type": type });
  res.end(body);
};

http
  .createServer((req, res) => {
    const [rawPath, query] = req.url.split("?");
    let url = rawPath;
    if (url === BASE) url = "/";
    else if (url.startsWith(BASE + "/")) url = url.slice(BASE.length);

    if (url === "/health") return send(res, 200, "text/plain", "ok");
    if (url === "/_mods")
      return send(res, 200, "application/json", JSON.stringify(modules()));
    if (url.startsWith("/_doc/")) {
      const hit = modDir(decodeURIComponent(url.slice("/_doc/".length)));
      if (!hit) return send(res, 404, "application/json", JSON.stringify({ error: "module not found" }));
      const read = (fn) => {
        try {
          return fs.readFileSync(path.join(hit.dir, fn), "utf8");
        } catch {
          return null;
        }
      };
      return send(res, 200, "application/json", JSON.stringify({
        name: hit.name,
        group: hit.group,
        description: desc(hit.dir, hit.name),
        readme: read("README.md"),
        skill: read("skill.md"),
      }));
    }
    if (url === "/_wp") {
      const full = /(^|&)v=full(&|$)/.test(query || "");
      const p = full ? path.join(DOCS, "whitepaper.md") : path.join(DOCS, "simple", "whitepaper.md");
      let text;
      try {
        text = fs.readFileSync(p, "utf8");
      } catch {
        text = WP_FALLBACK;
      }
      res.writeHead(200, {
        "content-type": "text/markdown; charset=utf-8",
        "x-wp-variant": full ? "full" : "simple",
      });
      return res.end(text);
    }
    // Everything else → the single-page viewer.
    send(res, 200, "text/html; charset=utf-8", fs.readFileSync(INDEX));
  })
  .listen(PORT, "0.0.0.0", () => console.log(`hub app on :${PORT} base ${BASE}`));
