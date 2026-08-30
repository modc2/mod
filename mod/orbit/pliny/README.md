# plinyville 🐉

A mod that mirrors **[github.com/elder-plinius](https://github.com/elder-plinius)**
as a live, searchable repo gallery, a **market** that turns every one of his repos
into its own mod, an **arcade** that *runs* the third of them that are browser
apps, a **taxonomy** that says which sort each repo is, an **agent** that reads
the corpus for you when a filter box will not do, and a defanged host for his web
app, **plinyworld**.

Three surfaces over one core (`plinyville.py` + `market.py`), so the browser, the
CLI and an agent can never get different answers to the same question:

| surface | port | what it is |
| --- | --- | --- |
| **api** | `50592` | the JSON mirror + the market — repos, trees, files, READMEs, code search, `/market`, `/m/<repo>/*`, the exhibit report. Also serves **MCP** at `POST /mcp` (and `POST /m/<repo>/mcp`). |
| **app** | `50593` | the browser side — the market gallery, a per-mod file browser at `/m/<repo>`, **the repos that are apps, running sandboxed** at `/m/<repo>#run`, and the hosted plinyworld exhibit. Holds no data; proxies `/api/*` to the api. |
| **mcp** | stdio | `python3 mcp.py` — the same tools for MCP clients that prefer a subprocess; `--all` serves the ALL registry. |

One MCP connection covers the whole thing: **`POST /mcp/all`** serves the corpus
tools *and* one tool per elder-plinius repo — [see below](#mcpall--one-server-every-pliny-repo-as-its-own-tool).

## The market — every repo as its own mod

`m pliny/install L1B3RT4S` (or the **＋ install** button in the app, or the
`pv_install` MCP tool) archives a repo's recursive tree, README and readable
files into the **store mod** at `~/.mod/store/plinyville/mods/<repo>/…`,
content-addressed with a real **localfs CID**, and mints a mod manifest for it.
`m pliny/stock` archives **all 46 repos in one run** — it clones them
(see [below](#the-archiver-clones-it-doesnt-call-the-api)) instead of reading
them file-by-file through the REST API, so there is no rate wall to stop at.

Each installed repo is then a self-contained mod, all multiplexed by the one
plinyville process under `/m/<repo>`:

| the mod's… | url | reads from |
| --- | --- | --- |
| **app** | `/pliny/m/<repo>` | a file browser over its own api |
| **api** | `/api/plinyville/m/<repo>/{info,readme,tree,file,search}` | the store archive (offline), GitHub as fallback |
| **mcp** | `POST /api/plinyville/m/<repo>/mcp` | its own MCP server: `<slug>_info/_readme/_tree/_file/_search/_install` |

The catalog itself is store-mod data — `m store/get_json plinyville/market` reads
the index of installed mods, sizes and CIDs.

```bash
m pliny/market                # the market catalog (JSON)
m pliny/mods search=prompt    # short list: name → installed? → wiring
m pliny/install L1B3RT4S      # archive one repo into the store as a mod
m pliny/stock                 # clone + archive the whole market, in one run
m pliny/manifest L1B3RT4S     # one mod's manifest (app/api/mcp wiring)
m pliny/uninstall L1B3RT4S    # drop a mod's archive (mirror untouched)
```

### The archiver clones, it doesn't call the API

Reading a repo through the REST API costs one call for the tree and one per
file, against an anonymous budget of **60 an hour**. That budget is why the
market used to sit at one mod with a red `403: API rate limit` across the top of
the page: `stock` spent it on the second repo and the gallery could not even
re-list itself afterwards.

`clone.py` archives over **git** instead. One shallow clone brings a whole repo
down in a single request, and the git transport is not charged against the REST
budget at all — `gh repo clone` when the GitHub CLI is logged in (its
credentials also lift the API budget for metadata), plain anonymous `git clone`
otherwise. Either way the bundle it writes is the same shape the REST archiver
wrote, so a cloned mod and an API-archived one are indistinguishable: same
`/m/<repo>` app, same api, same MCP server, same localfs CID.

```bash
m pliny/clone L1B3RT4S        # clone one repo and archive it
m pliny/stock                 # all 46 — ~45s, zero REST calls
m pliny/clones                # the cache: heads, sizes, what went stale
m pliny/forget_clones         # reclaim the disk (archives survive)
m pliny/discover              # re-list the repos with no API budget at all
m pliny/stock via=api         # the old REST archiver, if you want it
```

Clones live in `~/.mod/pliny/clones/<repo>` and are a *cache*, not the
archive — the archive is the bundle in the store mod. A second `stock` run
re-fetches each clone and only rebuilds the ones whose HEAD moved, so re-running
it is cheap. Repos with no commits at all (he has three) archive as empty mods
rather than as failures.

The one thing git cannot do is *list* a user's repos, and that list was the last
thing needing REST budget. `m pliny/discover` gets it from `gh repo list`
when the CLI is logged in and from the public repositories page otherwise —
merging into the gallery, never replacing it, so a scrape can only add. The
daily scan falls back to it when the API answers 403, and so does the app's
refresh button.

## RUN — the repos that are apps

A third of this corpus is not prose. ST3GG is a steganography suite, ImageDefender
watermarks an image against model training, R00TS is a d3 dashboard, GLOSSOPETRAE
and ENTHEA and NATURALIS-FUTURA are whole worlds in one file, and FABLE-SHOWCASE
is **57 self-contained demos** in one repo. Reading their source is not the same
as watching them work — so the mod runs them.

```bash
m pliny/run                   # the ARCADE: every repo that is an app
m pliny/run name=ST3GG        # can it run, from where, what it touches
m pliny/audit ST3GG           # just the audit
```

- `GET /api/plinyville/run` — the arcade
- `GET /api/plinyville/m/<repo>/run` — one repo's manifest: entries, URL, audit
- `GET /api/plinyville/m/<repo>/run/<path>` — **the app itself**
- `GET /pliny/m/<repo>#run` — the same thing framed in the console
- MCP: `pv_run`, `op=run` on any `pv_<repo>` tool, `<slug>_run` on a per-repo server

The bytes come from the **clone**, not the store bundle: the archive is capped at
60 text files and carries no images, so it can describe an app but not serve one.
A repo with no clone yet is cloned on demand.

### The sandbox is the feature

Running someone else's JavaScript on a host where **every mod shares one origin**
(and therefore one `localStorage`) is the entire risk here, so:

- Every run response carries **`Content-Security-Policy: sandbox allow-scripts …`**
  and, deliberately, **no `allow-same-origin`**. The document gets an *opaque*
  origin: it cannot read this host's storage or cookies even when opened
  top-level in its own tab. The console's `<iframe>` repeats the same sandbox as
  an attribute — the attribute is the belt, the header is the braces, and only
  the header survives a direct link.
- An opaque origin makes `localStorage` **throw**, which breaks pages that never
  expected to be sandboxed, so served HTML gets a tiny in-memory Storage shim.
  That shim and a provenance chip are the *only* edits to upstream, and both are
  marked `data-pliny=` in the served source.
- **`elder-plinius.github.io` never runs from here.** It is the live
  clipboard-hijack PoC; its RUN points at the defanged exhibit instead, and the
  asset route refuses it outright.
- Nothing executes on the box. These are pages served to a browser.

### It says what it is before you press play

`GET /m/<repo>/run` audits the entry and the scripts beside it and prints what it
finds next to the button: clipboard use, the hosts it calls, storage, camera,
`eval`, an API key someone committed, a back end it expects on `localhost:11434`.
It is a grep, not a proof — it is there so that pressing RUN is a decision.

And a repo that *cannot* run says why, because "not runnable" on its own tells
you nothing:

| verdict | what it means |
| --- | --- |
| `source` | a Vite/CRA entry whose only script is `/src/main.tsx` — the browser cannot compile it (GL4SS, LEAKHUB), or a build template (P4RS3LT0NGV3) |
| `demo` | a front end that loads itself from `localhost:6080` — something else has to be running (anthropic-quickstarts) |
| `python` / `text` | a program to run elsewhere, or a corpus of prompts to read (L1B3RT4S, CL4R1T4S) |
| `incomplete` | its page references files that are not in the repo |

Merely *mentioning* `localhost:11434` does not demote a page — half these apps
offer a local model as an option and run fine without one. Only a page that
*loads its content* from a local service is called a demo.

### …and whether it will still work once it arrives

A page can serve `200` and be completely dead: the bytes are fine, the page
paints, every button does nothing. Three things do that here, and the arcade
reports all three rather than letting you find out by clicking:

- **an unresolved merge conflict.** `<<<<<<< HEAD` is a SyntaxError, and one of
  them takes the whole script with it. Those are repaired on the way out — the
  HEAD side kept, `git checkout --ours` — and the repair is announced in the
  served file. R00TS also ships a *stray* marker with nothing closing it; that
  line is dropped and counted separately, because one leftover marker is still
  the same SyntaxError.
- **a script that will not compile anyway.** When the host has node, every
  script the entry pulls in is run through `node --check` after the repair, and
  a failure is reported with the parser's own message. One shape of that failure
  is now repaired instead: R00TS declares `const words` inside
  `updateWordCloud(words)`, shadowing the function's own parameter, which is a
  SyntaxError that throws the whole file away — the page painted and every
  button on it was dead. node names the line, the declaration keyword is dropped
  so the line assigns to the binding that is already there (which is what the
  code means from that line on), node is asked again, and the repair is
  announced in the served bytes. Anything node still refuses is reported, not
  hidden.
- **a back end that is not here.** T3MP3ST keeps its API address in a settings
  field rather than a literal `fetch()`, so the arcade also treats a localhost
  host repeated through a page as the page's back end and names the port.

The verdicts are cached in `~/.mod/pliny/run.json`, keyed on each clone's state,
so the gallery can put a RUN button on 15 of 47 cards without walking 47
checkouts on every page load.

## BUILD — the apps that ship as source

Three of these repos are real browser apps that nobody ever built. What upstream
committed is a Vite `index.html` whose only script is `/src/main.tsx`, or a
template a node script fills in — a browser loads that as a blank page, so the
arcade called them `needs_build` and the card said *read the source* about an
app that was meant to be played.

The missing step is not clever. It is `npm install && npm run build`, and the
card now offers it:

```bash
m pliny/build GL4SS           # install, build, and the arcade can run it
m pliny/build                 # every receipt + what is one build away
m pliny/build GL4SS forget=1  # drop the build and the node_modules
```

- `GET /api/plinyville/builds` — every receipt, what is buildable, what is blocked
- `GET /api/plinyville/m/<repo>/build` — the plan, or how the last attempt went
- `POST /api/plinyville/m/<repo>/build` — build it (**returns at once**; poll the GET)
- MCP: `pv_build`, `op=build` on any `pv_<repo>` tool
- the console: a **BUILD** button on the card, which becomes **RUN** when it lands

The build runs **inside the repo's own clone**, and that is the whole design: it
writes `dist/index.html` next to the source, and `Runner.entries` walks the same
checkout it always walked, finds a page whose scripts are real JavaScript, ranks
it above the unbuilt stub and serves it out of the same sandbox as everything
else. No new serving path, no new hole.

What it is careful about:

- **Nothing is built unless somebody asks.** The daily scan never triggers one —
  a build is minutes of network and a few hundred megabytes of `node_modules`.
- **`--ignore-scripts` on every install.** This is somebody else's dependency
  tree, and an npm lifecycle script is arbitrary code running as this user. The
  only thing executed is the repo's own declared build.
- **Vite gets `--base ./`.** Vite's default emits `<script src="/assets/…">`,
  which is *this server's* root and not the app's. A page that builds and then
  404s its own bundle is worse than one that never built. Skipping the repo's
  own `tsc -b && vite build` also skips a type check that fails on repos whose
  pages work fine.
- **node is chosen, not assumed.** The system node here is 18 and Vite 7 wants
  20.19+; the builder finds every node on the box (including one in the nix
  store that nothing put on `PATH`) and picks the newest that the package's
  `engines` will admit to.
- **A build that would only produce a broken page is refused before it starts.**
  LEAKHUB builds perfectly and then throws: its bundle reads `VITE_CONVEX_URL`
  at boot, a Convex deployment of its own that is not in the repo. The card says
  that, with the variable named, instead of offering a button that hands back a
  white page.
- **Every build leaves a receipt** in `~/.mod/pliny/builds.json`: the commit it
  was built from, the node it used, the seconds it took, and on failure the tail
  of the log. A build whose clone has since moved past that commit is reported
  **stale** rather than quietly served as if it were current.

Today: **GL4SS** (a WebGL/leaflet "looking glass") and **P4RS3LT0NGV3** (a
universal text encoder and steganography suite, 13 tools) both build in under
ten seconds after the install and are playable; LEAKHUB is the one that is
honestly refused.

## TYPES — which sort of thing do you want

Forty-seven cartridges in one wall is a pile. Thirteen of these repos are
jailbreak prompt collections, eleven are leaked system prompts, sixteen are
browser apps, three are a name and no commits — and `L1B3RT4S` and `AutoTemp`
are not the same kind of object at all. So every repo carries one or more
**types**, and everything that lists repos takes `type=`:

```bash
m pliny/types                      # the taxonomy, with counts
m pliny/types L1B3RT4S             # why this repo is filed where it is
m pliny/market type=jailbreak      # only the liberation prompts
m pliny/run type=jailbreak,app     # a jailbreak you can also press RUN on (AND)
```

```
GET /types                    the taxonomy + counts   GET /types?repo=  the evidence
GET /repos?type=  ·  GET /market?type=  ·  GET /run?type=      (comma-separated = AND)
GET /m/<repo>/types           one repo's types, and why
MCP: pv_types, and type= on pv_repos / pv_market / pv_run
```

| type | what it is | today |
| --- | --- | --- |
| `jailbreak` | prompts that unlock a model — liberation sets, god-mode primers, the DAN family | 13 |
| `system-prompt` | a model's own instructions, extracted and kept verbatim | 11 |
| `redteam` | tooling that attacks or probes a model on purpose | 12 |
| `app` | a page — the arcade can run these here, sandboxed | 16 |
| `tool` | code you run yourself: a CLI, a library, an agent | 28 |
| `writing` | essays, lore, poetry, manifestos | 8 |
| `exhibit` | a live attack, hosted here defanged for study | 1 |
| `empty` | no commits — the repo is a name and nothing else | 3 |

**It shows its receipts.** The classifier is a heuristic over the name, the
description, the topics, the README and the filenames, and every type it hands
out carries the word that produced it and the field it was found in
(`GET /types?repo=L1B3RT4S`). Two inputs outrank the words: what the runner
*measured* (a page we watched load is an `app`, whatever its README calls
itself) and eight hand-pinned repos whose prose does not describe them —
L1B3RT4S's README is ASCII art, and CL4R1T4S never says "system prompt". A pin
is marked `source: pinned` so nobody mistakes a decision for a measurement, and
an unknown type id is a 400, never a silently unfiltered list.

## ASK — the Claude agent reads the corpus for you

"Which of these actually jailbreak Claude, and what does the prompt look like?"
is not a question a filter box can answer. It is a question somebody has to go
and *read* thirteen repos to answer — so the mod hands that job to an agent.

```bash
m pliny/chat "which of these jailbreak Claude, and how" types=jailbreak
```

```
POST /chat        {question, types?, repo?, model?, session?}   the answer
POST /chat/stream the same, as SSE: every tool call as it happens
GET  /chat  ·  GET /.well-known/agent.json                      the agent/1.0 card
```

`POST /chat` runs the **headless `claude` CLI against this module's own MCP
server** — the same `pv_*` tools any agent gets at `POST /mcp` — and answers
with the repo and the path it read it in:

```
claude --print --restricted --tools ""          no Bash, no Read, no WebFetch
       --mcp-config <this module> --strict-mcp-config      no other MCP server
       --allowedTools mcp__pliny__pv_…          eleven read-only tools, no install
```

**The type filter is a real fence.** `{"types": ["jailbreak"]}` starts that MCP
server with `PLINYVILLE_SCOPE` set to the thirteen jailbreak repos, and the
server *refuses* anything else — `ENTHEA is out of scope for this server`. The
limit lives on the tool, not in a sentence in a system prompt, which is the
difference between a boundary and a request. A listing filtered by the fence
also reports how many it hid, so the agent says "out of scope" rather than
"there is no such repo".

Everything it does comes back with the answer: every tool call, the files it
opened, the session id (pass it back as `session` to keep talking), the turns
and the cost. An answer that never opened a file is returned `grounded: false`
and the console says so on the page — the agent is a finding aid for a corpus
this module already serves verbatim, not a source in its own right.

It runs on the host's own Claude account, so a guest gets 12 questions an hour
per address, two at a time (`PLINYVILLE_CHAT_RATE`, `_CONCURRENCY`, `_TIMEOUT`;
a `PLINYVILLE_CHAT_TOKEN` bearer lifts the cap). No `claude` on the box is not
an error either — `GET /chat` says so, and the console's ASK strip goes red
instead of offering a button that spins forever.

## About plinyworld (read this)

The upstream page *looks* like an innocuous poetry site. It is actually a
red-team **proof-of-concept for a clipboard-hijacking ("pastejacking") attack**:
every nav/poem link carries a hidden handler that **silently overwrites your
clipboard** with a payload string plus a **typosquatted phishing URL**
(`paypa1.com`, `g00gle.com`, `am4zon.com`, …). Paste anywhere and you've spread
the attacker's link without ever seeing it.

plinyville hosts it as an **exhibit, not a weapon.** Publishing an exact copy
would put a working clipboard-phishing payload on the public internet, firing at
every unwitting visitor — this fork will not do that. Instead:

- The served page runs **`plinyworld/triggers.defanged.js`**: clicking a trigger
  copies **nothing** and navigates nowhere; it reveals, inline, exactly what the
  live version *would* have copied. The mechanism stays legible for study.
- A banner marks the page as a defanged demonstration.
- The original payload is preserved **verbatim but unrun** at
  `plinyworld/upstream/triggers.js`, and is only ever served as `text/plain`
  (`/plinyworld/payload`, `nosniff`) — readable as evidence, never runnable.
- `GET /exhibit` (and the `pv_exhibit` tool, and the gallery's *what it does*
  panel) report the payload string, the mechanism and the 15 typosquatted links
  **read out of that unrun file** — so you can review the exhibit without
  visiting it. See `plinyworld/SOURCE.md`.

## Use

```bash
m plinyville                       # the repo gallery (JSON)
m pliny/update                # re-pull repos + the plinyworld upstream
m pliny/repos search=prompt   # filter the gallery
m pliny/repo L1B3RT4S         # one repo's live details
m pliny/readme GLOSSOPETRAE   # a repo's README (markdown)
m pliny/tree L1B3RT4S         # walk a repo
m pliny/file L1B3RT4S ANTHROPIC.mkd
m pliny/search "system prompt"
m pliny/exhibit               # what the plinyworld PoC actually does

m pliny/run                   # the ARCADE - every repo that is an app
m pliny/run name=ST3GG        # one repo: does it run, from where, what it touches
m pliny/audit ST3GG           # what the page reaches for, before you run it

m pliny/types                 # the taxonomy: what sort of thing each repo is
m pliny/types L1B3RT4S        # and why this one is filed there
m pliny/market type=jailbreak # any listing takes type= (comma-separated = AND)
m pliny/chat "which of these jailbreak Claude, and how" types=jailbreak

m pliny/scan                  # one scan now (what the daily cron runs)
m pliny/scan_status           # fresh or stale, what changed, the CID
m pliny/cid                   # this module's own content id
m pliny/cron hour=4           # install the daily scan (uncron removes it)

m pliny/serve                 # run api + app in the background
m pliny/status                # is anything listening?
m pliny/worker                # the same two, under pm2
m pliny/route_diff            # what wiring would change in the gateway
m pliny/deploy                # pm2 workers + Caddy routes
```

### api — `http://localhost:50592`, gateway `/api/plinyville`

- `GET /` — info + route list (the null call)
- `GET /repos?search=&limit=&refresh=1` — the cached gallery
- `GET /repo?name=` · `GET /readme?name=`
- `GET /tree?name=&path=&ref=` · `GET /file?name=&path=&ref=`
- `GET /search?q=&limit=` — code search across the user
- `GET /types` · `GET /types?repo=` — the taxonomy, and one repo's evidence
- `POST /chat` · `POST /chat/stream` · `GET /chat` — the agent, its stream, its card
- `GET /exhibit` — what the plinyworld PoC would do, from the unrun payload
- `GET /payload` — that payload, as `text/plain`
- `POST /update` — re-pull repos + the upstream snapshot
- `GET /status` — the daily scan receipt: fresh or stale, what changed, the CID
- `POST /scan` — run the daily scan now
- `GET /run` — the arcade: every repo that is an app
- `GET /m/<repo>/run` — can it run, from which entry, and what it touches
- `GET /m/<repo>/run/<path>` — the app itself, sandboxed (see [RUN](#run--the-repos-that-are-apps))
- `GET /builds` — every build receipt, what is buildable, what is blocked
- `GET /m/<repo>/build` · `POST /m/<repo>/build` — the plan, and the build (see [BUILD](#build--the-apps-that-ship-as-source))
- `GET /tools[?all=1]` · `POST /mcp` — the MCP registry and endpoint
- `POST /mcp/all` — the **ALL** server: every repo as its own tool

### app — `http://localhost:50593`, gateway `/pliny`

- `GET /` — the repo gallery (filter, per-repo README modal, *what it does* panel)
- `GET /m/<repo>` — one market mod: its manifest, its wiring, its files
- `GET /m/<repo>#run` — that mod **running**, framed, with its audit beside it
- `GET /m/<repo>/run/<path>` — the same page on its own, still sandboxed
- `GET /plinyworld/` — the **defanged** exhibit
- `GET /api/*` — proxied to the api, so the page talks to one origin

**The console.** Both pages are one 8-bit console: square corners, 2px borders,
hard offset shadows, scanlines over the lot, and buttons that go *down* when you
press them. Type is two bitmap faces embedded as base64 woff2 in `fonts.py` —
**Press Start 2P** for headings, labels, buttons and badges, **VT323** for prose,
metadata and code — so a page is still one request and renders the same on a box
with no font but DejaVu. Every repo is a cartridge: a title bar filled with the
accent, the name and a `MOD`/`REPO` badge in it, then the body, with the titles
and blurbs clamped so a row of cards is one height and their buttons sit on one
line.

**No glyph icons.** The faces carry a latin subset, the box that serves this has
no emoji font at all, and a smooth vector glyph inside a bitmap UI reads as a
mistake — so the invader, the star, the floppy and the skull are `box-shadow`
sprites drawn from ASCII maps in `SPRITES` (app.py). They take the colour of the
text around them and scale in whole pixels.

**Skins.** The header's theme pill opens 21 palettes — the BUILD console's set
(GLASS, MATRIX, NEON, EMBER, ABYSS, DRIVE, VAPOR, DISCO, BABE, RAINBOW, SURF,
PAPER, GAMEBOY, MARIO, WARP, WIN95) plus PLINY (the default), ACID, VOID,
BUBBLEGUM and HOT DOG, grouped dark and light. A theme declares ten colors in
`THEME_CSS` (app.py) and *nothing else*: every other token — panels, borders,
rings, shadows, the header bar — is a `color-mix` off those, and the geometry is
the same for all 21, so a new skin is one line of palette. The choice is one
`localStorage` key shared by the gallery and the per-mod pages, applied before
first paint so nothing flashes.

The exhibit at `/plinyworld/` is deliberately **not** restyled: it is a fork of
the real attack page and is evidence of what that page looks like. Only the
banner above it is ours.

### mcp — `POST http://localhost:50592/mcp`, or `python3 mcp.py` on stdio

Thirteen corpus tools: `pv_info`, `pv_repos`, `pv_repo`, `pv_readme`, `pv_tree`,
`pv_file`, `pv_search`, `pv_exhibit`, `pv_status`, `pv_market`, `pv_run`,
`pv_install`, `pv_update`. Everything is read-only except `pv_update` and `pv_install`, which
only write this module's own mirror and store.

### mcp/all — one server, **every pliny repo as its own tool**

`POST /mcp/all` (or `python3 mcp.py --all`) serves the same thirteen tools **plus
one tool per elder-plinius repo, named after the repo** — `pv_l1b3rt4s`,
`pv_cl4r1t4s`, `pv_glossopetrae`, … 57 tools today. The tool list *is* the
market: no lookup step, and each tool's description says what that repo is.

Every repo tool takes one `op`:

| `op` | does |
| --- | --- |
| `readme` *(default)* | the repo's README |
| `tree` | list a directory — `path=` |
| `file` | read one file — `path=`, optional `ref=` |
| `search` | grep the repo — `query=` (archives it first if it has to) |
| `info` | the mod manifest + wiring |
| `install` | archive it into the store |

Reads come from the store archive when the repo is installed and live from
GitHub otherwise, so the ALL server answers with or without a network.

```bash
claude mcp add --transport http plinyville http://localhost:50592/mcp/all
m pliny/mcp_all               # the endpoint, the count, the tool names
m pliny/tools all=1           # the full registry
curl "localhost:50592/tools?all=1" # same, over http
```

```json
{ "mcpServers": { "plinyville": { "type": "http", "url": "http://localhost:50592/mcp/all" } } }
```

Prefer the narrow surfaces? `POST /mcp` is the twelve corpus tools only, and
`POST /m/<repo>/mcp` is a single repo's own server with no `name` argument.

## Staying current — the daily scan

A mirror that cannot say how old it is is worse than one that says *stale*. A
cron job runs **one scan a day** (`17 4 * * *`), and the gallery's header carries
what it found:

> **● up to date · scanned 3h ago**  ⬡ QmdEtb…5LQ2

Left pill: the scan receipt — click it to scan now. Right pill: **this module's
own CID**, as registered; click to copy. Hovering the left pill gives the last
scan time, the repo count, what changed, the cron schedule and when the next
scan lands.

One scan does four things:

1. re-pulls the repo list from GitHub,
2. re-pulls the plinyworld upstream snapshot (`index.html`, `triggers.js`, `COMMIT`),
3. re-archives any **installed** market mod whose repo has moved since the last
   scan — over the clone archiver, so the REST budget does not apply (capped at 8
   per scan so one busy night is not an hour of cloning; the next scan continues,
   and the receipt names what it skipped),
4. re-registers the module and records the CID that comes back, so the CID on the
   page addresses the code that is actually serving.

Every run — success or failure — writes a receipt to
`~/.mod/pliny/scan.json` (last run plus the last 20). `GET /status`,
`pv_status` and `m pliny/scan_status` all read that one file, so nothing has
to guess. A scan that fails is recorded as failed: the pill turns red and says
so, instead of quietly aging while the page claims to be current. A good receipt
counts as fresh for 36h (a 24h interval plus slack); after that the pill goes
amber.

```bash
m pliny/cron                  # install the daily job (deploy does this too)
m pliny/cron hour=9 minute=30 # move it
m pliny/uncron                # remove it — nothing else in crontab is touched
m pliny/scan                  # run one now
m pliny/scan_status           # the receipt
tail -f /tmp/plinyville-scan.log   # what cron saw
```

Cron carries almost no environment, so the nightly scan authenticates from disk
(`~/.mod/pliny/github.json`, or the `git` mod's account) — see below. When
it is anonymous and the shared 60/hour budget is already spent, the scan no
longer fails: it re-lists the repos off the public repositories page (the receipt
records `repos_source: github-html` and the REST error it stepped around) and
restocks by cloning, which needs no budget either.

## GitHub rate limits

Anonymous GitHub is **60 requests/hour per IP** — shared by everything on the box.
Archiving no longer touches it ([the archiver clones](#the-archiver-clones-it-doesnt-call-the-api)),
and the repo list falls back to the public page, but the live reads that bypass
the store — `repo`, `readme`, `tree`, `file` and code `search` on a repo that is
not installed — are still REST calls. Past the wall those 403, so authenticate:

```bash
m pliny/token ghp_xxxxxxxx     # validated against /user, stored 0600 → 5,000/hr
m pliny/rate                   # what's left, and where the token came from
m pliny/token clear=1          # forget this module's copy
```

The token is resolved fresh on every call, in this order:

1. `$GITHUB_TOKEN` / `$GH_TOKEN`
2. `~/.mod/pliny/github.json` — what `m pliny/token` writes
3. the `git` mod's connected account (`~/.mod/git/github.json`) — the fleet's PAT,
   borrowed rather than copied

Because it is read per call and not at import, dropping a token in reaches a
server that is already running; no restart. A classic PAT with **no scopes** is
enough — this module only reads public repos. `GET /api/plinyville/rate` reports
the budget (checking it is itself free), and the app shows a strip when the
budget is anonymous or running low. The repo cache lives in
`~/.mod/pliny/state.json`.

## Layout

```
config.json                     module manifest (port=api, app_port=app)
mod.py                          CLI verbs + process/gateway control (anchor)
plinyville.py                   the core — GitHub mirror + exhibit analysis
api.py                          JSON API + MCP endpoint            :50592
run.py                          the arcade - entry discovery, the audit, the sandbox
builds.py                       BUILD - the apps that ship as source, built so they run
kinds.py                        the taxonomy - what sort of thing each repo is, with receipts
chat.py                         the chat - the claude agent, fenced to this corpus
app.py                          gallery UI + exhibit + /api proxy  :50593
mcp.py                          MCP tools (stdio + Streamable HTTP)
scan.py                         the daily scan, its receipts, its crontab entry
test_plinyville.py              smoke test over all three surfaces
plinyworld/
  SOURCE.md                     provenance + what was defanged
  triggers.defanged.js          the SAFE script the served page runs
  upstream/index.html           pinned upstream markup (study copy)
  upstream/triggers.js          original payload — preserved, NEVER wired live
  upstream/COMMIT               pinned upstream commit
```
