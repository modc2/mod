#!/usr/bin/env node
/**
 * Module activator — scale-to-zero front proxy for the mod fleet.
 *
 * Sits in front of the local (pm2-managed) module ports. For every request it:
 *   1. maps the path to a module + target port (`/api/{mod}` → api port with the
 *      prefix stripped, `/{mod}` → app port — matching the old per-module Caddy
 *      blocks it replaces),
 *   2. WAKES the module if its port isn't listening (pm2 start the matching
 *      procs, wait for the port), then
 *   3. reverse-proxies the request (http + websocket upgrades) and stamps
 *      last-access.
 *
 * A background loop STOPS any module that has been idle longer than IDLE_MINUTES
 * AND currently has zero established TCP connections on its ports (the usage
 * signal, polled via `ss`). Stopped modules cold-start again on the next hit.
 *
 * Zero npm deps (built-in http/net/child_process only). Runs as root so it can
 * drive pm2. Docker-hosted modules (Caddy points them at container names, not
 * localhost) are NOT managed here — only modules whose pm2 procs live under the
 * repo are eligible.
 */
const http = require("http");
const net = require("net");
const fs = require("fs");
const path = require("path");
const { execFileSync, execFile } = require("child_process");

const HOME = process.env.HOME || "/root";
const REPO = path.join(HOME, "mod", "mod");
const PORT = parseInt(process.env.ACTIVATOR_PORT || "9000", 10);
const IDLE_MS = parseInt(process.env.IDLE_MS || String(parseInt(process.env.IDLE_MINUTES || "10", 10) * 60 * 1000), 10);
const SWEEP_MS = parseInt(process.env.SWEEP_SECONDS || "60", 10) * 1000;
const WAKE_TIMEOUT_MS = parseInt(process.env.WAKE_TIMEOUT_MS || "30000", 10);
// Modules that must never be auto-stopped (the gateway/infra itself).
const PIN = new Set((process.env.ACTIVATOR_PIN || "claude").split(",").map((s) => s.trim()).filter(Boolean));
// If set, the idle sweep ONLY ever stops these modules — the ones whose gateway
// traffic is actually routed through the activator. Prevents sleeping a module
// that's still reached directly (it would have no wake path). Empty = manage all
// (minus PIN), which is only safe once EVERY module routes through here.
const MANAGED = new Set((process.env.ACTIVATOR_MANAGED || "").split(",").map((s) => s.trim()).filter(Boolean));
// OOM guard: keep at least this much MemAvailable (MB). When the box dips
// below, the sweep/wake path stops least-recently-used managed modules until
// the headroom recovers — better one cold-start than the kernel OOM killer
// picking a victim for us. 0 disables the guard.
const MIN_FREE_MB = parseInt(process.env.MIN_FREE_MB || "1500", 10);
// Hard cap on concurrently RUNNING managed modules (0 = uncapped). Waking one
// more evicts the LRU first, so "too many apps" can't accumulate.
const MAX_RUNNING = parseInt(process.env.MAX_RUNNING || "0", 10);

