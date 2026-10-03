# chain

Hub for the mod protocol's modular smart-contract fleet on Base. Eleven
contract modules — `token`, `oracle`, `registry`, `perms`, `tokengate`,
`bloctime`, `treasury`, `market`, `debit`, `safe`, `bridge` — each owning one
concern, composed into a single protocol and operated from one console.

- **Web console** (`src/app`, port **8801**): Hub dashboard with live
  network/gas/deployer stats, Interact (call any function of any deployed
  contract), Contracts (ABIs, source, IPFS CIDs), Protocol flows (mint,
  register, stake, pool), Control (verify, deploy scripts), Owner console
  (owner-only setters, Safe batch export, owner-only host readout), and Docs
  (in-app tutorial, guide, API reference, agent usage).
- **HTTP API** (`src/api/api.py`, port **8800**): FastAPI mirror of the common
  operations — see `/docs` on the running server.
- **Python orchestrator** (`src/mod.py`): the full-power surface — deploys,
  forks, staking, registry, pool, yield, admin.
- **Contract builder** (`src/build`): write Solidity in the browser, compile it
  here with solc 0.8.26, then deploy it from your own wallet. Starter templates
  in `src/build/templates/`; upload your own project (hardhat *or* foundry
  layouts — `lib/openzeppelin-contracts/…` imports are remapped onto the
  installed packages) or fork one out of the shared gallery — every fleet module ships in it,
  tests included, plus `protocol` (all of them in one project); the console for it is the app's `/chain` page, and it works on a phone.
- **Wallets**: MetaMask (automatic chain switch/add on send) or a
  browser-local keypair — reads never need a wallet.
- **Agent** (`src/agent/mod.py`, the console's AGENT tab): hand a project to
  Claude Code. The run goes through the `orbit/agent` module — its shipped
  `chain-mod` agent hands the run to this module's harness runner (`chainmod`),
  the same road the build console's runs take, so the agent module's owner
  gate, task ledger and console all see it. Claude works in a sandboxed Hardhat
  copy of the project (edits accepted there and nowhere else, a shell that runs
  `npx hardhat test|compile` and nothing else) and its edits are written back
  into the project when it finishes.

## Quickstart

```bash
# API
src/api/start.sh                    # :8800

# App (DEV=0 builds and serves prod)
DEV=0 src/app/start.sh              # :8801

# Deploy the fleet to Base Sepolia
m chain/deploy network=testnet
```

Deploy order (parallel within a group, sequential across):
`[token oracle registry perms] → [tokengate bloctime] → [treasury] → [market] → [debit]`

Per-network deployments (addresses + ABI/source CIDs) live in `config.json`.
Contract sources are under `src/contracts/<module>/`, each with its own README.
Builder projects and wallet-signed builds are per-user state, so they live
off-tree in `~/.mod/chain/build/`.

## The agent

```bash
curl localhost:8800/agent/status              # CLI + agent module reachable? who may run it?
curl -N -X POST localhost:8800/agent/run -H 'Content-Type: application/json' \
  -d '{"key": "<wallet-signed token>", "query": "add a test for transfer()", "project": "token"}'
curl 'localhost:8800/agent/runs?address=0x…'  # past runs, newest first
```

`/agent/run` is a bridge: it verifies the token, names the project, and
forwards to `orbit/agent`'s `POST /run/stream` as `agent_type: chain-mod` with
`harness_args: {project, address, network, model}`, streaming the agent
module's SSE events (`step` / `done` / `error`) straight back. Harness runs
execute on the host's own Claude account, so the agent module keeps them
**owner-only** — the tab says so up front, and a guest's run comes back as a
403 event. The runner itself is callable from Python:

```python
a = m.mod('chain.agent')()
a.harness()                                   # {name: 'chainmod', available, version, …}
a.run('write tests for every contract', project='token', address='0x…', on_step=print)
```

Steps are the fleet's step dicts (`workspace` → `read`/`edit`/`write`/`bash`… →
`project` → `finish`), the finish step carrying the answer, the changed files,
turns and cost. Workspaces live under `~/.mod/chain/build/agent/run/<address>/<project>/`,
the run ledger at `~/.mod/chain/build/agent/runs.json`. Env: `CHAIN_AGENT_MODEL`
(default `sonnet`), `CHAIN_AGENT_CONCURRENCY` (2), `CLAUDE_BIN`, `AGENT_API_URL`
(the API's link to orbit/agent, default `http://localhost:50117`).

## ABIs live in the store

Every ABI this module knows about is written into the **store module** as a
content-addressed object owned by the address that deployed it — the fleet's
own contracts under `chain/abi/<network>/<name>.json`, and every wallet-signed
build alongside them. What's recorded anywhere else is just the CID, so an ABI
is fetchable from any project instead of being copy-pasted between them.

```
GET  /build/abi/{cid}          → { cid, abi }        # parsed, ready for ethers
POST /build/abi                  {address, name, network, abi, source?} → { cid }
GET  /build/abis?address=0x…   → ABIs that address has stored
GET  /cid/{cid}                → raw stored content (ABI JSON or .sol source)
```

Deploying from the console does the POST for you and shows the CID; INTERACT
takes an address + a CID and drives the contract from there. Contracts deployed
somewhere else are just as welcome — POST the ABI and they get a CID too.

## Host readout (owner-only)

The Owner console shows the machine the API runs on — per-core CPU, memory,
swap, disk, per-interface network traffic, socket census and the busiest
processes (`GET /system/stats`, polled every 4s). It is the one private part of
the API: raw cmdlines can carry secrets and traffic shape is operational intel.

Access is proven by a wallet signature, not by a password:

```
GET  /system/access?address=0x…   → { is_owner, authed }   # console hides the panel unless is_owner
GET  /system/challenge?address=0x… → single-use message     # 403 for non-owners
POST /system/login {address, signature, nonce} → { token }  # 12h HMAC token
GET  /system/stats  (Authorization: Bearer <token>)
```

The ACL is off-tree in `~/.mod/chain/owners.json` (0600), seeded on first use
from the non-local deployers in `config.json` — edit it to add wallets, or set
`CHAIN_OWNERS=0x…,0x…` to override it entirely. Tokens are signed with
`~/.mod/chain/server.secret`, so a restart doesn't sign everyone out.

**For agents:** read [`skill.md`](./skill.md) — capabilities, functions,
endpoints and examples in one sheet. In-app docs at `/docs` on the console.
