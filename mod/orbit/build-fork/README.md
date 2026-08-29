<div align="center">

# Build-Fork Mod

**Programmable AI developer interface**

Script tasks with Python. Run jobs through Rust. Version to IPFS. Watch from a retro terminal.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.70+-dea584.svg)](https://www.rust-lang.org)
[![Next.js 14](https://img.shields.io/badge/next.js-14-000.svg)](https://nextjs.org)
[![IPFS](https://img.shields.io/badge/IPFS-versioned-65c2cb.svg)](#versioning)

```
┌──────────────────────────────────────────┐
│  Python SDK  →  Rust Engine  →  Next.js  │
│  34 methods     Axum + SQLite   Terminal  │
│  build-fork/mod.py   api/src/        app/src/  │
└──────────────────────────────────────────┘
```

</div>

## Quick Start

```bash
git clone https://github.com/modprotocol/mod.git
cd mod/mod/orbit/build-fork

pip install -r requirements.txt
./start.sh
```

API on **:8894**, UI on **:8895**. Local mode by default — no wallet needed.

<details>
<summary>Docker</summary>

```bash
docker compose up -d
```

</details>

<details>
<summary>Install from IPFS</summary>

```bash
ipfs get <CID> -o build-fork && cd build-fork
pip install -r requirements.txt
```

Get the latest CID from the on-chain registry: `m.get_cid('build-fork')`

</details>

---

## Python SDK

```python
from build_fork import Mod

c = Mod()
```

### Code Operations

```python
c.analyze_code(path="/project", focus="security")
c.generate_code(description="FastAPI auth with JWT", path="/project")
c.refactor(instructions="Extract into decorators", path="/project")
c.debug(path="/project", error="TypeError on line 42")
c.run_task(task="Add docstrings to public functions", path="/project")
c.batch_process(["Check SQL injection", "Find unused imports"], model="haiku")
c.ask("Explain this error: TypeError on line 42")
c.edit_file("config.py", "Add DATABASE_URL env var")
```

### Jobs

**Local** — fire-and-forget via Claude CLI:

```python
task = c.bg("refactor utils.py to use async", mod="core", model="sonnet")
c.bg_status(task['pid'])
c.bg_list()
```

**Server** — managed execution with live streaming through the Rust engine:

```python
job = c.submit("Build React dashboard", model="sonnet", work_dir="/project")
c.tail(job['id'])          # stream live output (SSE)
c.jobs()                   # list all jobs
c.guide(job['id'], "focus on the API first")  # steer a running job mid-task
c.cancel(job['id'])        # cancel running job
c.delete_job(job['id'])    # remove job
```

The SDK auto-starts the Rust API on first use and shuts it down after idle timeout (default 300s).

### Harness — hand this console a whole run

Another module can treat build-fork as an agent it runs on. Two calls are the whole
contract, the same pair the CLI harness modules answer (`orbit/claudecode`,
`orbit/codexcli`):

```python
c.harness()                # {name: 'buildforkmod', available, server, api, …}
steps = c.run("fix the failing test", path="/project", on_step=print)
```

`run()` submits a job, follows its live output, and translates it into the step
dicts every agent run in the fleet emits (`{tool, params, result|error}`,
ending in `finish`) — so a job here renders in the caller's console exactly
like a native run. The job is independent of the call: every step carries its
`job` id, so a caller that gives up can still follow, steer or cancel it.

`orbit/agent` ships this as the **Build Console** agent (harness `buildmod`);
picking it in that console sends the run here, sandboxed per caller and filed
in this module's ledger.

### Versioning

Semantic versioning backed by IPFS:

```python
c.snapshot("v1.2.0", description="Add auth endpoints")
c.changelog()
c.get_version("v1.2.0")
c.restore_version("v1.2.0")                    # preview (dry run) — open to anyone
c.restore_version("v1.2.0", dry_run=False)     # the real revert — owner's own key only
```

`restore_version` is the one code-writing call that is not part of the delegated
edit surface: `require_root_owner`, not `require_owner`. Editors and BlocTime
holders write freely and still cannot undo what was written. `c.can_revert()`
answers whether a given key holds that authority.

### Prompts (shareable, stored in localfs)

Every task inherits a nice, readable **default system prompt** — no hidden
empty string. Named prompts are saved to a small catalog and pushed into
localfs (IPFS), so each one gets a content-address (CID) you can share. Someone
else pulls it in by CID.

```python
c.default_prompt()                       # the default system prompt, ready to show
c.save_prompt("Rust Reviewer", "You review Rust for correctness…")
c.list_prompts()                         # the gallery — everything saved, newest first
c.share_prompt("p_ab12cd34ef56")         # → { cid, gateway } — the share link
c.import_prompt("Qm…")                   # pull a shared prompt into your catalog
c.delete_prompt("p_ab12cd34ef56")        # author or owner only
```

Bodies live in localfs; the catalog index lives off-tree in `~/.mod/build-fork/prompts.json`.

### Modules

```python
c.create_module("mymod", prompt="Build a web scraper module")
c.edit_module("mymod", prompt="Add rate limiting")
c.modules()
```

### Renaming — the name is wiring, not a label

A module's name is not just its folder. It is the route it answers on
(`/{name}`, `/{name}/api`), the state directory it writes to (`~/.mod/{name}`),
the names of its pm2 processes, the key it holds in the router's overrides and
the activator's lists, this console's per-module records (version log, GitHub
link, screenshot), and the string every sibling uses to declare it in `deps`.
Move only the folder and the module goes dark in eight places at once.

So `rename_module` moves all of it in one operation — stopping the module
first, forgetting its stale pm2 entries, rewriting what points at the old name,
re-filing the host state, re-generating the caddy routes, and starting it again
under its new name if it was up.

```python
c.rename_module("mymod", "betterName", dry_run=True)   # the plan — nothing moves
c.rename_module("mymod", "betterName")                 # do it
c.rename_module("mymod", "betterName", refs="all")     # chase prose mentions too
```

`refs` decides how far the old name is chased **inside the module's own files**:

| mode | rewrites |
|---|---|
| `paths` (default) | only wiring: tree paths, route prefixes, `~/.mod` state dirs, pm2 process names, `m <mod>/fn` call forms |
| `all` | that, plus every whole-word mention — prose and comments included |
| `none` | nothing inside the module; the directory just moves |

The default is deliberately narrow because modules called `store`, `chain` or
`build` say their own name in English constantly, and English is not wiring.
The report tells you what it left behind (`words` per file), so `all` is one
re-run away when the module's name is distinctive.

`dry_run=True` returns the same report without touching anything: the files it
would rewrite and how many references each holds, the host paths it would
re-file, the siblings whose `deps` it would update, and the processes it would
stop and start. In the console this is the **PREVIEW** button on the INFO tab's
rename panel; the rename itself needs an owner sudo signature, same as delete.

Two refusals worth knowing: the console will not rename **itself** (it would
move the tree it is running from and stop the process answering the request),
and it will not rename an orbit module onto a name `core/` already owns —
names are path-derived and core wins, so the result would be a module that
never routes. A **prod Next app** carries its route prefix into its bundle, so
after a rename its assets still point at the old one until it is rebuilt; the
report says so, and restarting it from the APP tile does that rebuild.

### Suggestions — collaborate without forking

A merge request needs someone who can write the change. A **suggestion** needs
only someone with an opinion: any signed-in caller files one against any module,
in words. The module's admin triages the queue — or **plays** it, which hands the
suggestion to the agent as an ordinary edit job **running on the admin's own
account**. So a contributor's text never writes to the tree by itself; playing it
is the owner typing that edit themselves, with the contributor's intent attached.

The **discussion** on a suggestion is open to everyone — no wallet, no
whitelist, no association with the module — because the person who hit the thing
and will never own a wallet is often the one holding the detail that makes it
actionable. Unsigned callers post under a stable `anon:` pen name. That invites
noise, so a thread is never carried whole by the queue: a list sends its last 20
comments and the true `comment_count`, and a reader **refreshes the entire
history** from `/suggestions/{id}/comments` (the console does this on a timer
while a thread is open, and again on every reply). Voting stays signed-in — the
count is meant to be people — and so does filing.

```python
# anyone signed in
c.suggest("polymarket", "show fees in the ledger", "the per-fill fee is in the API but not the table")
c.suggestions("polymarket")            # the queue (public, tail of each thread)
c.suggestion_vote("sg_ab12")           # second it — the open queue sorts by this

# no account required
c.suggestion_comment("sg_ab12", "same, it's confusing on mobile")
c.suggestion_comments("sg_ab12")       # the whole discussion, every time

# the module's admin
c.play_suggestion("sg_ab12", instructions="keep the existing column order")  # → an edit job on YOUR account
c.triage_suggestion("sg_ab12", "rejected", note="already possible in SETTINGS")
c.delete_suggestion("sg_ab12")
```

A play is a normal task: it appears in the ledger, snapshots the module on
completion (the CID lands back on the suggestion), and rolls back like any other
edit. `playing → played | play_failed` folds in from the job on the next read.
In the console this is the **IDEAS** tab, whose badge is the open queue.

### Module process control

Drive any other module's lifecycle through pm2 — the actual supervisor — so a
stop stays stopped and a restart is reliable (a raw port kill is just undone by
pm2's autorestart). Owner-only; cross-module actions require an owner sudo
signature when the API runs under the pm2/root deployment.

```python
c.module_status("openplay")     # {running, processes:[{name, pm_id, status, ...}]}
c.restart_module("openplay")    # kill + bring back up via pm2
c.stop_module("openplay")       # stays down (no autorestart)
c.start_module("openplay")      # pm2 start, or bootstrap from ecosystem.config.js / start.sh
c.module_process("openplay", "restart")   # generic form; target a single service:
# POST /modules/openplay/process {"action":"restart","target":"api"}
```

**Pluggable backend.** The supervisor is chosen per module so the calls above
behave identically regardless of how a module is run:

| Backend | How processes are found | Actions |
|---|---|---|
| `pm2` | pm2 procs whose cwd / exec / launch-arg lands inside the module dir (so Python modules launched from repo root via `--app-dir <module>/api` still resolve) | `pm2 stop/start/restart <id>` |
| `systemd` | loaded units named `mod-<name>[-api/-app].service` | `systemctl stop/start/restart` |
| `generic` | the PID *listening* on the module's `port` / `app_port` (via `ss`) | SIGTERM to stop; `start.sh` to (re)start |

Selection order: `MOD_PM` env override → the module's `config.json`
`"process_manager"` field → **auto** (pm2 if it has procs, else a loaded
systemd unit, else generic). The JSON response reports the resolved `backend`.

**Opt-in nix.** If a module ships a `flake.nix` (or `shell.nix`) and `nix` is
installed, any launcher build runs itself — the generic backend's `start.sh`,
or the bootstrap fallback — is wrapped in `nix develop --command …` /
`nix-shell --run …`, so the module starts inside its declared environment. pm2 /
systemd units carry their own env and are left untouched. `nix_env` in the
response flags whether the module declares one.

---

## Web UI

Retro terminal dashboard at **localhost:8895**.

| Feature | Details |
|---|---|
| Job submission | Live SSE streaming with output tail |
| File browser | Syntax highlighting for 20+ languages |
| Search | `Cmd+P` file search, `Cmd+Shift+F` content grep |
| Wallet auth | MetaMask, SubWallet, BIP-39, password-derived key |
| ▶ START | Anything the console sees as down gets a START button — see below |
| API tab | The **selected** module's API, not this console's — see below |
| Themes | gameboy (default), mario, warp, glass, rainbow, matrix, neon, ember, abyss, drive, vapor, disco, babe, surf, paper, win95 |
| Fonts | default (Inter + JetBrains Mono), zoodmantra (Syne + Space Mono), antireal — picked independently of the theme |
| Extras | Image paste, ASCII boot screen, CRT aesthetic |
| The mark | The mod protocol's **cube** in the top-right corner — the owner clicks it to set the module's own logo |

### The mark in the corner

The far top-right of the header — past the account chip — carries the mod
protocol's cube. It is the protocol's mark, not this console's own device:
build-fork is one module among the fleet's, and it should read that way.

The **owner** clicks it to change it:

- **glyph** — 1–4 characters (`◆`, `✦`, `Ω`)
- **image url** — an `http(s)` image you already host
- **upload** — PNG / JPEG / WEBP / GIF / SVG up to 512KB
- **cube** — back to the protocol's default

#### It is not build-fork's state

The mark lives in the **[`logo`](../logo) module** (`:50760`), which keeps every
module's mark and accepts a change only from the address in that module's own
`config.json`. Build renders the editor and carries the signature; it cannot
produce one.

That separation is the point. This app's route handlers run as root on the
host — if they were also the only gate on build-fork's branding, the gate would mean
very little. So a save costs **one wallet signature**, and it is the signature
that authorizes it:

```
browser            build-fork (:8895)                 logo (:50760)
  │  personal_sign     │                              │
  ├───────────────────▶│  POST /build-fork/api/logo        │
  │                    │  authorization: <build-fork token>  ← you are signed in here
  │                    │  x-mod-token:   <owner sig>    ← this is what authorizes
  │                    ├─────────────────────────────▶│  verify m.mod('auth')
  │                    │                              │  signer == build-fork's owner?
```

Reads are proxied and cached at `~/.mod/build-fork/logo-cache.json`, so a sleeping
logo module shows yesterday's mark rather than blanking the header — the panel
says when it is showing a cached answer. Uploaded bytes are relayed through
this origin so the header still works on a deployment where only build-fork is
exposed.

With no signing wallet in the session (local mode, a read-only session), the
panel points at the host instead: `m logo/glyph build-fork '◆'`.

| Route | Auth | What |
|---|---|---|
| `GET /build-fork/api/logo` | public | `{kind, glyph\|src}` + `source`, `owner`, `cli` |
| `POST /build-fork/api/logo` | owner session **and** `x-mod-token` | `{glyph}` / `{url}` / `{dataUrl}` / `{reset:true}` |
| `GET /build-fork/api/logo/image` | public | the uploaded bytes, relayed (`?v=` cache stamp) |

Migrating a deployment that still has the old `~/.mod/build-fork/logo.json`:

```bash
python3 ../logo/scripts/migrate_from_build.py --module build-fork --write
```

### Every place is an address

Where you are in the console is in the address bar, because every place in
the console is a module:

| URL | Opens |
|---|---|
| `/build-fork/{mod}` | That module — its app, files, API, tasks, versions |
| `/build-fork/hub` | The **hub**, which is a module like any other |
| `/build-fork` | The front door; resolves to `/build-fork/hub` |
| `/build-fork?mod={name}` | The fallback for names Next already serves — `api`, `auth`, `_next` |

So a module page is linkable (`https://modc2.com/build-fork/polymarket`),
refreshable, bookmarkable, and the browser's **back/forward** walks the trail
of modules you opened. The picker pill in the top-left is that address: it
says `hub` on the hub, autocompletes `hub` like any other name, and offers a
**Console** link in every module's share-QR next to its public app link.

Navigation moves `window.history` directly rather than routing: the console is
one long-lived client tree, and a router push would remount it — throwing away
open panes, live streams and scroll — every time you switched modules.

### Press START

Whenever something the console can see is off, there is a START button on
screen — you never have to go find the right tab first.

- **The bar under the header** names what's down and starts it from any view:
  the console's own API (via the app's same-origin `/api/service`, which is
  still alive when the API isn't), the open module's API, its app. Press it
  and it starts everything on the list. `✕` hides it until something *else*
  goes down.
- **Hub cards** carry a `▶ START` chip while a module is offline, so a stopped
  module comes back without opening it first. Only modules that actually
  declare a service get one — a bare folder has nothing to start.
- **The APP tab's empty state** keeps its own START for the app alone.

Starting anything is owner-gated (pm2 through `/modules/{name}/process`);
signed out, the button says `START · SIGN IN` and opens the account panel.

### The API tab describes the module you're looking at

Standing in another module and opening API used to list *build-fork's* endpoints,
because the tab only ever loaded this console's `/schema`. It now resolves the
selected module's own surface, in order, and says which one answered:

| Source | Who has it |
|---|---|
| `GET {base}/schema` | This console and its forks — a hand-written catalog with auth levels |
| `GET {base}/tools` | MCP servers (targon, x, lium, chutes, agent…) — each tool becomes a row, TRY IT sends one `tools/call` to `/mcp` |
| `GET {base}/openapi.json` | Every FastAPI module (chain, bloctime, copytensor…) — paths, query params and body fields become the playground's inputs |
| `config.json` | The `endpoints` block, or failing that `fns` — enough to show what a stopped module exposes |

`base` is the gateway path `/api/{mod}` on this origin (the same convention as
this console's own `/api/build-fork`), so foreign calls are same-origin and carry
**no** bearer of ours — that token is this console's, not theirs. A module with
none of the four says so plainly instead of showing someone else's API.
Catalogs are cached for an hour; `↻` in the filter row reloads one.

### Sharing a session

Every finished task is pinned to localfs as a bundle (prompt, transcript,
edits, module + version CID). The **⛶ QR** button on a task turns that CID
into a link — copy it, or scan the QR:

| Link | Opens |
|---|---|
| `/build-fork?task=<cid>` | The session itself — prompt, transcript, EDITS, AUDIT — read-only |
| `/build-fork?replay=<cid>` | The same session, plus the composer pre-filled to run it again |
| `<cid>` | The raw bundle, resolvable on any console sharing the blob store |

Both links resolve by CID, so a session shared from one console opens on
another even when its row was never in that console's ledger — those show a
`⇄ shared` pill and no Stop/Delete, since there's nothing local behind them.
Recipients need no wallet: the task ledger is world-readable. Tasks inside a
private module, or sealed by their author's vault, stay closed to everyone
else — sharing the link doesn't unseal them.

---

## MCP

The whole console is an MCP server. Streamable HTTP at `POST /mcp`, and stdio
for clients that want a subprocess:

```bash
# Streamable HTTP
curl -sX POST http://localhost:8894/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# stdio — a bridge to the running API, not a second server
build-fork-jobs --stdio
```

```jsonc
// claude_desktop_config.json / .mcp.json
{ "mcpServers": {
    "build-fork": {
      "command": "/path/to/build-fork/src/api/target/release/build-fork-jobs",
      "args": ["--stdio"],
      "env": { "BUILD_FORK_API_URL": "http://localhost:8894", "BUILD_FORK_TOKEN": "0xYou:1234:sig" }
    } } }
```

`initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`,
`prompts/list`, `prompts/get`, `ping` — single messages or JSON-RPC batches.

**Auth is the REST auth.** Every tool is fulfilled by re-entering this server's
own REST route over loopback carrying the caller's `Authorization` header, so
there is exactly one definition of who may do what. An anonymous MCP client
gets precisely the tools tagged `[public]`; an owner gets everything; a tool
that needs a sudo signature says so in the same words the browser would see.
Pass one as a `sudo` argument and it goes out as `x-sudo`.

| | tools |
|---|---|
| console | `build_info` · `whoami` · `system_status` · `costs` |
| tasks | `submit_task` · `list_tasks` · `get_task` · `wait_task` · `steer_task` · `cancel_task` |
| modules | `list_modules` · `get_module` · `module_process` · `snapshot_module` · `module_versions` · `restore_module` · `undo_module` (both owner-key-only) |
| files | `list_files` · `read_file` · `write_file` · `search_code` |
| merge requests | `fork_module` · `open_merge_request` · `list_merge_requests` · `merge_request_diff` · `review_merge_request` · `merge_merge_request` |
| suggestions | `list_suggestions` · `suggest` · `suggestion_thread` · `comment_suggestion` · `play_suggestion` · `triage_suggestion` |
| arenas | `arena_list` · `arena_tools` · `arena_call` · `arena_enter` · `arena_withdraw` · `arena_status` · `arena_leaderboard` · `arena_match` |

Resources: `build-fork://info`, `build-fork://arenas`, `build-fork://modules`.
Prompts: `ship_change` (fork → change → merge request), `compete`.

---

## Arenas

This console speaks `arena/1.0` in both directions.

**Outward.** It finds arenas by reading the fleet's configs — every module
declaring `protocol: "arena/*"` — so an arena installed tomorrow is reachable
with no change here. Arenas share a protocol but not a vocabulary (the wasm
arena `enter_player`s, the coding arena `enter_agent`s), so `arena_enter` and
`arena_match` probe the peer's own `tools/list` and adapt to what it offers.

```
arena_list                    → what's here, and which of them is answering
arena_tools   {arena}         → that arena's vocabulary
arena_call    {arena,tool,…}  → any tool on any arena; the general bridge
arena_match   {arena,subject,entrants}
```

**Inward.** `arena_enter` registers this console in an arena as an `http`
competitor pointed back at us, and the arena then calls:

| | |
|---|---|
| `POST /arena/solve` | `{task, language?, mode?}` → `{code, language}` — coding arenas |
| `POST /arena/play` | `{view, seat?}` → `{move}` — game arenas |

Each accepted call runs a **real agent job on this box**, which spends money.
So both endpoints are default-deny:

- off until the owner runs `arena_enter` (`403` before that),
- then only for a caller presenting the shared key minted at that moment
  (`401` without it),
- key stored `0600` in `~/.mod/build-fork/arena.json` and never returned by any
  read endpoint,
- `arena_withdraw` tells the arena and switches the endpoints back off — and
  switches them off even if the arena is down, because the local switch is
  what actually stops us answering.

```bash
# enter, compete, leave
m build-fork/arena_enter openarena
m build-fork/arena_match openarena two-sum build,free-default
m build-fork/arena_withdraw openarena
```

One agent job per move makes `play` slow — a game arena should expect seconds
per turn, not milliseconds. `solve` is the natural fit.

---

## REST API

Rust server (Axum + SQLite) on port `8894`.

<details>
<summary>Public Endpoints</summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/config` | Module config |
| `GET` | `/owner` | Owner address |
| `GET` | `/repos` | List git repos |
| `GET` | `/modules` | List orbit modules |
| `GET` | `/modules/{name}/config` | Module config by name |
| `GET` | `/changelog` | Version changelog |
| `GET` | `/versions/{version}` | Version entry |
| `GET` | `/files/tree?path=&depth=` | Directory tree |
| `GET` | `/files/content?path=` | File contents |
| `GET` | `/files/search?path=&query=` | Search file names |
| `GET` | `/files/grep?path=&query=` | Grep file contents |
| `GET` | `/suggestions?module=&status=&author=` | Every suggestion (public queue) |
| `GET` | `/modules/{name}/suggestions` | One module's suggestion queue |
| `GET` | `/suggestions/{id}` | One suggestion + its discussion |
| `GET` | `/suggestions/{id}/comments` | The ENTIRE thread (lists carry only its tail) |
| `POST` | `/suggestions/{id}/comment` | Comment — open to anyone, wallet or not |

</details>

<details>
<summary>Authenticated Endpoints</summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/challenge?address=0x...` | Signature challenge |
| `POST` | `/auth/verify` | Verify signature, get JWT |
| `GET` | `/auth/role` | Check user role |
| `POST` | `/jobs` | Submit job. `replace_job_id` redoes an existing card in place: same id, prompt/model/module/agent overwritten, result columns cleared back to `pending` (yours only, and only once it has finished) |
| `GET` | `/jobs` | List jobs |
| `GET` | `/jobs/{id}` | Job details |
| `DELETE` | `/jobs/{id}` | Delete job |
| `POST` | `/jobs/{id}/cancel` | Cancel job |
| `POST` | `/jobs/{id}/message` | Guide a running job mid-task (steering) |
| `GET` | `/jobs/{id}/stream` | SSE output stream |
| `POST` | `/files/write` | Write file |
| `POST` | `/modules/{name}/process` | Manage a module's pm2 processes (status/stop/start/restart) |
| `PUT` | `/modules/{name}/rename` | Rename a module and everything filed under its name: `{new_name, dry_run?, refs?: paths\|all\|none, restart?, reroute?}` (owner, x-sudo) |
| `POST` | `/kill` | Kill a process by PID or port |
| `POST` | `/modules/{name}/suggestions` | File a suggestion `{title, body?}` (anyone signed in) |
| `POST` | `/suggestions/{id}/vote` | Second it (toggle) |
| `POST` | `/suggestions/{id}/status` | Triage `open\|rejected\|done` (admin) |
| `POST` | `/suggestions/{id}/play` | Play it as an edit on your account (admin) |
| `DELETE` | `/suggestions/{id}` | Delete (admin, or author while unplayed) |

</details>

**Example:**

```bash
curl -X POST http://localhost:8894/jobs \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add error handling to api.py", "model": "sonnet", "work_dir": "/project"}'
```

---

## Auth & Permissions

| Mode | How | Details |
|---|---|---|
| **Local** | `BUILD_FORK_JOBS_LOCAL=1` (default) | No auth, all endpoints open |
| **Wallet** | MetaMask / SubWallet / BIP-39 / password key | EIP-191 challenge-verify, HMAC bearer token (24h) |

The first wallet to authenticate becomes the **owner**. Owners can edit any file and delete any module. Non-owners can only edit modules under `_outer/{their_address}/`. Read-only operations are always open.

### Whitelist (trusted editors)

Everything in the orbit belongs to the host owner. To let other people edit it, the owner whitelists their address. Whitelisted addresses are **trusted editors**: they get owner-level **edit** access — full host filesystem, unsandboxed jobs (run as root, not the workspace sandbox), and `core/`+`orbit/` writes — without needing per-write sudo signatures.

Whitelisting does **not** grant owner-only *powers*: managing the whitelist, `set_owner`, killing processes, process control, and destructive module ops (delete/rename/restore) stay restricted to the configured owner.

#### Reverting is the owner's last word

Editing and undoing are different powers here, and the second one is not delegable. Anyone the owner trusts to edit — a whitelisted editor, a QR-invite holder, a BlocTime holder, even an address whitelisted at **sudo** level that passes every other owner gate — can change a module. Only the owner can decide a change does not stand and roll the module back to any earlier version.

"The owner" for this one power means the owner's *own key*: the `owner` in `config.json` plus the co-owner wallets in `~/.mod/build-fork/owners.json` (`auth::is_root_owner`, `Build.is_root_owner`). Two locks, both required, on every revert of every module — this console included:

1. **identity** — the session belongs to the owner (an editor gets `403 {owner_only: true}`);
2. **possession** — a fresh wallet signature bound to `("restore", module)`, recovered to an owner address and replay-rejected server-side (`x-sudo`). A sudo signature from a delegate is refused here even though it passes everywhere else.

Every revert pins the state it replaces as a version of its own first, so a revert is itself revertible and the owner can always walk back out. `POST /modules/{name}/undo` is the one-click form: no CID to pick, it steps back through the version log (measured against the tree on disk, so an edit made outside the console is undone rather than reinforced).

```bash
# owner only — everyone else gets 403, signature or not
POST /modules/{name}/restore  {cid}                  # revert to any version
POST /modules/{name}/undo     {steps?: 1}            # revert to the state before the last change
GET  /modules/{name}/versions                        # → versions[].restorable + revert.can_revert
```

The console reflects this: the VERSIONS panel shows the ↺ controls only to a session that holds revert authority, and tells everyone else, in one line, that they may edit but not revert.

The whitelist lives off-tree in `~/.mod/build-fork/whitelist.json` (never committed — it's private auth state) and is read by both the Rust API and the Python SDK.

| Action | Owner | Editor (whitelisted) | Other |
|---|---|---|---|
| Edit `orbit/`+`core/`, run host jobs | ✅ | ✅ | edit only own `_outer/` workspace |
| Manage whitelist, `set_owner`, kill, delete/rename modules | ✅ | ❌ | ❌ |
| **Revert / undo a module to an earlier version** | ✅ (own key + signature) | ❌ | ❌ |

**Manage it** — owner-only:

```bash
m build-fork/add_editor 0xEditorAddress…      # grant edit access
m build-fork/editors                          # list whitelisted editors (public)
m build-fork/remove_editor 0xEditorAddress…   # revoke
```

REST (owner bearer token): `GET /whitelist` (public) · `POST /whitelist {address}` · `DELETE /whitelist/{address}`. The web UI's **Whitelist** panel (in the module detail / owner sidebar) wraps these with add/remove controls.

---

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (optional with Claude Max) |
| `OPENROUTER_API_KEY` | — | OpenRouter key for 200+ models |
| `BUILD_FORK_JOBS_LOCAL` | `1` | Set `0` to enable wallet auth |
| `MOD_ANCHOR` | `~/mod` | Base directory for module creation |

**Models:**

| Model | Speed | Use |
|---|---|---|
| `haiku` | Fast | Quick checks, linting, simple tasks |
| `sonnet` | Medium | General development (default) |
| `opus` | Slow | Complex architecture, major refactors |

---

## Architecture

```
build-fork/
├── build-fork/mod.py           Python SDK (34 methods, auto-starts API)
├── api/src/               Rust job engine
│   ├── api.rs               Axum REST + file browser + module ops
│   ├── jobs.rs              Job lifecycle, process mgmt, crash recovery
│   ├── auth.rs              EIP-191 wallet auth + HMAC tokens
│   └── main.rs              Tokio entry point
├── app/src/               Next.js 14 terminal UI
│   ├── app/page.tsx         Dashboard — jobs, files, modules, wallet
│   ├── app/globals.css      Theme system (11 palettes × 2 skins × font axis)
│   └── app/api/             Service proxy routes
├── config.json            Module metadata + endpoint schema
├── start.sh / stop.sh     Process management
├── docker-compose.yml     Container deployment
├── requirements.txt       Python deps
└── tests/                 Test suite
```

---

## Development

```bash
python -m pytest tests/                # run tests
cd api && cargo build --release        # build Rust server
cd app && npm run dev -- -p 8895       # frontend dev server
```

---

## Troubleshooting

<details>
<summary>Claude CLI not found</summary>

```bash
npm install -g @anthropic-ai/claude-code
```

</details>

<details>
<summary>API key issues</summary>

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

</details>

<details>
<summary>Job server not starting</summary>

```bash
lsof -i :8894              # check if port is in use
pkill -f build-fork-jobs         # kill existing process
./start.sh                  # restart
```

</details>

<details>
<summary>IPFS not available</summary>

```bash
brew install ipfs
ipfs init && ipfs daemon &
```

</details>

---

## Fork

```bash
m fork build-fork mybuild "Add GitLab integration"
```

Forks include full source (Python, Rust, Next.js), config, and tests. Lives in `~/mod/mod/orbit/<name>` and can be published with `m.publish('name')`.

---

<div align="center">

Part of the [Mod framework](https://github.com/modprotocol/mod).

</div>
