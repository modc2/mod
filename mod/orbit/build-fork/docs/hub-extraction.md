# Extracting the HUB into its own module

**Status:** design / plan. Nothing implemented yet.
**Question it answers:** "make the hub a separate module that the build mod
uses, so the app is fully modularized."

---

## 1. What the HUB actually is today

The HUB is not one thing. It is a *data plane* and a *view*, both living inside
`orbit/build-fork` and both tangled into unrelated code.

### The view (~1,450 lines, inside a 20,795-line file)

All of it is in `src/app/src/app/page.tsx`:

| What | Lines |
|---|---|
| `HUB_MOD` / `HUB_ENTRY` / `modHref` address plumbing | 74–140 |
| `HUB_HUES` / `hubHue()` — per-card accent | 654–664 |
| `HubSeg` — the segmented control the toolbar is built from | 915–960 |
| `ModuleShot` — screenshot avatar + full-bleed card art | 1113–1200 |
| ~12 `hub*` state atoms (search, sort, filters, preview, owner pick…) | 1450–1500 |
| `probeHubStatuses` — batch port probe → live dots | 6394–6430 |
| `renderHubGraph` — dependency-graph layout | 13022–13178 |
| `renderHubModeSwitch` / `renderHubView` / `renderHubOwnerMenu` | 13183–13725 |
| `renderAddModuleModal` | 13827–13950 |

Plus ~350 lines of `hub-*` CSS in `src/app/src/app/globals.css`
(2076, 2172, 2230, 2273, 2802, 3149, 3666, 3674).

### The data plane

| Concern | Where | Size |
|---|---|---|
| Tree scan → module rows (+registry CID join, mtime, dir hints) | `src/api/src/api.rs:1865` `list_modules` | ~250 lines |
| Screenshot capture + cache | `src/api/src/screenshots.rs` | 383 lines |
| Background CID minting for cards | `src/api/src/autosnap.rs` | 259 lines |
| Batch port probe (live dots) | `src/app/src/app/api/service/route.ts` GET | ~30 lines |
| Privacy/visibility overlay | `src/api/src/privacy.rs` | woven through build |
| Import a module (git/CID) | `src/api/src/api.rs:4723` | owner-privileged |
| Start/stop off a card | `src/api/src/process.rs` | owner-privileged |

The good news: `screenshots` and `autosnap` are almost fully decoupled —
**one call site each** (`api.rs:277`, `api.rs:299` + `main.rs:78`). They lift
out cleanly.

### The precedent that already exists

The HUB's *other* mode used to do exactly what is being proposed. AGENTS was
not build's data — `AgentsPanel.tsx` spoke `orbit/agent`'s protocol over
build's same-origin `/agentmod/*` pass-through (`api.rs:453`). That is still
the shape to copy, and the reason not to reach for an iframe: clicking a card
has to drive build's console state (select module → open its best tab), which
a props/callback boundary does for free. (The panel itself was removed in
2.8.1 — the hub browses modules and nothing else, so the precedent lives on
in this document rather than in the code.)

---

## 2. Recommendation

**Grow `core/hub`. Do not create `orbit/hub`.**

`core/hub` already exists and is already, verbatim, "the module catalog —
every module in the orbit with its description and shipped docs." It has
`modules / names / dir / desc / doc / search / info`. It is the right home.

Creating `orbit/hub` instead would be an active hazard: `Mod.mod()` resolves
via `anchor_object()` with `paths.orbit` searched before `paths.core`
(`core/mod.py:57–62`), so an `orbit/hub` would **shadow** `core/hub` for every
`m.mod('hub')` and `m hub/...` caller — including `core/docs`, which lists
`hub` in its deps and re-exports it. Same footgun as `orbit/config` vs
`core/config`, and this one has a live consumer.

Split the work by plane:

- **Data plane → `core/hub`.** This is the modularization that pays: the
  registry becomes a service that `claude`, `codex`, `agent`, `web` and
  `activator` can all use instead of each re-deriving it.
