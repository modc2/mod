# Module Activator — scale-to-zero for the mod fleet

A tiny zero-dependency Node reverse-proxy that puts the local (pm2-managed)
modules to sleep when idle and wakes them on demand.

- **Idle stop:** a background sweep (every quarter of the idle threshold, min
  2.5s) looks for modules idle longer than the threshold (`idleSeconds` in
  overrides.json — **60s** on this host — else `IDLE_SECONDS`). "Idle" = no
  connection on any port the module listens on carried traffic within the
  window (`ss -ti` lastsnd/lastrcv, so a parked Caddy keepalive doesn't count)
  **and** its pm2 procs use under `BUSY_CPU`% CPU (a sync loop with nobody
  connected is still working). The least used go first (fewest requests since
  start, then longest idle), `STOPS_PER_SWEEP` (4) per pass.
- **Socket activation (the wake path, 2026-10-16):** when a module is slept the
  activator binds the exact host:ports its procs (and their children) were
  listening on, recorded in `~/.mod/activator/slept.json`. The first
  connection that **sends a byte** releases the holds, `pm2 restart`s the
  procs, waits for the port and splices the waiting connection(s) through —
  ~2-5s cold start. Works for every caller: Caddy routes, the :3001 portal, a
  browser on the bare port, and peer modules dialing each other directly. A
  bare connect-and-close (liveness checks) does NOT wake it and sees the port
  as live. Holds are re-armed on activator start. The log line
  `hold X: connection on :P from <process>` names who woke it.
  Consumers that probe on a timer must skip held ports — `orbit/mcp` reads
  `slept.json` (`upstream::asleep`) for that.
- **Wake via :9000:** the older path still works — `/api/{mod}` / `/{mod}/api`
  → api port (prefix stripped), `/{mod}` → app port, woken then proxied.
- **Pinned modules** never sleep — and are actively kept up: the sweep restarts
  a pinned module's stopped pm2 procs, so a reboot, a crash or a stray
  `pm2 stop` doesn't quietly leave it down. Pin with `ACTIVATOR_PIN` (env,
  default `web,claude`) or per module at runtime with `actl pin <mod>`.
- **`"always_on": true` in a module's config.json** is a pin nobody can lift
  at runtime: it overrides `disabled` in overrides.json (ignored, logged once),
  `actl disable|sleep|unpin` answer 409, and revive retries at most every 60s
  instead of backing off to 15 min. Remove the flag to turn it off. (polymarket.)
- **OOM guard:** when `MemAvailable` drops below `MIN_FREE_MB` (or a wake would
  exceed `MAX_RUNNING` concurrent apps), the least-recently-used managed modules
  are stopped until the box has headroom again — a cold-start on the next hit
  beats the kernel OOM killer picking a victim. Idle (0-connection) modules go
  first; busy ones are only evicted for memory pressure, never for the cap.

Only modules whose pm2 procs live under `~/mod/mod/{orbit,core}/<mod>` are
eligible. Docker-hosted modules (Caddy points them at container names, not
`localhost`) are out of scope.

## Config (env)
| var | default | meaning |
|-----|---------|---------|
| `ACTIVATOR_PORT` | 9000 | listen port |
| `IDLE_SECONDS` | 10 | idle threshold before stopping (`IDLE_MINUTES` still honoured if set instead); overrides.json `idleSeconds` beats it |
| `IDLE_MS` | — | overrides both (ms; for testing) |
| `ACTIVATOR_MANAGED` | `*` | csv of modules the sweep may stop, or `*` = every pm2 module (safe with socket activation) |
| `ACTIVATOR_EXEMPT` | app,api,server,web,claude,build,passport,dns,mcp,agent,updates | never slept/revived: front door, console, auth, UDP, schedulers |
| `BUSY_CPU` | 3 | pm2 CPU% at/above which a module counts as working |
| `STOPS_PER_SWEEP` | 4 | idle modules slept per pass |
| `SWEEP_SECONDS` | 60 | how often the idle sweep runs |
| `WAKE_TIMEOUT_MS` | 30000 | how long to wait for a woken port |
| `ACTIVATOR_PIN` | web,claude | csv of modules that never sleep |
| `MIN_FREE_MB` | 1500 | OOM guard: keep MemAvailable above this; below it the LRU managed modules are stopped (0 = off) |
| `MAX_RUNNING` | 0 | cap on concurrently running unpinned managed modules; waking one more evicts the LRU first (0 = uncapped) |

## Going live (gateway cutover)

The sweep stops idle modules **regardless** of whether the gateway routes
through the activator. So the proxy and the sweep must go live together:

1. `./start.sh` — runs the activator under pm2 on `:9000`.
2. In `/etc/caddy/Caddyfile`, repoint the scale-to-zero modules' blocks from
   `reverse_proxy {$PM2_HOST:localhost}:<port>` to
   `reverse_proxy {$PM2_HOST:localhost}:9000` (keep the `uri strip_prefix
   /api/<mod>` on api blocks — the activator re-strips, so either is fine; keep
   exactly one). Leave **pinned** modules and **docker** modules pointing direct.
3. `caddy reload` (or `systemctl reload caddy`).

**Rollback:** restore the backed-up Caddyfile and `caddy reload`; the modules are
still reachable directly on their own ports, so rollback is instant.

### Caveat to validate before cutover
`pm2NamesFor` associates a pm2 proc with a module by matching its `pm_cwd` /
`pm_exec_path` / args against the module dir. A proc launched from inside another
module's dir can be mis-associated — verify the `start`/`stop` targets per module
(`GET` the activator log on a dry run) before routing production traffic through.

## Host control plane

The host owns the machine, so manual control beats the automation. State lives
in `~/.mod/activator/overrides.json` (`{disabled:[], pinned:[], idleSeconds,
minFreeMb, maxRunning}`) and is re-read on every sweep, so hand-edits take
effect without a restart.

```
actl status              # every managed module: running / slept / pinned / disabled / idle
actl pin     <mod>       # ALWAYS ON — never slept, and restarted if anything stops it
actl unpin   <mod>       # back to on-demand
actl disable <mod>       # OFF and stays off — the activator refuses to wake it (503)
actl enable  <mod>       # on-demand resumes (does not force-start)
actl sleep   <mod>       # stop now (wakes on the next request). Refused while pinned.
actl wake    <mod>       # start now
```

`pin` also wakes the module immediately, and clears `disabled` — the two are
opposites, so setting either drops the other.

**Why pin something.** Scale-to-zero is right for a console nobody is looking
at. It is wrong for anything that has to be *running* rather than merely
*reachable*: a bot polling a market, a watcher tailing a chain, a scheduled
loop. Those have no inbound request to wake them, so a slept module simply
stops doing its job until someone visits it. Pin those. Each pinned module
holds its memory for good — that's the trade, and why the OOM guard never
evicts one.

The control plane binds to localhost only (the gateway never routes
`/_activator`). Anything off-box drives it through a host-side surface that
authenticates first — e.g. orbit/build's console, whose **INFO → Module map**
card shows whether a module sleeps or is always on and flips it with one
owner-gated button (`PATCH /build/api/activator {module, pinned}`).
