# prefi

**Trade, feed the treasury, earn PREFI, lock it for a weekly cut.**

A trading protocol where profit is not yours to keep — you trade an asset
through the protocol, and when you close in profit that profit goes to the
treasury and you are minted **1 PREFI per $1** captured. PREFI locked for a
duration earns *staketime* (`amount × seconds`), and each epoch the treasury is
distributed to lockers in proportion to their staketime. Trading is the mint;
locking is the claim.

```
trade ──profit──► treasury ──weekly epoch──► lockers (pro-rata by staketime)
  │
  └──1 PREFI per $1 profit──► you ──lock──► staketime ──► claim
```

The ledger (markets, positions, stakes, treasury epochs) is kept off-tree in
`~/.mod/prefi/` — override with `PREFI_DIR`. Prices come from CoinGecko, cached
5 minutes across every caller. The Solidity contracts in `src/contracts/` are
the on-chain version of the same rules; nothing is deployed yet, so the ledger
above is the source of truth.

---

## Run it

```bash
m prefi/serve            # api :50410 + app :50411 (production build)
m prefi/serve dev=True   # uvicorn --reload + next dev
m prefi/seed             # list the default Base markets (WETH, cbBTC, AERO)
m prefi/health
```

Under pm2, the way the fleet runs it:

```bash
pm2 start /usr/bin/python3 --name prefi.api -- -m uvicorn api:app \
    --host 0.0.0.0 --port 50410 --app-dir <module>/src/api
pm2 start /usr/bin/npx --name prefi.app --cwd <module>/src/app -- next start -p 50411
```

The app needs a build before `next start`: `cd src/app && npm install && npx next build`.

| | local | gateway |
|---|---|---|
| app | http://localhost:50411/prefi | https://modc2.com/prefi |
| api | http://localhost:50410 | https://modc2.com/api/prefi |

The app is served under `basePath: /prefi` and talks to the API same-origin at
`/prefi/api/*` (a Next rewrite), so one gateway route covers the whole module.

## API

| route | what |
|---|---|
| `GET /health` `GET /status` | liveness, protocol totals |
| `GET /markets` · `POST /markets/add` · `POST /markets/seed` | tradeable assets |
| `POST /position/open` · `POST /position/close` · `GET /positions/{addr}` | trading |
| `POST /stake/lock` · `/stake/extend` · `/stake/unlock` · `GET /stakes/{addr}` | staketime |
| `GET /treasury` · `/treasury/history` · `POST /treasury/distribute` · `/treasury/claim` | epochs |
| `GET /leaderboard` · `GET /portfolio/{addr}` · `GET /prices` | read models |

`python3 -c "from mod import Mod; Mod({}).test()"` from `src/` runs the 41-case
integration test against a temp ledger.

## Wallet

Browser wallets (injected / Coinbase) work out of the box. WalletConnect is only
wired in when `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID` is set — a placeholder id
makes the relay reject the session and takes the whole wallet list down with it.

`NEXT_PUBLIC_UNISWAP_API` is optional: point it at a running
[`uniswap`](../uniswap) module to fill the live pool-price panel.

## Known gaps

- **Docker path is stale.** `Dockerfile` / `docker-entrypoint.sh` build and boot
  the Rust crate in `src/api/` (`prefi-server`), which is an older
  prediction-market API — it does not serve the endpoints this app calls. The
  Python FastAPI in `src/api/api.py` is the real backend. Docker is unbuilt and
  untested; use `m prefi/serve` or pm2.
- Contracts are undeployed — `config.json.contracts` is empty and the ledger is
  server-side only.
