# config — every config, pretty

The web face for `core/config`'s `Config` class: a dict subclass with
attribute access and a clean aligned tree repr. This module renders any
module's `config.json` — and any JSON you paste — through that repr, live at
**https://modc2.com/config**.

Two config modules, one lens:

- `core/config` — the library (`Config` class). This app imports and showcases it.
- `orbit/configs` — the fleet **manager** (get/set/lint/ports). This app reuses
  its scanner for fleet data and stays read-only.

## The app

Zero-dependency (Python stdlib only), one port (`:50240`), read-only.

- **FLEET** — searchable module rail; the selected `config.json` rendered as
  the Config tree. Collapse nested blocks, click a row to copy its dot-path
  (`c.urls.app`), flip to raw JSON, copy either view.
- **PLAYGROUND** — paste any JSON object, see its tree render live.
- **ABOUT** — the library's docstring, the HTTP API, and the full source.

```bash
m config/serve          # → http://localhost:50240  (modc2.com/config via caddy)
m config/kill
```

## CLI

```bash
m config                          # this module's own config, as the tree
m config/get configs              # any module's config via the Config repr
m config/get core/config          # ids disambiguate the shared 'config' name
m config/get configs urls.app     # dot-path into one key
m config/render '{"a": {"b": 1}}' # any JSON as the tree
m config/source                   # the Config class source + doc
```

## API

```
GET  /config/api/modules?search=   fleet rows (id, name, icon, ports…)
GET  /config/api/config?id=        one config → { config, text, lines }
POST /config/api/render {data}     any JSON object → { text, lines }
GET  /config/api/source            the Config class doc + source
```

`text` is the library's exact repr; `lines` is its structured twin
(`{i, k, kp, v, t, p}` — indent, key, padded key, value, type, dot-path) so
clients can add colors, collapse, and copy-path without re-deriving layout.

## Naming

The directory and route take the singular name: `orbit/config` is the app,
`core/config` stays the library (imported by path, untouched). `m configs/lint`
flags the shared `"config"` name across the two — intentional; use ids
(`core/config`, `orbit/config`) wherever it matters.

## Tests

```bash
pytest orbit/config/tests -q
```
