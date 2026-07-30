<div align="center">

# Build Mod

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
│  build/mod.py   api/src/        app/src/  │
└──────────────────────────────────────────┘
```

</div>

## Quick Start

```bash
git clone https://github.com/modprotocol/mod.git
cd mod/mod/orbit/build

pip install -r requirements.txt
./start.sh
```

API on **:8870**, UI on **:8871**. Local mode by default — no wallet needed.

<details>
<summary>Docker</summary>

```bash
docker compose up -d
```

</details>

<details>
<summary>Install from IPFS</summary>

```bash
ipfs get <CID> -o build && cd build
pip install -r requirements.txt
```

Get the latest CID from the on-chain registry: `m.get_cid('build')`

</details>

---

## Python SDK

```python
from build import Mod

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

### Versioning

Semantic versioning backed by IPFS:

```python
c.snapshot("v1.2.0", description="Add auth endpoints")
c.changelog()
c.get_version("v1.2.0")
c.restore_version("v1.2.0")
```

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

Bodies live in localfs; the catalog index lives off-tree in `~/.mod/dev/prompts.json`.

### Modules

```python
c.create_module("mymod", prompt="Build a web scraper module")
c.edit_module("mymod", prompt="Add rate limiting")
c.modules()
```

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

Retro terminal dashboard at **localhost:8871**.

| Feature | Details |
|---|---|
| Job submission | Live SSE streaming with output tail |
| File browser | Syntax highlighting for 20+ languages |
| Search | `Cmd+P` file search, `Cmd+Shift+F` content grep |
| Wallet auth | MetaMask, SubWallet, BIP-39, password-derived key |
| Themes | dark, light, matrix, cyberpunk, amber, ocean |
| Extras | Image paste, ASCII boot screen, CRT aesthetic |

---

## REST API

Rust server (Axum + SQLite) on port `8870`.

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

</details>

<details>
<summary>Authenticated Endpoints</summary>

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/challenge?address=0x...` | Signature challenge |
| `POST` | `/auth/verify` | Verify signature, get JWT |
| `GET` | `/auth/role` | Check user role |
| `POST` | `/jobs` | Submit job |
| `GET` | `/jobs` | List jobs |
| `GET` | `/jobs/{id}` | Job details |
| `DELETE` | `/jobs/{id}` | Delete job |
| `POST` | `/jobs/{id}/cancel` | Cancel job |
| `POST` | `/jobs/{id}/message` | Guide a running job mid-task (steering) |
| `GET` | `/jobs/{id}/stream` | SSE output stream |
| `POST` | `/files/write` | Write file |
| `POST` | `/modules/{name}/process` | Manage a module's pm2 processes (status/stop/start/restart) |
| `POST` | `/kill` | Kill a process by PID or port |

</details>

**Example:**

```bash
curl -X POST http://localhost:8870/jobs \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add error handling to api.py", "model": "sonnet", "work_dir": "/project"}'
```

---

## Auth & Permissions

| Mode | How | Details |
|---|---|---|
| **Local** | `DEV_JOBS_LOCAL=1` (default) | No auth, all endpoints open |
| **Wallet** | MetaMask / SubWallet / BIP-39 / password key | EIP-191 challenge-verify, HMAC bearer token (24h) |

The first wallet to authenticate becomes the **owner**. Owners can edit any file and delete any module. Non-owners can only edit modules under `_outer/{their_address}/`. Read-only operations are always open.

### Whitelist (trusted editors)

Everything in the orbit belongs to the host owner. To let other people edit it, the owner whitelists their address. Whitelisted addresses are **trusted editors**: they get owner-level **edit** access — full host filesystem, unsandboxed jobs (run as root, not the workspace sandbox), and `core/`+`orbit/` writes — without needing per-write sudo signatures.

Whitelisting does **not** grant owner-only *powers*: managing the whitelist, `set_owner`, killing processes, process control, and destructive module ops (delete/rename/restore) stay restricted to the configured owner.

The whitelist lives off-tree in `~/.mod/dev/whitelist.json` (never committed — it's private auth state) and is read by both the Rust API and the Python SDK.

| Action | Owner | Editor (whitelisted) | Other |
|---|---|---|---|
| Edit `orbit/`+`core/`, run host jobs | ✅ | ✅ | edit only own `_outer/` workspace |
| Manage whitelist, `set_owner`, kill, delete/rename modules | ✅ | ❌ | ❌ |

**Manage it** — owner-only:

```bash
m build/add_editor 0xEditorAddress…      # grant edit access
m build/editors                          # list whitelisted editors (public)
m build/remove_editor 0xEditorAddress…   # revoke
```

REST (owner bearer token): `GET /whitelist` (public) · `POST /whitelist {address}` · `DELETE /whitelist/{address}`. The web UI's **Whitelist** panel (in the module detail / owner sidebar) wraps these with add/remove controls.

---

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (optional with Claude Max) |
| `OPENROUTER_API_KEY` | — | OpenRouter key for 200+ models |
| `DEV_JOBS_LOCAL` | `1` | Set `0` to enable wallet auth |
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
build/
├── build/mod.py           Python SDK (34 methods, auto-starts API)
├── api/src/               Rust job engine
│   ├── api.rs               Axum REST + file browser + module ops
│   ├── jobs.rs              Job lifecycle, process mgmt, crash recovery
│   ├── auth.rs              EIP-191 wallet auth + HMAC tokens
│   └── main.rs              Tokio entry point
├── app/src/               Next.js 14 terminal UI
│   ├── app/page.tsx         Dashboard — jobs, files, modules, wallet
│   ├── app/globals.css      Theme system (6 themes)
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
cd app && npm run dev -- -p 8871       # frontend dev server
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
lsof -i :8870              # check if port is in use
pkill -f dev-jobs         # kill existing process
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
m fork build mybuild "Add GitLab integration"
```

Forks include full source (Python, Rust, Next.js), config, and tests. Lives in `~/mod/mod/orbit/<name>` and can be published with `m.publish('name')`.

---

<div align="center">

Part of the [Mod framework](https://github.com/modprotocol/mod).

</div>