- **View → `build-fork/src/app/src/components/HubPanel.tsx`.** This is the
  file-size win: ~1,450 lines out of `page.tsx`. Keep **one** renderer.

What stays in build, deliberately: auth, privacy, jobs, process control,
module import/delete/restore, file writes, merge requests. Those are
owner-privileged writes and build owns the auth + `x-sudo` chain. Moving them
would split a security boundary across two processes for no gain.

---

## 3. Phase 1 — `core/hub` becomes a service

New `core/hub/api.py` (FastAPI), port **50520** (50510 `compute` is the next
used port below; 50880 above — 50520/50521 are free).

`config.json` gains: `"port": 50520`, `"route": false`, bind `127.0.0.1`.
**Not routed publicly in phase 1** — see the privacy note in §6.

```
GET  /health
GET  /config
GET  /modules?q=&anchor=&group=      # the row shape build already renders
GET  /modules/{name}                 # one row + parsed config.json
GET  /modules/{name}/doc             # README + skill  (already hub.doc)
GET  /modules/{name}/screenshot      # ?refresh=1 ?fresh=1  → image/png
GET  /probe?ports=8894,8895          # {port: bool} batch
GET  /search?q=
GET  /autosnap/status
POST /autosnap/tick
```

Work:

1. **Port `list_modules` (`api.rs:1865`) to Python, key-for-key.** Same JSON
   keys — `name, path, display, category, has_config, app_url, api_url,
   description, fns, deps, owner, version, cid, created_at, updated_at,
   has_app_dir, has_api_dir, has_server_dir, mods` — so build's UI needs zero
   changes. `core/hub.modules()` is already 80% of the walk; add the
   config parse, `urls.app`/`urls.api`, dir hints, the `registry.json` CID
   join (accept both the bare map and the legacy `{data: …}` wrapper), and
   `newest_mtime` at depth 1. Keep the `mod` root pseudo-row and the
   `orbit/` + `core/` tree-root rows.
2. **Port `screenshots.rs`.** Chromium subprocess through the local caddy
   with the gateway host DNS-mapped to 127.0.0.1. Preserve the policy
   constants exactly: FRESH_TTL 6h, FAIL_TTL 10m, REFRESH_FLOOR 60s,
   FRESH_FLOOR 10s, CAPTURE_TIMEOUT 45s, and the concurrency semaphore —
   they are the only thing keeping a hostile refresh loop off the CPU.
   Cache moves to `~/.mod/hub/screenshots/`; let it re-capture rather than
   migrating.
3. **Port `autosnap.rs`.** 60s tick, PER_TICK 3, doubling backoff
   (600s → 6h cap), MAX_TREE_BYTES 256MB, skip private modules.
4. **Keep the Python fns bit-compatible for `core/docs`.** `modules(group=)`
   must keep returning `{name, group, description, readme, skill}`. New
   fields additive only. Add `probe`, `screenshot`, `snapshot_status`.

~600 lines of Python. Nothing in build changes; nothing user-visible changes.

---

## 4. Phase 2 — build consumes hub

1. **`/hubmod/*path` proxy** in `api.rs` — copy `agentmod_proxy` (`api.rs:453`),
   `HUB_API_URL` defaulting to `http://localhost:50520`. ~40 lines. **Note:**
   `agentmod_proxy` hardcodes `content-type: application/json`; the screenshot
   route needs a byte-passthrough variant that preserves the upstream
   content-type.
2. **`list_modules` becomes an overlay.** Call hub, then apply
   `privacy::hidden_names / denied_roots / records` and
   `auth::get_owner_address()` attribution, then return.
   **Keep the current function as `list_modules_local` and fall back to it on
   any hub error.** This is non-negotiable: build is the console people open
   to fix a broken fleet — it cannot become unusable because another module is
   down. `src/mod.py:1408` already models the idiom (`except ConnectionError:
   m.mods(...)`).
