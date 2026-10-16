#!/usr/bin/env node
/**
 * Module activator — scale-to-zero front proxy for the mod fleet.
 *
 * Sits in front of the local (pm2-managed) module ports. For every request it:
 *   1. maps the path to a module + target port (`/{mod}/api` — or the legacy
 *      `/api/{mod}` alias — → api port with the
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
// Idle threshold. IDLE_MS (testing) > IDLE_SECONDS > IDLE_MINUTES. Default 10s —
// "kill the least-used unused mod if no one is using it after more than 10
// seconds" (2026-10-16). Short on purpose: a module with an open tab keeps an
// established TCP connection, which the sweep treats as "in use".
const IDLE_MS = parseInt(
  process.env.IDLE_MS
    || (process.env.IDLE_SECONDS ? String(parseInt(process.env.IDLE_SECONDS, 10) * 1000) : "")
    || (process.env.IDLE_MINUTES ? String(parseInt(process.env.IDLE_MINUTES, 10) * 60 * 1000) : "")
    || "10000", 10);
const SWEEP_MS = parseInt(process.env.SWEEP_SECONDS || "60", 10) * 1000;
const WAKE_TIMEOUT_MS = parseInt(process.env.WAKE_TIMEOUT_MS || "30000", 10);
// Modules that must never be auto-stopped (the gateway/infra itself).
const PIN = new Set((process.env.ACTIVATOR_PIN || "claude").split(",").map((s) => s.trim()).filter(Boolean));
// If set, the idle sweep ONLY ever stops these modules — the ones whose gateway
// traffic is actually routed through the activator. Prevents sleeping a module
// that's still reached directly (it would have no wake path). Empty = manage all
// (minus PIN), which is only safe once EVERY module routes through here.
const MANAGED = new Set((process.env.ACTIVATOR_MANAGED || "").split(",").map((s) => s.trim()).filter(Boolean));
// `ACTIVATOR_MANAGED=*` = every pm2 module on the box. Safe now because a slept
// module's ports are held by the activator itself (socket activation, below):
// ANY connection — gateway, portal, a peer module dialing the bare port — wakes
// it, so nothing needs to be routed through :9000 to have a wake path.
const MANAGE_ALL = MANAGED.has("*");
// Never slept and never revived — the front door, the console, auth, and
// anything speaking UDP (socket activation only holds TCP ports).
const EXEMPT = new Set((process.env.ACTIVATOR_EXEMPT
  || "app,api,server,web,claude,build,passport,dns,mcp,agent,updates")
  .split(",").map((s) => s.trim()).filter(Boolean));
const isManaged = (m) => !EXEMPT.has(m) && (MANAGE_ALL || !MANAGED.size || MANAGED.has(m));
// A module whose pm2 procs are burning more than this much CPU is doing work
// (a sync loop, an index build) even with nobody connected — leave it running.
const BUSY_CPU = parseFloat(process.env.BUSY_CPU || "3");
// How many idle modules one sweep may stop (least used first).
const STOPS_PER_SWEEP = parseInt(process.env.STOPS_PER_SWEEP || "4", 10);
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
// config.json `"always_on": true` = the module itself demands to be up. It beats
// overrides.json in both directions: treated as pinned, and a `disabled` entry
// for it is IGNORED (warned once) — a stray agent edit to overrides.json or an
// `actl disable` must never be able to take it down (polymarket runs live money).
const isAlwaysOn = (m) => !!(REGISTRY[m] && REGISTRY[m].alwaysOn);
const warnedDisabled = new Set();
const isDisabled = (m) => {
  if (!overrides.disabled.includes(m)) return false;
  if (isAlwaysOn(m)) {
    if (!warnedDisabled.has(m)) { warnedDisabled.add(m); log(`always_on ${m}: ignoring 'disabled' in overrides.json`); }
    return false;
  }
  return true;
};
const isPinned = (m) => PIN.has(m) || isAlwaysOn(m) || overrides.pinned.includes(m);

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
      if (apiPort || appPort) reg[name] = { dir, apiPort, appPort, alwaysOn: cfg.always_on === true };
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
const hits = {};       // module → requests proxied since start ("least used" ranking)
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

// ── fleet snapshot ──────────────────────────────────────────────────────────
// One pass of ss + /proc + pm2 per sweep instead of a shell-out per module.
//   listens: [{host, port, pid}] every TCP listener on the box
//   conns:   [{local, peer, idleMs}] established, with ms since last traffic
//   kids:    pid → [child pids]
function snapshot() {
  const snap = { listens: [], conns: [], kids: {}, jlist: pm2Jlist() };
  try {
    const out = execFileSync("ss", ["-ltnpH"], { encoding: "utf8", maxBuffer: 1 << 22 });
    for (const line of out.split("\n")) {
      const cols = line.trim().split(/\s+/);
      if (cols.length < 5) continue;
      const hp = splitHostPort(cols[3]);
      if (!hp) continue;
      for (const m of line.matchAll(/pid=(\d+)/g)) snap.listens.push({ ...hp, pid: parseInt(m[1], 10) });
    }
  } catch {}
  try {
    const out = execFileSync("ss", ["-tniH", "state", "established"], { encoding: "utf8", maxBuffer: 1 << 24 });
    let cur = null;
    for (const line of out.split("\n")) {
      if (!line.trim()) continue;
      if (!/^\s/.test(line)) {
        const cols = line.trim().split(/\s+/);
        cur = { local: cols[2], peer: cols[3], idleMs: 0 };
        snap.conns.push(cur);
      } else if (cur) {
        // Quietest of send/recv. Missing fields = treat as live.
        const t = ["lastsnd", "lastrcv"].map((k) => (line.match(new RegExp(k + ":(\\d+)")) || [])[1])
          .filter(Boolean).map(Number);
        cur.idleMs = t.length ? Math.min(...t) : 0;
      }
    }
  } catch {}
  try {
    for (const d of fs.readdirSync("/proc")) {
      if (!/^\d+$/.test(d)) continue;
      try {
        const st = fs.readFileSync(`/proc/${d}/stat`, "utf8");
        const ppid = parseInt(st.slice(st.lastIndexOf(")") + 2).split(" ")[1], 10);
        (snap.kids[ppid] = snap.kids[ppid] || []).push(parseInt(d, 10));
      } catch {}
    }
  } catch {}
  return snap;
}
function splitHostPort(s) {
  const i = (s || "").lastIndexOf(":");
  if (i < 0) return null;
  const port = parseInt(s.slice(i + 1), 10);
  if (!port) return null;
  return { host: s.slice(0, i).replace(/^\[|\]$/g, "").replace(/%.*$/, ""), port };
}
// Every TCP port the module's online pm2 procs (or their children — next start
// forks next-server, uvicorn forks workers) are listening on.
function modulePorts(mod, snap) {
  const info = REGISTRY[mod];
  const procs = pm2NamesFor(info.dir, snap.jlist).filter((p) => p.status === "online" && p.pid);
  const pids = new Set();
  const stack = procs.map((p) => p.pid);
  while (stack.length) {
    const p = stack.pop();
    if (pids.has(p)) continue;
    pids.add(p);
    for (const c of snap.kids[p] || []) stack.push(c);
  }
  const seen = new Set();
  return snap.listens.filter((l) => pids.has(l.pid) && !seen.has(l.host + "|" + l.port) && seen.add(l.host + "|" + l.port))
    .map(({ host, port }) => ({ host, port }));
}
// Established connections on any of these ports that carried traffic within
// `withinMs`. An idle keepalive socket Caddy parked on the upstream doesn't
// count as someone using the module.
function liveConns(ports, snap, withinMs) {
  const set = new Set(ports.filter(Boolean).map(String));
  let n = 0;
  for (const c of snap.conns) {
    if (c.idleMs >= withinMs) continue;
    const a = splitHostPort(c.local), b = splitHostPort(c.peer);
    if ((a && set.has(String(a.port))) || (b && set.has(String(b.port)))) n++;
  }
  return n;
}
function procCpu(mod, snap) {
  let cpu = 0;
  for (const p of snap.jlist) {
    if (pm2NamesFor(REGISTRY[mod].dir, [p]).length && p.pm2_env && p.pm2_env.status === "online") cpu += (p.monit && p.monit.cpu) || 0;
  }
  return cpu;
}

// ── socket activation ───────────────────────────────────────────────────────
// When a module is slept, the activator binds the exact host:port pairs it was
// listening on. The first connection to any of them — from the gateway, the
// portal, a browser, or another module calling the bare port — releases the
// holds, restarts the module's pm2 procs, waits for its port, and splices the
// waiting connection(s) through. Nothing is routed differently while it's up.
// ~/.mod/activator/slept.json survives restarts so holds are re-armed.
const SLEPT_PATH = path.join(HOME, ".mod", "activator", "slept.json");
let slept = {};          // module → [{host, port}] held while asleep
const holds = {};        // module → [net.Server]
const pending = {};      // module → [{sock, host, port}] waiting on a wake
const activationStats = { wakes: 0, lastWake: null };
try { slept = JSON.parse(fs.readFileSync(SLEPT_PATH, "utf8")) || {}; } catch { slept = {}; }
function saveSlept() {
  try { fs.mkdirSync(path.dirname(SLEPT_PATH), { recursive: true }); fs.writeFileSync(SLEPT_PATH, JSON.stringify(slept, null, 2)); }
  catch (e) { log("saveSlept error", e.message); }
}

function listenOnce(host, port, onConn, ipv6Only) {
  return new Promise((resolve) => {
    const srv = net.createServer({ pauseOnConnect: true }, onConn);
    srv.once("error", (e) => resolve({ err: e.code || e.message }));
    const h = host === "*" || host === "" ? "::" : host;
    srv.listen({ host: h, port, exclusive: true, ipv6Only: !!ipv6Only }, () => resolve({ srv }));
  });
}

// Bind every port in slept[mod]. Ports still in TIME_WAIT/held by a straggler
// are retried for a few seconds; a port something else owns is skipped.
async function armHolds(mod) {
  if (holds[mod] || !slept[mod] || !slept[mod].length) return;
  const servers = [];
  for (const { host, port } of slept[mod]) {
    // A module bound both 0.0.0.0:p and [::]:p → hold the v6 side v6-only.
    const v6only = host === "::" && slept[mod].some((o) => o.port === port && o.host === "0.0.0.0");
    let r;
    for (let i = 0; i < 12; i++) {
      r = await listenOnce(host, port, (sock) => onHeldConn(mod, host, port, sock), v6only);
      if (r.srv || r.err !== "EADDRINUSE") break;
      await sleep(250);
    }
    if (r.srv) servers.push(r.srv);
    else log(`hold ${mod}: can't bind ${host}:${port} (${r.err})`);
  }
  if (servers.length) {
    holds[mod] = servers;
    log(`hold ${mod}: ${slept[mod].map((p) => `${p.host}:${p.port}`).join(", ")} — wakes on first connection`);
  }
}

function releaseHolds(mod) {
  const servers = holds[mod];
  delete holds[mod];
  if (!servers) return Promise.resolve();
  // close() unbinds the port right away; its callback waits for accepted
  // sockets to end — and the ones we're holding ARE those sockets. Don't wait.
  for (const s of servers) s.close();
  return new Promise((r) => setImmediate(r));
}

function onHeldConn(mod, host, port, sock) {
  sock.on("error", () => {});
  lastAccess[mod] = now(); hits[mod] = (hits[mod] || 0) + 1;
  (pending[mod] = pending[mod] || []).push({ sock, host, port });
  if (pending[mod].length > 1) return; // a wake is already under way
  activate(mod);
}

async function activate(mod) {
  const t0 = now();
  const first = pending[mod][0];
  const ok = await wake(mod, first.port);
  const queue = pending[mod] || [];
  delete pending[mod];
  if (ok) {
    delete slept[mod]; saveSlept();
    activationStats.wakes++;
    activationStats.lastWake = { module: mod, ms: now() - t0, at: new Date().toISOString() };
  }
  for (const { sock, host, port } of queue) {
    if (!ok) { sock.destroy(); continue; }
    // Another port of the same module (app vs api) may come up a beat later.
    const deadline = now() + WAKE_TIMEOUT_MS;
    while (now() < deadline && !(await isUp(port))) await sleep(200);
    const target = host === "*" || host === "" || host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
    const up = net.connect({ host: target, port }, () => { sock.pipe(up); up.pipe(sock); sock.resume(); });
    up.on("error", () => sock.destroy());
    sock.on("close", () => up.destroy());
  }
  // Wake failed → the module is still asleep; put the holds back so the next
  // caller retries instead of getting connection-refused forever.
  if (!ok) await armHolds(mod);
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
  return managedList()
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
// `hold` (default) = socket-activate: keep its ports open so the next
// connection wakes it. Disable passes hold=false — off stays off.
async function stopModule(mod, { hold = true } = {}) {
  const info = REGISTRY[mod];
  if (!info) return;
  const snap = snapshot();
  const ports = modulePorts(mod, snap);
  const procs = pm2NamesFor(info.dir, snap.jlist).filter((p) => p.status === "online");
  for (const p of procs) await pm2("stop", p.name);
  if (procs.length) log(`stop ${mod}: ${procs.map((p) => p.name).join(", ")}`);
  if (!hold) { await releaseHolds(mod); if (slept[mod]) { delete slept[mod]; saveSlept(); } return; }
  // Fall back to config ports if nothing was listening (already half-down).
  const keep = ports.length ? ports
    : [info.apiPort, info.appPort].filter(Boolean).map((port) => ({ host: "0.0.0.0", port }));
  if (keep.length && procs.length) { slept[mod] = keep; saveSlept(); await armHolds(mod); }
}

async function wake(mod, port) {
  if (isDisabled(mod)) return false; // host turned it off — stays off
  if (waking[mod]) return waking[mod];
  // Our own hold answers isUp, so a held module is down by definition.
  if (!holds[mod] && await isUp(port)) return true;
  waking[mod] = (async () => {
    await releaseHolds(mod);
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
// Returns { mod, port, strip } or null. `/{mod}/api/...`→api (canonical; strip
// `/{mod}/api`), `/api/{mod}/...`→api (legacy alias, strip `/api/{mod}`),
// `/{mod}/...`→app (no strip, apps carry their basePath).
function route(url) {
  const seg = url.split("?")[0].split("/").filter(Boolean);
  if (!seg.length) return null;
  if (seg[0] === "api" && seg[1] && REGISTRY[seg[1]]) {
    const mod = seg[1];
    return { mod, port: REGISTRY[mod].apiPort, strip: `/api/${mod}` };
  }
  if (seg[1] === "api" && REGISTRY[seg[0]] && REGISTRY[seg[0]].apiPort) {
    const mod = seg[0];
    return { mod, port: REGISTRY[mod].apiPort, strip: `/${mod}/api` };
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
  lastAccess[r.mod] = now(); hits[r.mod] = (hits[r.mod] || 0) + 1;
  if (isDisabled(r.mod)) { res.writeHead(503, { "content-type": "text/plain" }); return res.end(`activator: ${r.mod} disabled by host\n`); }
  const ok = await wake(r.mod, r.port);
  if (!ok) { res.writeHead(503, { "content-type": "text/plain" }); return res.end(`activator: ${r.mod} failed to wake\n`); }

  const opts = {
    host: "127.0.0.1", port: r.port, method: req.method,
    path: proxyPath(req.url, r.strip), headers: req.headers,
  };
  const up = http.request(opts, (ur) => {
    res.writeHead(ur.statusCode, ur.headers);
    ur.pipe(res);
    // pipe() does not propagate a close backwards, so a client that hangs up
    // mid-response would leave the upstream socket established forever. On a
    // streaming route (SSE) that means the module can never look idle, and
    // the app behind it keeps a dead session. Take the upstream down with the
    // client. On a normal completion `ur` has already ended, so this is a
    // no-op.
    res.on("close", () => { ur.destroy(); up.destroy(); });
  });
  up.on("error", (e) => { if (!res.headersSent) res.writeHead(502); res.end(`activator: upstream error ${e.code || e.message}\n`); });
  req.pipe(up);
});

// websocket / raw upgrades (next.js HMR, live price feeds, etc.)
server.on("upgrade", async (req, socket, head) => {
  const r = route(req.url);
  if (!r || !r.port) return socket.destroy();
  lastAccess[r.mod] = now(); hits[r.mod] = (hits[r.mod] || 0) + 1;
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

// ── pinned revive ───────────────────────────────────────────────────────────
// A pinned module that is down gets its stopped pm2 procs restarted. Backoff so
// a module that refuses to start is retried on a widening interval instead of
// being thrashed by every sweep (5s apart under a short idle timeout).
const REVIVE_MIN_MS = 30 * 1000;
const REVIVE_MAX_MS = 15 * 60 * 1000;
const REVIVE_MAX_ALWAYS_MS = 60 * 1000; // always_on mods never back off past a minute
const reviving = {}; // module → { next, delay }

async function revivePinned(mod, info) {
  const procs = pm2NamesFor(info.dir);
  const down = procs.filter((p) => p.status !== "online");
  // No pm2 entry at all → nothing to revive (the module is started some other
  // way); fully online → back to a clean slate for the next outage.
  if (!procs.length || !down.length) { delete reviving[mod]; return; }
  const st = reviving[mod] || { next: 0, delay: REVIVE_MIN_MS };
  if (now() < st.next) return;
  await releaseHolds(mod);
  if (slept[mod]) { delete slept[mod]; saveSlept(); }
  log(`pinned ${mod}: ${down.map((p) => `${p.name}=${p.status}`).join(", ")} → restarting`);
  for (const p of down) await pm2("restart", p.name);
  lastAccess[mod] = now();
  const cap = isAlwaysOn(mod) ? REVIVE_MAX_ALWAYS_MS : REVIVE_MAX_MS;
  reviving[mod] = { next: now() + Math.min(cap, st.delay), delay: Math.min(cap, st.delay * 2) };
}

// ── idle sweep ──────────────────────────────────────────────────────────────
const sweepStats = { idleStops: 0, lastIdleStop: null };
const managedList = () => Object.keys(REGISTRY).filter(isManaged);
async function sweep() {
  try {
    loadOverrides();            // host edits take effect live
    REGISTRY = buildRegistry(); // pick up newly-added modules
    const snap = snapshot();    // one ss/proc/pm2 pass for the whole fleet
    const idleMs = idleMsNow();
    const idle = []; // unused past the threshold this pass
    for (const mod of managedList()) {
      const info = REGISTRY[mod];
      // Host disabled it → enforce STOPPED regardless of traffic/idle.
      if (isDisabled(mod)) {
        if (holds[mod] || slept[mod]) await stopModule(mod, { hold: false });
        const up = pm2NamesFor(info.dir, snap.jlist).filter((p) => p.status === "online");
        if (up.length) { log(`disabled ${mod}: enforcing stop`); await stopModule(mod, { hold: false }); }
        continue;
      }
      // Pinned = "always on", enforced the same way `disabled` is enforced:
      // not merely "never slept" but actively brought back when something else
      // stopped it — a reboot before any request arrives, a crash loop's final
      // exit, a stray `pm2 stop`. Without this, pinning only held as long as
      // nothing outside the activator touched the process.
      if (isPinned(mod)) { await revivePinned(mod, info); continue; }
      const procs = pm2NamesFor(info.dir, snap.jlist).filter((p) => p.status === "online");
      if (!procs.length) {
        // Slept by us and still down (activator restarted, or a wake failed)
        // → make sure its ports are held so the next caller wakes it.
        if (slept[mod] && !holds[mod] && !waking[mod]) await armHolds(mod);
        continue;
      }
      if (slept[mod] && !waking[mod]) { delete slept[mod]; saveSlept(); } // came back some other way
      const ports = modulePorts(mod, snap);
      if (!ports.length) continue; // nothing listening = nothing to hold; leave it alone
      // In use = traffic on its ports within the window, or its procs are
      // working (CPU) with nobody connected.
      const conns = liveConns(ports.map((p) => p.port), snap, idleMs);
      const cpu = procCpu(mod, snap);
      if (conns > 0 || cpu >= BUSY_CPU) { lastAccess[mod] = now(); continue; }
      const idleFor = now() - (lastAccess[mod] || 0);
      if (idleFor < idleMs) continue;
      idle.push({ mod, idleFor, procs });
    }
    // Least used first (fewest requests since start, then longest idle), a
    // few per sweep so a crowd crossing the line together drains in order.
    idle.sort((a, b) => ((hits[a.mod] || 0) - (hits[b.mod] || 0)) || (b.idleFor - a.idleFor));
    for (const v of idle.slice(0, STOPS_PER_SWEEP)) {
      log(`idle ${v.mod}: ${Math.round(v.idleFor / 1000)}s, no traffic, ${hits[v.mod] || 0} hits (least used of ${idle.length}) → sleeping ${v.procs.map((p) => p.name).join(", ")}`);
      await stopModule(v.mod);
      sweepStats.idleStops++;
      sweepStats.lastIdleStop = { module: v.mod, idleSeconds: Math.round(v.idleFor / 1000), hits: hits[v.mod] || 0, at: new Date().toISOString() };
    }
    // After the idle pass: enforce the memory floor / running cap even when
    // nothing has hit its idle timeout yet.
    await relievePressure();
  } catch (e) { log("sweep error", e.message); }
}
// Self-rescheduling so short idle timeouts fire close to on-time: sweep at a
// quarter of the threshold (min 2.5s), never slower than the env SWEEP cadence.
const sweepDelay = () => Math.max(2500, Math.min(SWEEP_MS, idleMsNow() / 4));
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
  const out = [];
  const jlist = pm2Jlist();
  for (const mod of managedList().sort()) {
    const info = REGISTRY[mod];
    if (!info) continue;
    // Socket-activation mode lists only pm2 modules (no pm2 = can't sleep it).
    if (MANAGE_ALL && !pm2NamesFor(info.dir, jlist).length) continue;
    const held = !!holds[mod];
    const apiUp = !held && await isUp(info.apiPort);
    const appUp = !held && await isUp(info.appPort);
    out.push({
      module: mod,
      apiPort: info.apiPort, appPort: info.appPort,
      running: apiUp || appUp, apiUp, appUp, slept: held, heldPorts: held ? slept[mod] : undefined,
      disabled: isDisabled(mod), pinned: isPinned(mod), pinnedByEnv: PIN.has(mod), alwaysOn: isAlwaysOn(mod),
      idleSeconds: Math.round((now() - (lastAccess[mod] || 0)) / 1000),
      hits: hits[mod] || 0,
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
      policy: `idle = no traffic on its ports and <${BUSY_CPU}% cpu; least used slept first, ${STOPS_PER_SWEEP} per sweep; slept ports held → first connection wakes it`,
      sweep: sweepStats,
      activation: { ...activationStats, held: Object.keys(holds).sort() },
      managedMode: MANAGE_ALL ? "all" : (MANAGED.size ? "list" : "all"),
      exempt: [...EXEMPT],
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
      if (!isManaged(mod)) return send(400, { error: `${mod} is not activator-managed` });
      const port = REGISTRY[mod].appPort || REGISTRY[mod].apiPort;
      if (isAlwaysOn(mod) && (action === "disable" || action === "sleep" || action === "unpin")) {
        return send(409, { error: `${mod} is always_on in its config.json — remove that flag to ${action} it` });
      }
      switch (action) {
        case "disable": // off, and stays off
          setMembership(overrides.disabled, mod, true);
          setMembership(overrides.pinned, mod, false);
          saveOverrides(); await stopModule(mod, { hold: false }); break;
        case "enable":  // on-demand resumes (does not force-start)
          setMembership(overrides.disabled, mod, false); saveOverrides(); break;
        case "pin":     // always on
          setMembership(overrides.pinned, mod, true);
          setMembership(overrides.disabled, mod, false);
          saveOverrides(); await wake(mod, port); break;
        case "unpin":
          setMembership(overrides.pinned, mod, false); saveOverrides(); break;
        case "sleep":   // stop now (will still wake on next request)
          // Pinned means the sweep puts it straight back — refuse rather than
          // stop it for five seconds or silently discard the pin.
          if (isPinned(mod)) return send(409, { error: `${mod} is pinned always-on — unpin it first` });
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
  log(`activator on :${PORT} — ${Object.keys(REGISTRY).length} modules, idle=${IDLE_MS / 1000}s, pinned=[${[...PIN].join(",")}], managed=[${MANAGE_ALL || !MANAGED.size ? "ALL" : [...MANAGED].join(",")}], exempt=[${[...EXEMPT].join(",")}], minFree=${MIN_FREE_MB}MB, maxRunning=${MAX_RUNNING || "∞"}`);
});
