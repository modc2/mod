# Orbit Modules

The orbit ecosystem contains 200+ pluggable modules. Each module lives in `mod/orbit/` and is automatically discovered by the framework. No registration needed --- just drop a folder with an anchor file and it's live.

## Module Categories

### AI & Agents

| Module | Description |
|--------|------------|
| `agent` | Agentic AI workflows — structured tool execution with memory and planning |
| `claude` | Claude Code CLI integration — code analysis, generation, refactoring, background jobs |
| `model` | Model management and routing |
| `model.openrouter` | OpenRouter API proxy for multi-model access |
| `skill` | Skill-based task execution |
| `search` | LLM-powered module search — find the best module for any task (free tier) |
| `arena` | AI arena (stub) |

### Storage & Data

| Module | Description |
|--------|------------|
| `ipfs` | IPFS client with auto-managed Kubo daemon — `put()`, `get()`, pinning |
| `filecoin` | Filecoin storage deals |
| `lighthouse` | Lighthouse (Filecoin/IPFS) storage provider |
| `cache` | Function-level caching (`fncache`) |
| `localfs` | Local filesystem storage |

### Blockchain & DeFi

| Module | Description |
|--------|------------|
| `bridge` | Substrate/Solana → EVM identity bridge — snapshot claims, on-chain commitments |
| `uniswap` | Uniswap V3 multichain strategy engine (Rust backend) — DCA, limit orders, momentum, copy trading |
| `safe` | Gnosis Safe multisig integration |
| `eth` | Ethereum utilities |
| `near` | NEAR Protocol integration |
| `solana` | Solana integration |
| `polycopy` | Polygon copy trading |
| `raydium` | Raydium DEX (Solana) |

### Dev Tools

| Module | Description |
|--------|------------|
| `git` | Git operations |
| `gitagent` | AI-powered git workflows |
| `pytest` | Python test runner |
| `conda` | Conda environment management |
| `replit` | Replit integration |
| `docker` | Docker management |

### Infrastructure

| Module | Description |
|--------|------------|
| `web` | Web utilities |
| `mcp` | Model Context Protocol |
| `modal` | Modal.com serverless |
| `proton` | Proton integration |
| `protonmail` | ProtonMail integration |

### And Many More

The full list of 200+ modules can be discovered with:

```bash
m mods
```

---

## Key Modules In Depth

### agent — Agentic AI Workflows

The agent module runs multi-step AI workflows with structured tool execution.

```python
agent = m.mod('agent')()

result = agent.forward(
    query="Build a REST API for user management",
    path="/path/to/project",
    tools=['cmd', 'git', 'deploy'],
    steps=10
)
```

**How it works**:
1. `init_memory()` — sets up goal, output format, available tools, and context
2. Runs N steps, each step:
   - Sends context to LLM (via OpenRouter, default `anthropic/claude-opus-4.5`)
   - Parses structured plan from response (`<PLAN><STEP>JSON</STEP></PLAN>`)
   - Executes tool calls from the plan
   - Appends results to context
3. `finish` tool terminates the loop

**Tool schema**: Tools are loaded dynamically via `m.schema(tool_name)` — any module can be a tool.

**Memory**: Uses `agent.memory` module for persistent context across sessions.

**Philosophy**: Aggressive, one-shot when possible. Minimal reads, maximum action.

---

### claude — Claude Code Integration

Programmatic access to Claude Code for automated code operations.

```python
claude = m.mod('claude')()

# Ask Claude
response = claude.ask("Explain this code", model="anthropic/claude-opus-4")

# Analyze code
analysis = claude.analyze_code("/path/to/file.py")

# Generate code
code = claude.generate_code("REST API with FastAPI")

# Refactor
claude.refactor("/path/to/messy.py")

# Debug
claude.debug("/path/to/broken.py")

# Edit a file with instructions
claude.edit_file("/path/to/file.py", instructions="Add error handling")
```

**Background jobs** (via Rust server at `localhost:8820`):
```python
job_id = claude.submit("Long running task")
claude.jobs()          # List all jobs
claude.job(job_id)     # Check status
claude.cancel(job_id)  # Cancel job
claude.logs(job_id)    # View logs
```

**CLI integration**: Wraps the `claude` binary, auto-installs via Homebrew if missing.

