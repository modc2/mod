# chain

Hub for the mod protocol's modular smart-contract fleet on Base. Eleven
contract modules — `token`, `oracle`, `registry`, `perms`, `tokengate`,
`bloctime`, `treasury`, `market`, `debit`, `safe`, `bridge` — each owning one
concern, composed into a single protocol and operated from one console.

- **Web console** (`src/app`, port **8801**): Hub dashboard with live
  network/gas/deployer stats, Interact (call any function of any deployed
  contract), Contracts (ABIs, source, IPFS CIDs), Protocol flows (mint,
  register, stake, pool), Control (verify, deploy scripts), Owner console
  (owner-only setters, Safe batch export), and Docs (in-app tutorial, guide,
  API reference, agent usage).
- **HTTP API** (`src/api/api.py`, port **8800**): FastAPI mirror of the common
  operations — see `/docs` on the running server.
- **Python orchestrator** (`src/mod.py`): the full-power surface — deploys,
  forks, staking, registry, pool, yield, admin.
- **Contract builder** (`src/build`): write Solidity in the browser, compile it
  here with solc 0.8.26 (`@openzeppelin/contracts` imports resolve), then deploy
  it from your own wallet. Starter templates in `src/build/templates/`; the
  console for it is the app's `/chain` page.
- **Wallets**: MetaMask (automatic chain switch/add on send) or a
  browser-local keypair — reads never need a wallet.

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

Per-network deployments (addresses + pinned ABI/source CIDs) live in
`config.json`. Contract sources are under `src/contracts/<module>/`, each with
its own README. Builder drafts and wallet-signed builds are per-user state, so
they live off-tree in `~/.mod/chain/build/`.

**For agents:** read [`skill.md`](./skill.md) — capabilities, functions,
endpoints and examples in one sheet. In-app docs at `/docs` on the console.
