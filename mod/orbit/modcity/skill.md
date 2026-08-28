# modcity

Modular housing & cities. Prefab 3 m modules snap onto a grid and stack into
buildings; a style + constraints turn the same bricks into a brownstone, a
laneway suite or a neon tower. Everything is computable: cost, floor area,
embodied carbon, lead time, pro-forma and building-code compliance fall
straight out of the placed modules.

An agent can drive the whole thing from text — no 3D UI, no coordinates.

## When to use

- **Build from a sentence** — `m modcity/build brief="a 2-bedroom Toronto
  laneway house, scandi, with an office, roof garden and solar, under $400k"
  owner=agent:me`. Parses the brief, masses the building on the grid, prices
  it, checks the code envelope, saves it PRIVATE. `save=false` = dry run.
- **Edit from a sentence** — `m modcity/edit design_id=dsn_… instruction="add
  a bedroom and a bathroom, make it japandi" owner=agent:me`.
- **Edit exactly** — `m modcity/edit_design design_id=dsn_… ops='[{"op":"add",
  "module":"bath"},{"op":"constraint","key":"max_budget","value":400000}]'`.
- **Learn the vocabulary first** — `m modcity/agent_spec` returns every legal
  module id, style id, code id, panel id, constraint key and op. Never guess
  an id: unknown ones are rejected, not silently dropped.
- **Check the parse before building** — `m modcity/parse_brief brief="…"` /
  `m modcity/parse_instruction instruction="…"`.
- **Review / share** — `audit` (plan-examiner pass, `provider=rules` is
  offline and always available), `publish_design`, `export_design` (CID).

## The loop an agent runs

```
spec  = m.call('modcity/agent_spec')                       # vocabulary
out   = m.call('modcity/build', {'brief': …, 'owner': …})  # design_id + stats
while not out['compliance']['ok']:
    out = m.call('modcity/edit', {'design_id': out['design_id'],
                                  'instruction': fix_for(out['advice'])})
```

`build` and `edit` both return `stats` (cost, area, carbon, floors, pro-forma),
`compliance` (every rule as `{value, limit, ok}`) and `advice` — one plain
instruction per failing rule, phrased so it can be fed straight back to `edit`.
`edit` also returns `applied`, `failed` and `unparsed`, so a clause the parser
could not read is reported, never ignored.

## Data model

A design is `cells` + `style` + `constraints`:

```json
{"cells": [{"x": 0, "z": 0, "stack": ["living", "bedroom", "solar"]}],
 "style": "scandi",
 "constraints": {"code": "to_laneway", "lot_w": 3, "lot_d": 4, "max_floors": 2}}
```

`x`/`z` are 3 m grid columns, `stack` runs bottom→top, one entry per 3 m
storey — including a roof cap (`solar`, `garden`, `cornice`), which occupies
the top level of its column. Stacks are contiguous from the ground.

## Endpoints

API `:50140` (gateway `/modcity/api`), app `:50141/modcity`.
`GET /agent`, `POST /build`, `POST /design/{id}/edit`, `POST /design/{id}/ops`
mirror the four agent fns. `m modcity/serve` starts both under pm2.

## Notes

- Designs are **private by default** and scoped to `owner` — pass the same
  owner string on every call, or you cannot edit what you built.
- Parsing is deterministic keyword matching, no LLM: the calling agent is the
  language model. Explicit kwargs (`style=`, `floors=`, `bedrooms=`) always
  beat the prose, so mix text with exact numbers when it matters.
- Featured examples are read-only — `copy_design` first, then edit the copy.
- Compliance is massing guidance against Canadian ADU/laneway presets, not a
  stamped approval. Confirm figures with the authority having jurisdiction.
- `m modcity/test` runs the agent rail end to end against a throwaway design.
