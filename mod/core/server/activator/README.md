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
- **Pinned modules** (`ACTIVATOR_PIN`, default `web,claude`) never sleep.

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
