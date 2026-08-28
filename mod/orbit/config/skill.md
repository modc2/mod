---
name: config
description: The config app — core/config's Config tree repr as a web app at modc2.com/config. Renders any module's config.json (or any pasted JSON) as the clean aligned tree with collapse, type colors, and click-to-copy dot-paths. Use to eyeball a module's config pretty-printed, demo the Config class, or serve the fleet config browser. Read-only; edits belong to orbit/configs.
---

# config

`core/config`'s `Config` class (dict + attribute access + clean tree repr),
worn as a zero-dependency web app. Read-only sibling of `orbit/configs`
(the manager — get/set/lint/ports live there).

## Common usage
```bash
m config                          # this module's own config, as the tree
m config/get configs              # any module's config via the Config repr
m config/get core/config          # ids disambiguate the shared 'config' name
m config/render '{"a": {"b": 1}}' # any JSON object as the tree
m config/serve                    # app on :50240 → https://modc2.com/config
```

## Key functions
- `get(mod, key=None)` — one config as `{config, text, lines}`; `mod` is an id
  (`orbit/configs`), name, or dir; dot-path `key` narrows.
- `render(data)` — dict or JSON string → `{text, lines}`; `text` is the exact
  Config repr, `lines` its structured twin (indent/key/value/type/dot-path).
- `modules(search=None)` — fleet rows (via orbit/configs) with stable ids.
- `source()` — the Config class doc + source.
- `serve(port=50240)` / `kill()` — the app (FLEET / PLAYGROUND / ABOUT).

## Web app
`GET /api/modules`, `GET /api/config?id=`, `POST /api/render {data}`,
`GET /api/source`. Tolerates the `/config` gateway prefix stripped or kept.