---

### ipfs — IPFS Storage

Full IPFS client with automatic daemon management.

```python
ipfs = m.mod('ipfs')()

# Store and retrieve JSON
cid = ipfs.put({'key': 'value'})
data = ipfs.get(cid)

# File operations
cid = ipfs.add_file('/path/to/file.txt')
ipfs.get_file(cid)

# Raw content
content = ipfs.cat(cid)

# Pinning
ipfs.pin_add(cid)
ipfs.pin_rm(cid)
pinned = ipfs.pins()

# Node management
ipfs.start_node()
ipfs.stop_node()
ipfs.restart_node()
status = ipfs.node_status()

# CID validation
ipfs.valid_cid('QmXyz...')  # True/False
```

**Auto-management**:
- Installs Kubo v0.40.1 if missing (macOS, Linux, Windows)
- Starts daemon automatically on first use
- Health checks every 30 seconds
- Auto-reconnects on failure
- Connects on `127.0.0.1:5001` (fallback `0.0.0.0:5001`)

---

### bridge — Identity Bridge

Substrate/Solana to EVM identity bridge with snapshot-based token claims and on-chain commitments. Served via the core server (`m.serve('bridge')`) — no custom API needed.

```python
bridge = m.mod('bridge')()

# Check snapshot
bridge.in_snapshot(address='5Substrate...')

# Commit identity (sign "commit {evmAddr}" with source wallet)
bridge.commit(source_address='5Sub...', evm_address='0x...', signature='0x...', source_type='substrate')

# Claim tokens
bridge.claim(auth_token='token', recipient='0xEVM...', address='5Sub...')

# Check status
bridge.has_claimed(address='5Sub...')
bridge.unclaimed(address='5Sub...')
bridge.status()

# Deploy contracts
bridge.deploy(network='testnet')
```

**Architecture**: Balances from snapshots (`snapshot/*.json`), claims tracked in `~/.bridge/claims.json`, commitments in `~/.bridge/commitments.json`. On-chain: BridgeableToken (ERC20 + Ownable) on Base Sepolia.

**Serve**: `m serve bridge` (API only) or `m serve bridge.app` (API + Next.js frontend).

---

### uniswap — DEX Strategy Engine

Multichain Uniswap V3 integration with a Rust backend for strategy execution.

```python
uni = m.mod('uniswap')()

# Get a swap quote
quote = uni.quote(
    chain_id=8453,
    token_in='0xUSDC...',
    token_out='0xWETH...',
    amount_in=1000000
)

# Build a swap transaction
tx = uni.build_swap(...)

# Check pool state
state = uni.pool_state(chain_id=8453, pool='0xPool...')

# Check balance
balance = uni.balance(chain_id=8453, token='0xUSDC...', wallet='0x...')
```

**Strategy types**:
```python
# DCA (Dollar Cost Averaging)
uni.create_strategy(type='dca', interval=3600, amount=100, ...)

# Limit Order
uni.create_strategy(type='limit', target_price=2000, ...)

# Momentum (SMA crossover)
uni.create_strategy(type='momentum', short_window=10, long_window=30, ...)

# Cross-chain Arbitrage
uni.create_strategy(type='cross_chain_arb', chains=[8453, 137], ...)

# Portfolio Rebalancing
uni.create_strategy(type='rebalance', allocations={...}, ...)

# Copy Trading
uni.add_to_watchlist(wallet='0xTrader...')
uni.copy_trade(wallet='0xTrader...', max_trade_size=1000, ...)
```

**Strategy management**:
```python
uni.list_strategies()
uni.get_strategy(strategy_id)
uni.pause_strategy(strategy_id)
uni.resume_strategy(strategy_id)
uni.delete_strategy(strategy_id)
uni.strategy_history(strategy_id)
```

**Supported chains**: Base (8453), Polygon (137)
**Backend**: Rust HTTP server at `localhost:8080`

---

## Creating a New Module

1. Create the directory:
```bash
mkdir -p mod/orbit/mymod/mymod
```

2. Create the anchor file (`mod.py`):
```python
# mod/orbit/mymod/mymod/mod.py
class Mod:
    description = "What this module does"

    def forward(self, **kwargs):
        return {"status": "ok"}
```