3. **`/modules/:name/screenshot`** → proxy. **`/autosnap/status`** → proxy.
4. **Drop `autosnap::spawn()` from `main.rs:78`** so the loop doesn't run
   twice; keep `BUILD_FORK_AUTOSNAP=0` as the belt-and-braces guard.
5. After a release at parity, delete `screenshots.rs` + `autosnap.rs`
   (642 lines out of build's API).

Build's public API surface is **unchanged** — same paths, same bodies. The
console, the MCP `modules` tool, and `m build-fork/modules` all keep working
without edits.

---

## 5. Phase 3 — the view

Mechanical extraction from `page.tsx` into
`src/app/src/components/HubPanel.tsx` + `HubPanel.css` (the `hub-*` rules move
out of `globals.css`). Move the blocks listed in §1.

The entire coupling surface, once extracted:

```
props in:  { modules, statuses, apiUrl, token, address, mode, ...filters }
callbacks: onOpen(module) · onStart(name) · onImport(spec) · onOpenAgentFolder(path)
```

Everything else in those 1,450 lines is local state. `onOpen` is the only one
that reaches back into console internals (`resetModuleState` → `selectModule`
→ `setSidebarView(getBestTab(m))`), and it stays in `page.tsx` as a closure.

**If `/hub` later needs a public front door, do not fork the markup** — have
hub's app iframe `/build-fork/hub`; the console already carries an `isEmbedded`
recursion guard (`page.tsx:15619`). Two renderers of the same card wall is the
mistake this fleet has already paid for elsewhere.

---

## 6. Risks, and one live bug found on the way

**The name is already contended — today, in production.** `core/hub` has a
`config.json`, so it is scanned into build's registry. Verified against the
running API:

```
$ curl -s 'localhost:8894/modules?q=hub'
hub | core | /root/mod/mod/core/hub
```

It passes `isRealModule` (`page.tsx:6369`) via `has_config`, which means
`hubIsOurs` (`page.tsx:15626`) is **already `false`**. Consequences right now:

- HUB no longer appears as an omnibox suggestion (`15634`);
- typing `hub`, or loading `/build-fork/hub`, resolves to the core/hub *module
  card* instead of the hub view (`15683`);
- `HUB_ENTRY` (`128`) is dead code.

The header HUB pill still works, which is why this hasn't been noticed. Phase 1
makes it permanent, so pick one before starting:

- **(a) Reserve the name for the view** — drop `hubIsOurs`, always treat
  `hub` as the console address. Cost: core/hub's card deep-links to the hub
  view. Recommended; it's one line and matches what users expect.
- **(b) Give the view a non-module address** (`/build-fork/` root only, or a
  sigil like `/build-fork/~hub`). Cleaner conceptually, more churn.

**Privacy.** Hub returns the raw on-disk catalog; build applies the visibility
overlay on the way out. That keeps the redaction in the module that owns the
secret material — but it also means **hub must not be routed publicly in
phase 1** (`"route": false`, bound to 127.0.0.1). Exposing `/hub` on the
gateway is a phase-4 decision that first needs a visibility story of its own.

**Circular dependency.** build → hub is a hard dep (with fallback). hub → build
must stay soft or absent. Do not have hub call back into build's privacy
endpoint.

**`core/docs` regression.** It deps on `hub` and re-exports `m docs/modules` /
`m docs/doc`. Phase 1 must not change those return shapes. Worth a smoke test
in the phase-1 acceptance list.

---

## 7. Cost

| Phase | Work | Deliverable |
|---|---|---|
| 1 | ~600 lines new Python in `core/hub` | Catalog is a service; nothing else changes |
| 2 | ~120 lines Rust added, ~640 deleted | build consumes hub, with fallback |
| 3 | ~1,450 lines moved (no logic change) | `page.tsx` drops ~7%; hub is a component |

Phases 1 and 2 are independently shippable and independently revertible.
Phase 3 is a pure move and can happen before, after, or in parallel.