// ── host overrides (the host owns this machine; manual control beats automation)
// ~/.mod/activator/overrides.json = { "disabled": [...], "pinned": [...], "idleSeconds": N }
//   disabled    → kept STOPPED; the activator refuses to wake it (503) until the
//                 host re-enables it. "I want this off" actually stays off.
//   pinned      → never slept (on top of the env PIN). "I want this always on."
//   idleSeconds → the person's idle timeout; beats the env IDLE_MINUTES default.
//   minFreeMb   → the person's OOM-guard headroom floor; beats env MIN_FREE_MB.
//   maxRunning  → the person's concurrent-app cap; beats env MAX_RUNNING.
// Hot-reloaded every sweep + on each control call, so host edits take effect live.
const OVERRIDES_PATH = path.join(HOME, ".mod", "activator", "overrides.json");
const IDLE_SECONDS_MIN = 10, IDLE_SECONDS_MAX = 86400;
let overrides = { disabled: [], pinned: [], idleSeconds: null, minFreeMb: null, maxRunning: null };
function loadOverrides() {
  try {
    const o = JSON.parse(fs.readFileSync(OVERRIDES_PATH, "utf8"));
    overrides = {
      disabled: Array.isArray(o.disabled) ? o.disabled : [],
      pinned: Array.isArray(o.pinned) ? o.pinned : [],
      idleSeconds: clampIdle(o.idleSeconds),
      minFreeMb: clampRange(o.minFreeMb, 0, 65536),
      maxRunning: clampRange(o.maxRunning, 0, 128),
    };
  } catch { overrides = { disabled: [], pinned: [], idleSeconds: null, minFreeMb: null, maxRunning: null }; }
}
function clampIdle(v) {
  const n = Math.round(Number(v));
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.max(IDLE_SECONDS_MIN, Math.min(IDLE_SECONDS_MAX, n));
}
// null/invalid → null (use env default); otherwise clamped integer (0 allowed —
// it means "disabled" for both guard knobs).
function clampRange(v, min, max) {
  if (v === null || v === undefined) return null;
  const n = Math.round(Number(v));
  if (!Number.isFinite(n)) return null;
  return Math.max(min, Math.min(max, n));
}
// Effective idle threshold right now (overrides beat env, hot-reloaded).
const idleMsNow = () => (overrides.idleSeconds ? overrides.idleSeconds * 1000 : IDLE_MS);
const minFreeMbNow = () => (overrides.minFreeMb !== null ? overrides.minFreeMb : MIN_FREE_MB);
const maxRunningNow = () => (overrides.maxRunning !== null ? overrides.maxRunning : MAX_RUNNING);
function saveOverrides() {
  try {
    fs.mkdirSync(path.dirname(OVERRIDES_PATH), { recursive: true });
    fs.writeFileSync(OVERRIDES_PATH, JSON.stringify(overrides, null, 2));
  } catch (e) { log("saveOverrides error", e.message); }
}
loadOverrides();
const isDisabled = (m) => overrides.disabled.includes(m);
const isPinned = (m) => PIN.has(m) || overrides.pinned.includes(m);

const log = (...a) => console.log(new Date().toISOString(), ...a);

