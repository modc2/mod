---
name: logo
description: The fleet's brand-mark service — every module's logo (glyph, image URL or upload) kept in one place and changeable only by that module's own configured owner, proved with a mod-protocol token. CLI, API :50760, console :50761/logo.
type: orbit-module
---

# logo

Where a module's mark lives, so that the process which *draws* it is not the
process which can *change* it.

## When to reach for it

- "set/change this module's logo" → `m logo/glyph|url|upload <module> …`
- "who is allowed to change it" → `m logo/owner <module>` — names the address
  and where it came from, instead of you guessing at a manifest
- "what mark should I draw for X" → `GET /logo/X`, public, no token
- "which modules have marks" → `m logo/marks`
- a new console needs branding → read `GET /logo/{you}`, forward the owner's
  token on write. Do **not** store the mark yourself.

## The one rule

A mark may only be changed by the owner of the module it is drawn on:

1. `{module}/config.json` → `owner`
2. `~/.mod/{module}/owners.json` co-owners (bare array or `{"addresses": []}`)
3. this deployment's owner — **only** if the module declares none at all

Proof is a mod-protocol token (`m.mod('auth')().token({'scope':'logo'})`, or one
browser `personal_sign`) as `Authorization: Bearer …` — or as `x-mod-token`
when `Authorization` is already carrying some other session, which is how a
proxying console forwards the owner's signature without displacing its own.

Reads need nothing. `LOGO_OPEN=1` drops the gate for local dev only, and
`/status` says so.

## CLI

```bash
m logo/status                       # auth mode, limits, where state lives
m logo/marks                        # every module that has set a mark
m logo/owner build                  # who may change build's mark

m logo/glyph  build 'X'             # 1-4 characters
m logo/url    build https://ex.com/mark.png
m logo/upload build ./mark.png      # <=512KB, png/jpeg/webp/gif/svg
m logo/reset  build                 # back to the protocol cube
m logo/serve                        # pm2: logo-api :50760 + logo-app :50761
```

CLI writes go through the same gate as HTTP — the box's key still has to be
the module's owner. Running on the host grants nothing extra.

## Python

```python
import mod as m
logo = m.mod('logo')()
logo.get('build')                   # {'module': 'orbit/build', 'logo': {...}}
logo.owner('build')
logo.glyph('build', 'X')
logo.set('build', image='./mark.png')
logo.reset('build')
```

## HTTP

```bash
curl localhost:50760/logo/build
curl localhost:50760/logo/build/owner
curl -X POST localhost:50760/logo/build \
  -H "authorization: Bearer $MOD_TOKEN" \
  -H 'content-type: application/json' -d '{"glyph":"X"}'
```

`{module}` may be qualified: `orbit/store` vs `core/store`. Bare names resolve
**core-first**, matching `m.mod(name)`.

## Traps

- **A save that "does nothing" is usually the wrong key.** The error names both
  addresses — read it rather than retrying.
- **A module with no `owner` in its manifest cannot be painted** on an
  unclaimed deployment. Claim the deployment, or declare an owner.
- **The `config.json` mirror is advisory.** `~/.mod/logo/marks/` is the source
  of truth; the manifest's `logo` field is a copy for catalogs and is skipped
  silently if the file is read-only or mid-write.
- **An upload is served under `default-src 'none'; sandbox`.** An SVG mark
  renders and does nothing else — do not expect it to animate or fetch.
- **Consumers should proxy the bytes through their own origin** (build serves
  `/build/api/logo/image`), so the header keeps working where only they are
  exposed.
