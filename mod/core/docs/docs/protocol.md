# The Mod Protocol

The mod protocol is a small set of conventions that make any directory of code a **module** — loadable from Python, callable from the CLI, servable as an HTTP API, routable through a gateway, and (optionally) registered on-chain. Everything else in the framework builds on these rules.

## 1. A module is a directory

A module lives in an orbit (`core`, `orbit`, `mods`, or `local`) and needs only an anchor file:

```
mod/orbit/<name>/
├── mod.py          # anchor: class Mod with plain-Python methods (agent.py or <name>.py also work)
├── config.json     # metadata: name, description, port (api), app_port, base_path, schema (IPFS CID)
├── README.md       # human docs (optional)
├── skill.md        # agent-facing docs (optional)
└── app/ src/ ...   # anything else the module needs
```

Public methods on the `Mod` class are the module's functions. No decorators, no registration step.

```python
import mod as m
m.mod('storage')()          # load a module
m.fn('storage/put')(k, v)   # call one function
```

```bash
m storage/put mykey '{"hello":"world"}'   # same call from the CLI
```

## 2. Serving: functions become endpoints

`m serve <name>` runs the module behind the core Flask server (managed by PM2). Every public function becomes a POST endpoint:

| Route | Meaning |
|-------|---------|
| `POST /{fn}` | call function `fn` on the served module |
| `POST /mod/{name}/{fn}` | call `fn` on module `name` (namespaced form) |
| `POST /mod/{name}` | **null call** — no function given, returns the module's `info` (name, functions, schema) |

Arguments are JSON in the request body; the response is `{"result": ...}`. The null-call rule means any module can be discovered by POSTing to its bare URL.

Modules with their own native servers (Rust, Node, Next.js) follow the same public shape: an API on `config.json`'s `port` and an app on `app_port`.

## 3. Gateway: one URL rule

Every deployment routes modules the same way:

| Public URL | Goes to | Prefix |
|------------|---------|--------|
| `https://<host>/{mod}` | module **app** (`app_port`) | kept (apps set `basePath /{mod}`) |
| `https://<host>/api/{mod}` | module **API** (`port`) | stripped (`/api/{mod}/x` → `/x`) |

Three interchangeable implementations enforce this rule:

- **Caddy** — the production gateway on modc2.com. Routes are auto-generated from each module's `config.json` (`route: true` + a live port) by the `caddy` orbit module (`m caddy/apply`).
- **routy** — a standalone Rust gateway (`mod/orbit/routy`) with the same `/{mod}` / `/api/{mod}` rule. Its own control endpoints live under `/_api/*` (register, sync, stats) — `/_api` is the gateway's admin API, not a module route.
- **Next.js middleware** — `mod/core/app/middleware.ts` applies the identical rewrite inside the core frontend.

So for any module: **app at `/{mod}`, API at `/api/{mod}`, discovery via a null POST.**

## 4. Identity and auth

- **Keys** — multi-chain identity (ECDSA, sr25519, ed25519, Solana), stored encrypted under `~/.mod/key/`. See [Keys](keys).
- **Shared auth** — `m.mod('auth')` is the one identity layer modules share: it issues signed tokens and verifies/recovers the signer address. Servers expose it as `server.auth`.
- **Owners** — a module's `config.json` may name an owner address; owner-gated functions verify the caller's recovered address against it.
- **Private state stays off-chain** — whitelists, ACLs, and secrets live under `~/.mod/{module}/`, never in the committed `config.json`.

## 5. Storage and registration

- **Local** — everything persists as JSON under `~/.mod/`, optionally AES-encrypted. See [Storage](storage).
- **IPFS** — module schemas and shared objects are content-addressed; a module's `schema` field in `config.json` is an IPFS CID.
- **On-chain** — the BlocTime `Registry` contract (Base) maps a module name + IPFS metadata to an owner address, making a module's existence verifiable and transferable. See [Contracts](contracts).

## Minimal example

```bash
# 1. create
mkdir -p mod/orbit/hello && cat > mod/orbit/hello/mod.py <<'EOF'
class Mod:
    description = "hello module"
    def greet(self, name="world"):
        return f"hello {name}"
EOF

# 2. call
m hello/greet name=mod        # → "hello mod"

# 3. serve + discover
m serve hello
curl -X POST localhost:<port>/mod/hello          # null call → module info
curl -X POST localhost:<port>/greet -d '{"name":"mod"}'
```

That is the whole protocol: a directory, a class, one URL rule, and a null call that tells you what a module can do.