// ── registry ────────────────────────────────────────────────────────────────
// name → { apiPort, appPort }. Built from each module's config.json.
function buildRegistry() {
  const reg = {};
  for (const group of ["orbit", "core"]) {
    const base = path.join(REPO, group);
    let names = [];
    try { names = fs.readdirSync(base); } catch { continue; }
    for (const name of names) {
      const dir = path.join(base, name);
      const cfgPath = [path.join(dir, "config.json"), path.join(dir, name, "config.json")]
        .find((p) => fs.existsSync(p));
      if (!cfgPath) continue;
      let cfg;
      try { cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8")); } catch { continue; }
      const apiPort = portOf(cfg, ["port", "api_port"], cfg.urls && cfg.urls.api);
      const appPort = portOf(cfg, ["app_port"], cfg.urls && cfg.urls.app);
      if (apiPort || appPort) reg[name] = { dir, apiPort, appPort };
    }
  }
  return reg;
}

function portOf(cfg, keys, url) {
  for (const k of keys) if (Number.isInteger(cfg[k])) return cfg[k];
  if (typeof url === "string") {
    const m = url.match(/:(\d+)/);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

let REGISTRY = buildRegistry();
const lastAccess = {}; // module → ms timestamp
const waking = {};     // module → Promise (de-dupes concurrent wakes)
const now = () => Date.now();
for (const m of Object.keys(REGISTRY)) lastAccess[m] = now(); // startup grace

// ── pm2 helpers ───────────────────────────────────────────────────────────────
function pm2Jlist() {
  try { return JSON.parse(execFileSync("pm2", ["jlist"], { encoding: "utf8", maxBuffer: 1 << 24 })); }
  catch { return []; }
}

// pm2 process names whose cwd / exec / args land inside the module dir — mirrors
// the matching the claude process backend uses (python modules carry the path
// only in args via `--app-dir`). Pass a prefetched `pm2 jlist` when checking
// many modules in one pass (each jlist call shells out).
function pm2NamesFor(moduleDir, jlist) {
  const canon = fs.realpathSync.native ? safeReal(moduleDir) : moduleDir;
  const inside = (s) => s && (s === canon || s.startsWith(canon + "/"));
  const out = [];
  for (const p of jlist || pm2Jlist()) {
    const env = p.pm2_env || {};
    const args = Array.isArray(env.args) ? env.args : [];
    if (inside(env.pm_cwd) || inside(env.pm_exec_path) || args.some((a) => inside(String(a)))) {
      out.push({ name: p.name, status: env.status, pid: p.pid });
    }
  }
  return out;
}
function safeReal(p) { try { return fs.realpathSync(p); } catch { return p; } }

function pm2(action, name) {
  return new Promise((resolve) => {
    execFile("pm2", [action, name], { timeout: 30000 }, (err, so, se) =>
      resolve({ ok: !err, out: (so || "") + (se || "") }));
  });
}

// ── port / connection probes ──────────────────────────────────────────────────
function isUp(port) {
  return new Promise((resolve) => {
    if (!port) return resolve(false);
    const sock = net.connect({ host: "127.0.0.1", port }, () => { sock.destroy(); resolve(true); });
    sock.on("error", () => resolve(false));
    sock.setTimeout(800, () => { sock.destroy(); resolve(false); });
  });
}

// Count established (non-listening) connections on a local port via ss.
function establishedConns(port) {
  if (!port) return 0;
  try {
    const out = execFileSync("ss", ["-tnH", "state", "established"], { encoding: "utf8", maxBuffer: 1 << 22 });
    const suffix = ":" + port;
    let n = 0;
    for (const line of out.split("\n")) {
      const cols = line.trim().split(/\s+/);
      // local addr is col 3 (0-indexed) in `ss -tnH`. Match either endpoint.
      if (cols[3] && cols[3].endsWith(suffix)) n++;
      else if (cols[4] && cols[4].endsWith(suffix)) n++;
    }
    return n;
  } catch { return 0; }
}

// ── OOM guard ───────────────────────────────────────────────────────────────
// The activator is the only thing starting apps on demand, so it also owns the
// budget: keep MemAvailable above the floor and the running-app count under the
// cap by stopping least-recently-used managed modules. A stopped module still
// wakes on its next request — a cold-start beats the kernel OOM killer.
const guardStats = { pressureStops: 0, capStops: 0, lastStop: null };

function memAvailableMb() {
  try {
    const raw = fs.readFileSync("/proc/meminfo", "utf8");
    const m = raw.match(/^MemAvailable:\s+(\d+)/m);
    return m ? Math.round(parseInt(m[1], 10) / 1024) : Infinity;
  } catch { return Infinity; } // non-Linux: guard never fires
}

// Managed modules with online pm2 procs that the guard is ALLOWED to stop
// (not pinned/disabled — disabled ones are already being stopped by the sweep),
// LRU first. One jlist pass for the whole scan.
function evictionCandidates(excludeMod) {
  const jlist = pm2Jlist();
  const mods = MANAGED.size ? [...MANAGED] : Object.keys(REGISTRY);
  return mods
    .filter((m) => REGISTRY[m] && m !== excludeMod && !isPinned(m) && !isDisabled(m))
    .filter((m) => pm2NamesFor(REGISTRY[m].dir, jlist).some((p) => p.status === "online"))
    .sort((a, b) => (lastAccess[a] || 0) - (lastAccess[b] || 0));
}

// Stop LRU modules until the memory floor / running cap is satisfied. Prefers
// idle victims (0 established conns); falls back to busy ones only for memory
// pressure — a dropped request beats the whole box going down.
async function relievePressure(excludeMod) {
  const floor = minFreeMbNow();
  const cap = maxRunningNow();
  let candidates = evictionCandidates(excludeMod);
  const overCap = () => cap > 0 && candidates.length + (excludeMod ? 1 : 0) > cap;
  const underFloor = () => floor > 0 && memAvailableMb() < floor;
  if (!overCap() && !underFloor()) return;

  const evict = async (mod, why, stat) => {
    log(`guard ${why}: stopping ${mod} (free=${memAvailableMb()}MB floor=${floor}MB running=${candidates.length} cap=${cap || "∞"})`);
    await stopModule(mod);
    guardStats[stat]++;
    guardStats.lastStop = { module: mod, reason: why, at: new Date().toISOString() };
    candidates = candidates.filter((m) => m !== mod);
  };

  // Idle victims first, LRU order.
  for (const mod of [...candidates]) {
    if (!overCap() && !underFloor()) return;
    const info = REGISTRY[mod];
    if (establishedConns(info.apiPort) + establishedConns(info.appPort) > 0) continue;
    await evict(mod, overCap() ? "cap" : "pressure", overCap() ? "capStops" : "pressureStops");
  }
  // Still under the memory floor with only busy modules left → evict anyway.
  while (underFloor() && candidates.length) {
    await evict(candidates[0], "pressure(busy)", "pressureStops");
  }
}

// ── wake ────────────────────────────────────────────────────────────────────
async function stopModule(mod) {
  const info = REGISTRY[mod];
  if (!info) return;
  const procs = pm2NamesFor(info.dir).filter((p) => p.status === "online");
  for (const p of procs) await pm2("stop", p.name);
  if (procs.length) log(`stop ${mod}: ${procs.map((p) => p.name).join(", ")}`);
}

async function wake(mod, port) {
  if (isDisabled(mod)) return false; // host turned it off — stays off
  if (await isUp(port)) return true;
  if (waking[mod]) return waking[mod];
  waking[mod] = (async () => {
    const procs = pm2NamesFor(REGISTRY[mod].dir);
    if (!procs.length) { log(`wake ${mod}: no pm2 procs found`); return await isUp(port); }
    // Make room BEFORE starting: evict LRU modules if this wake would breach
    // the running cap or the box is already short on memory.
    try { await relievePressure(mod); } catch (e) { log("guard error", e.message); }
    log(`wake ${mod}: starting ${procs.map((p) => p.name).join(", ")}`);
    // `restart` reliably starts a stopped proc here (plain `start` can no-op on a
    // stopped pm2 entry); wake only runs when the port is already down, so we
    // never bounce a healthy process.
    for (const p of procs) await pm2("restart", p.name);
    const deadline = now() + WAKE_TIMEOUT_MS;
    while (now() < deadline) {
      if (await isUp(port)) { log(`wake ${mod}: up on :${port}`); return true; }
      await sleep(250);
    }
    log(`wake ${mod}: timed out waiting for :${port}`);
    return false;
  })().finally(() => { delete waking[mod]; });
  return waking[mod];
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── routing ───────────────────────────────────────────────────────────────────
// Returns { mod, port, strip } or null. `/api/{mod}/...`→api (strip `/api/{mod}`),
// `/{mod}/...`→app (no strip, apps carry their basePath).
function route(url) {
  const seg = url.split("?")[0].split("/").filter(Boolean);
  if (!seg.length) return null;
  if (seg[0] === "api" && seg[1] && REGISTRY[seg[1]]) {
    const mod = seg[1];
    return { mod, port: REGISTRY[mod].apiPort, strip: `/api/${mod}` };
  }
  if (REGISTRY[seg[0]]) {
    const mod = seg[0];
    return { mod, port: REGISTRY[mod].appPort, strip: "" };
  }
  return null;
}

function proxyPath(url, strip) {
  if (!strip) return url;
  const stripped = url.slice(strip.length);
  return stripped === "" ? "/" : stripped;
}

// ── server ────────────────────────────────────────────────────────────────────
const server = http.createServer(async (req, res) => {
  // Host control plane (localhost-only) — see handleControl.
  if (req.url.startsWith("/_activator")) return handleControl(req, res);

  const r = route(req.url);
  if (!r || !r.port) { res.writeHead(404, { "content-type": "text/plain" }); return res.end("activator: no route\n"); }
  lastAccess[r.mod] = now();
  if (isDisabled(r.mod)) { res.writeHead(503, { "content-type": "text/plain" }); return res.end(`activator: ${r.mod} disabled by host\n`); }
  const ok = await wake(r.mod, r.port);
  if (!ok) { res.writeHead(503, { "content-type": "text/plain" }); return res.end(`activator: ${r.mod} failed to wake\n`); }

  const opts = {
    host: "127.0.0.1", port: r.port, method: req.method,
    path: proxyPath(req.url, r.strip), headers: req.headers,
  };
  const up = http.request(opts, (ur) => { res.writeHead(ur.statusCode, ur.headers); ur.pipe(res); });
  up.on("error", (e) => { if (!res.headersSent) res.writeHead(502); res.end(`activator: upstream error ${e.code || e.message}\n`); });
  req.pipe(up);
});

// websocket / raw upgrades (next.js HMR, live price feeds, etc.)
server.on("upgrade", async (req, socket, head) => {
  const r = route(req.url);
  if (!r || !r.port) return socket.destroy();
  lastAccess[r.mod] = now();
  if (!(await wake(r.mod, r.port))) return socket.destroy();
  const up = net.connect({ host: "127.0.0.1", port: r.port }, () => {
    up.write(rebuildUpgrade(req, proxyPath(req.url, r.strip)));
    if (head && head.length) up.write(head);
    socket.pipe(up); up.pipe(socket);
  });
  up.on("error", () => socket.destroy());
  socket.on("error", () => up.destroy());
});

function rebuildUpgrade(req, path) {
  let s = `${req.method} ${path} HTTP/1.1\r\n`;
  for (let i = 0; i < req.rawHeaders.length; i += 2) s += `${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`;
  return s + "\r\n";
}

// ── idle sweep ──────────────────────────────────────────────────────────────
async function sweep() {
  try {
    loadOverrides();            // host edits take effect live
    REGISTRY = buildRegistry(); // pick up newly-added modules
    for (const [mod, info] of Object.entries(REGISTRY)) {
      if (MANAGED.size && !MANAGED.has(mod)) continue; // only act on modules routed through us
      // Host disabled it → enforce STOPPED regardless of traffic/idle.
      if (isDisabled(mod)) {
        const up = pm2NamesFor(info.dir).filter((p) => p.status === "online");
        if (up.length) { log(`disabled ${mod}: enforcing stop`); await stopModule(mod); }
        continue;
      }
      if (isPinned(mod)) continue; // host/env wants it always on
      const idleFor = now() - (lastAccess[mod] || 0);
      if (idleFor < idleMsNow()) continue;
      const conns = establishedConns(info.apiPort) + establishedConns(info.appPort);
      if (conns > 0) { lastAccess[mod] = now(); continue; } // still in use — keep alive
      const procs = pm2NamesFor(info.dir).filter((p) => p.status === "online");
      if (!procs.length) continue;
      log(`idle ${mod}: ${Math.round(idleFor / 1000)}s, 0 conns → stopping ${procs.map((p) => p.name).join(", ")}`);
      for (const p of procs) await pm2("stop", p.name);
    }
    // After the idle pass: enforce the memory floor / running cap even when
    // nothing has hit its idle timeout yet.
    await relievePressure();
  } catch (e) { log("sweep error", e.message); }
}
// Self-rescheduling so short idle timeouts fire close to on-time: sweep at a
// quarter of the threshold (min 5s), never slower than the env SWEEP cadence.
const sweepDelay = () => Math.max(5000, Math.min(SWEEP_MS, idleMsNow() / 4));
(function sweepLoop() { setTimeout(async () => { await sweep(); sweepLoop(); }, sweepDelay()); })();

// ── host control plane ────────────────────────────────────────────────────────
// The host owns this machine, so manual control must beat the automation. Bound
// to localhost only (the host runs `actl` locally; not reachable via the gateway,
// which never routes /_activator to :9000). Endpoints:
//   GET  /_activator/state                     → every managed module's live state
//   POST /_activator/control {module, action}  → disable|enable|pin|unpin|sleep|wake
function isLocal(req) {
  const a = req.socket.remoteAddress || "";
  return a === "127.0.0.1" || a === "::1" || a === "::ffff:127.0.0.1";
}

async function buildState() {
  const mods = MANAGED.size ? [...MANAGED] : Object.keys(REGISTRY);
  const out = [];
  for (const mod of mods.sort()) {
    const info = REGISTRY[mod];
    if (!info) continue;
    const apiUp = await isUp(info.apiPort);
    const appUp = await isUp(info.appPort);
    out.push({
      module: mod,
      apiPort: info.apiPort, appPort: info.appPort,
      running: apiUp || appUp, apiUp, appUp,
      disabled: isDisabled(mod), pinned: isPinned(mod),
      idleSeconds: Math.round((now() - (lastAccess[mod] || 0)) / 1000),
    });
  }
  return out;
}

function setMembership(list, mod, add) {
  const i = list.indexOf(mod);
  if (add && i === -1) list.push(mod);
  if (!add && i !== -1) list.splice(i, 1);
}

async function handleControl(req, res) {
  const send = (code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj, null, 2)); };
  if (!isLocal(req)) return send(403, { error: "control plane is localhost-only (host machine)" });
  loadOverrides();

  const urlPath = req.url.split("?")[0];
  if (req.method === "GET" && urlPath === "/_activator/state") {
    const free = memAvailableMb();
    return send(200, {
      idleSeconds: idleMsNow() / 1000,
      idleSource: overrides.idleSeconds ? "overrides" : "env",
      defaultIdleSeconds: IDLE_MS / 1000,
      sweepSeconds: sweepDelay() / 1000,
      idleMinutes: idleMsNow() / 60000,
      guard: {
        minFreeMb: minFreeMbNow(),
        maxRunning: maxRunningNow(),
        availableMb: free === Infinity ? null : free,
        underPressure: minFreeMbNow() > 0 && free < minFreeMbNow(),
        ...guardStats,
      },
      pinned: [...PIN], overrides, modules: await buildState(),
    });
  }
  if (req.method === "POST" && urlPath === "/_activator/control") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let p;
      try { p = JSON.parse(body || "{}"); } catch { return send(400, { error: "bad json" }); }
      const mod = p.module, action = p.action;
      // "set" is fleet-wide (no module): idle timeout + OOM-guard knobs. Only
      // the keys present in the body are touched; null resets to env default.
      if (action === "set") {
        if ("idleSeconds" in p) {
          const secs = clampIdle(p.idleSeconds);
          if (secs === null && p.idleSeconds !== null) {
            return send(400, { error: `idleSeconds must be ${IDLE_SECONDS_MIN}-${IDLE_SECONDS_MAX}, or null to reset to the env default` });
          }
          overrides.idleSeconds = secs;
        }
        if ("minFreeMb" in p) overrides.minFreeMb = clampRange(p.minFreeMb, 0, 65536);
        if ("maxRunning" in p) overrides.maxRunning = clampRange(p.maxRunning, 0, 128);
        saveOverrides();
        log(`control: set idleSeconds=${overrides.idleSeconds ?? `env(${IDLE_MS / 1000}s)`} minFreeMb=${overrides.minFreeMb ?? `env(${MIN_FREE_MB})`} maxRunning=${overrides.maxRunning ?? `env(${MAX_RUNNING})`}`);
        return send(200, {
          ok: true,
          idleSeconds: idleMsNow() / 1000,
          idleSource: overrides.idleSeconds ? "overrides" : "env",
          minFreeMb: minFreeMbNow(),
          maxRunning: maxRunningNow(),
        });
      }
      if (!mod || !REGISTRY[mod]) return send(404, { error: `unknown module '${mod}'` });
      if (MANAGED.size && !MANAGED.has(mod)) return send(400, { error: `${mod} is not activator-managed` });
      const port = REGISTRY[mod].appPort || REGISTRY[mod].apiPort;
      switch (action) {
        case "disable": // off, and stays off
          setMembership(overrides.disabled, mod, true);
          setMembership(overrides.pinned, mod, false);
          saveOverrides(); await stopModule(mod); break;
        case "enable":  // on-demand resumes (does not force-start)
          setMembership(overrides.disabled, mod, false); saveOverrides(); break;
        case "pin":     // always on
          setMembership(overrides.pinned, mod, true);
          setMembership(overrides.disabled, mod, false);
          saveOverrides(); await wake(mod, port); break;
        case "unpin":
          setMembership(overrides.pinned, mod, false); saveOverrides(); break;
        case "sleep":   // stop now (will still wake on next request)
          await stopModule(mod); break;
        case "wake":    // start now
          setMembership(overrides.disabled, mod, false); saveOverrides(); await wake(mod, port); break;
        default:
          return send(400, { error: "action must be: disable|enable|pin|unpin|sleep|wake" });
      }
      log(`control: ${action} ${mod}`);
      return send(200, { ok: true, module: mod, action, state: (await buildState()).find((m) => m.module === mod) });
    });
    return;
  }
  return send(404, { error: "unknown control route" });
}

server.listen(PORT, "0.0.0.0", () => {
  log(`activator on :${PORT} — ${Object.keys(REGISTRY).length} modules, idle=${IDLE_MS / 60000}m, pinned=[${[...PIN].join(",")}], managed=[${MANAGED.size ? [...MANAGED].join(",") : "ALL"}], minFree=${MIN_FREE_MB}MB, maxRunning=${MAX_RUNNING || "∞"}`);
});
