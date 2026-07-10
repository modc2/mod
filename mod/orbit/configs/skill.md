---
name: configs
description: Fleet config manager. Scans every module config.json across core/ and orbit/, gets/sets keys by dot-path with automatic backups (restore to undo), lints the fleet (invalid JSON, missing fields, duplicate names, port collisions), and maps claimed ports. Use to inspect or edit any module's config, find a free port, or audit fleet config health.
---

# configs

One place to see, query, edit, and sanity-check every module's `config.json`.
Named `configs` (plural) — the singular name belongs to `core/config` (the
Config library) and `orbit/config` (its read-only app at modc2.com/config).

## Common usage
```bash
m configs                              # every module config, one row each
m configs/get updates                  # full config (add a dot-path for one key)
m configs/set updates icon=📡          # set a key; auto-backup first
m configs/restore updates              # undo the last set/unset
m configs/lint                         # fleet issues (errors first)
m configs/ports                        # port map + collisions (find a free port)
```

## Key functions
- `configs`/`forward` — fleet scan, one summary row per module; `search=` filters.
- `get(mod, key=None)` — full config or one dot-path key.
- `set(mod, key, value)` / `unset(mod, key)` — dot-paths create nested dicts,
  values are JSON-coerced (`8080`→int, `true`→bool); backup taken before every write.
- `backups(mod)` / `restore(mod, backup=None)` — timestamped undo, latest by default.
- `lint(mod=None)` — invalid JSON, missing name/description, name≠dir,
  duplicate names, port collisions.
- `ports()` — every `*_port` key claimed, owners, collision flags.
- `history(mod)` / `diff(mod)` — git log / uncommitted diff of a config file.

## Web app
Zero-dep read-only browser (grid + JSON viewer + ports + lint) on one port:
`m configs/serve` → http://localhost:50230. GET-only API; edits are CLI-only.
