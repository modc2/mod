# pm — core process manager

Supervises module processes and **standardizes their environment on nix**. Every
service is launched inside its *nix image* — the module's own `flake.nix` if it
ships one, otherwise the shared [`core/nix`](../nix) env. pm2 is the default
supervisor backend; it's swappable ("or a better one").

## How it works
For each service `pm` writes a tiny launch wrapper:
```bash
cd <service dir>
eval "$(nix print-dev-env <image>)"   # import the nix image's PATH/env
exec bash start.sh                     # exec the real server → pm2 tracks it
```
`nix print-dev-env` imports the image's environment into the shell, then the
server is `exec`'d so pm2 tracks the **real process** (no lingering `nix develop`
parent). Verified: a process started this way runs from `/nix/store/...` rather
than the host toolchain.

If nix isn't available, the wrapper runs the command bare — graceful fallback.

## CLI (via `m`)
```
m pm/start <module> [target=api|app]   # launch in the nix image, under pm2
m pm/stop  <module> [target]
m pm/restart <module> [target]
m pm/ps   [module]                     # status of pm-managed services
m pm/logs <module>
m pm/image_info <module>               # which image + services would be used
```

Service discovery is convention-based: `src/api/start.sh` / `api/start.sh` →
`api`, `src/app/start.sh` / `app/start.sh` → `app`, else a top-level `start.sh`
→ `main`. pm2 names are `<module>.<service>`.

## Relationship to the rest
- [`core/nix`](../nix) defines the reusable image; `pm` runs things inside it.
- The claude module's `process.rs` and `core/server/activator` already pm2-manage
  the live fleet; `pm` is the standardized, nix-first front-end to that model and
  the migration path off per-module Docker (Docker's apt env → the nix image).