3. Use it immediately:
```bash
m mymod
m mymod/forward
m info mymod
```

No registration, no config — it just works.

## Publishing Your Module to the Registry

The `dev` orbit module bundles your module's source tree, hashes it to a single CID via localfs, and registers `(name, cid)` on the mod protocol registry contract on Base Sepolia. Anyone running a mod node can then fetch your module by name.

### 1. Author your module

Drop a directory under `mod/orbit/mymod/` with at minimum:

| File | Purpose |
|------|---------|
| `src/mod.py` (or `mod.py`) | Anchor class — must define `class Mod` |
| `config.json` | name, version, port (optional), owner (your wallet) |
| `skill.md` | Capabilities, usage, env vars. Generated by `/add-skill <mod>` |

Example `config.json`:

```json
{
    "name": "mymod",
    "version": "0.1.0",
    "description": "what mymod does",
    "owner": "0xYourWallet",
    "fns": ["forward", "info"]
}
```

### 2. Bundle the tree to one CID

`dev.bundle()` walks the module tree, calls `m.cid(mod)` per sub-module (which pushes content to localfs), and writes a manifest CID covering the whole bundle.

```bash
m dev/bundle
```

Returns:

```json
{
    "cid": "QmRoot...",
    "modules": {
        "mymod": "Qm..."
    }
}
```

The root CID and per-module CIDs are persisted to `dev/config.json:schema` and `dev/config.json:bundle_modules`.

### 3. Register on chain

`dev.register(name)` writes `(name, cid)` to the registry contract on Base Sepolia. Owner-only — your wallet must match `config.json:owner`.

```bash
m dev/register mymod
# or register everything that bundle() produced:
m dev/register_all
```

Verify:

```python
import mod as m
print(m.mod('chain')().mod_exists('mymod'))  # → True
```

### 4. API keys (encrypted upload)

If your module needs a secret (e.g. an OpenAI key), do NOT bake it into the bundle. Instead, after deploy, users supply their own key via the dev UI:

1. User loads `http://your-node/dev`
2. Clicks **set api key**, signs the challenge `mod-key-derivation-v1` with their wallet
3. Browser derives an AES-256-GCM key from `SHA-256(signature)`
4. Encrypts the API key locally, POSTs ciphertext + IV to `/api/dev/key/{provider}`
5. Server stores per-wallet ciphertext keyed by the signer's address (no plaintext ever crosses the wire)
6. On every request, the server recovers the wallet from the signature header, looks up the ciphertext, decrypts in-process, then forwards to the upstream

The signature itself is the access credential — possession of the wallet → possession of the AES key. No password, no token rotation.

### 5. Owner-only writes (defense in depth)

The Rust API (`src/api/src/api.rs`) gates write endpoints behind `require_owner(&headers)`. Any handler that touches `~/mod/core/` or `~/mod/orbit/` must call it:

```rust
async fn my_write_handler(headers: HeaderMap, Json(body): Json<Body>) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    // ... your write logic ...
}
```

A non-owner caller will receive `403 Owner-only`.

### 6. Single-port deployment

Each orbit module ships with:

- `Dockerfile` — multi-stage build (Rust API + Next.js app + runtime)
- `docker-compose.yml` — joins the `modnet` external Docker network
- `docker-entrypoint.sh` — boots API + app inside the container

The core Caddy gateway (`core/server/caddy/Caddyfile`) routes `/api/<mod>` and `/<mod>` to your container by name. So `claude`, `codex`, `dev`, and your `mymod` all live behind a single public port (80/443 on `modc2.com`, or `:3000` locally).

Add your module to the Caddyfile snippet:

```
@mymod_api path /api/mymod /api/mymod/*
handle @mymod_api {
    uri strip_prefix /api/mymod
    reverse_proxy mymod:<api_port>
}
@mymod_app path /mymod /mymod/*
handle @mymod_app {
    reverse_proxy mymod:<app_port>
}
```

Then `docker compose up -d` in your module's directory joins it to `modnet` and Caddy starts routing.

### Reference

- Registry contract: see [contracts.md](contracts.md)
- IPFS / localfs content storage: see [storage.md](storage.md)
- Encryption key derivation: see [keys.md](keys.md)
- API conventions: see [api.md](api.md)
