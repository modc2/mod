# Module Activator — scale-to-zero for the mod fleet

A tiny zero-dependency Node reverse-proxy that puts the local (pm2-managed)
modules to sleep when idle and wakes them on demand.

- **Idle stop:** a background sweep (every `SWEEP_SECONDS`) stops any module that
  has been idle longer than `IDLE_MINUTES` **and** currently has zero established
  TCP connections on its api/app ports (the usage signal, polled via `ss`).
- **Wake on access:** every request is mapped to a module + port
  (`/api/{mod}` → api port with the prefix stripped, `/{mod}` → app port). If the
  port is down the activator `pm2 start`s the matching procs, waits for the port,
  then reverse-proxies (http + websocket upgrades). First hit pays a cold-start.
- **Pinned modules** never sleep — and are actively kept up: the sweep restarts
  a pinned module's stopped pm2 procs, so a reboot, a crash or a stray
  `pm2 stop` doesn't quietly leave it down. Pin with `ACTIVATOR_PIN` (env,
  default `web,claude`) or per module at runtime with `actl pin <mod>`.
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
| `IDLE_MINUTES` | 10 | idle threshold before stopping |
| `IDLE_MS` | — | overrides IDLE_MINUTES (ms; for testing) |
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
