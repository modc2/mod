# configs

**The fleet's config manager.** Every module carries a `config.json`; this module
is the one place to see, query, edit, and sanity-check all of them.

- **Scan** — every `config.json` under `core/` and `orbit/` (270+ of them), one
  summary row each, searchable.
- **Edit** — set/unset any key by dot-path (`urls.app=…`), with JSON value
  coercion and an automatic timestamped backup in `~/.mod/configs/` before every
  write, so `restore` can always undo.
- **Lint** — fleet health report: invalid JSON, missing `name`/`description`,
  name↔directory mismatches, duplicate module names, and port collisions (the
  fleet really has some).
- **Ports** — every `*_port` claimed in any config, who claims it, and which
  collide.
- **Web app** — a zero-dependency, single-port, read-only fleet browser: config
  card grid with search, a syntax-highlighted JSON viewer, the port map with
  collisions highlighted, and the lint report. Edits stay on the CLI where
  they're owner-run.

## CLI

```bash
m configs                                   # every module config, one row each
m configs/configs search=chain              # filter
m configs/get updates                       # one module's full config
m configs/get updates urls.app              # one key (dot-paths)
m configs/set updates icon=📡               # set a key (auto-backup first)
m configs/set claude app_port=3117          # values are JSON-coerced (→ int)
m configs/unset updates urls.gateway
m configs/restore updates                   # undo the last set/unset
m configs/lint                              # fleet-wide issues report
m configs/lint updates                      # one module's issues
m configs/ports                             # port map + collisions
m configs/history updates                   # git log of a config
m configs/diff updates                      # uncommitted config changes
```

## Web app

```bash
m configs/serve            # http://localhost:50230 (background; kill with m configs/kill)
```

One port serves the UI at `/` and a JSON API at `/api/{info,configs,config,ports,lint,history}`.
The handler tolerates the `/configs` gateway prefix, so it works behind
`modc2.com/configs` whether or not Caddy strips the prefix.

## Notes

- Named `configs` (plural) because `core/config` — the Munch-style `Config`
  class — already owns the `config` name in the module tree.
- Backups live in `~/.mod/configs/backups/{mod}/{timestamp}.json`; `restore`
  refuses to restore a corrupt backup.
- The web API is **GET-only by design**: browsing is public-safe, mutation goes
  through the CLI.

## Tests

```bash
pytest orbit/configs/tests -q    # runs against a temp fleet; never touches real configs
```
