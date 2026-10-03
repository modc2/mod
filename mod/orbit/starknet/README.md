# starknet

Starknet as one mod — the chain behind an API and an app, local first,
zero dependencies.

- **Reads everything**: blocks, transactions, receipts, account balances
  (ETH / STRK / USDC / any ERC-20 with Uint256 decoding), nonces, class
  hashes, contract storage, and arbitrary `starknet_call` reads against any
  contract.
- **Self-sustaining**: python stdlib only. keccak-256 and the
  `starknet_keccak` entry-point hash are implemented in-tree, so selectors
  for any entrypoint name are computed locally — no starknet.py, no
  cairo-lang, no npm.
- **Local first**: set `STARKNET_RPC` to your own node and no third party is
  ever contacted. Without it, a failover pool of free public endpoints
  (publicnode, zan.top — both keyless) answers, and the last endpoint that
  worked is preferred on the next call.
- **One port** (51020) serves the REST API, and the console app at
  `/starknet/`.

## Layout

```
chain.py       the Starknet client as plain importable functions (reusable alone)
api.py         REST API + console server (http.server, one port)
console.html   the app — status, account lookup, contract calls
mod.py         module anchor: every fn callable through the mod protocol
config.json    ports, endpoints, fns, env
```

## Run

```bash
python3 mod.py serve          # foreground
# or through the protocol: Mod().serve(background=True)
```

- API: `http://localhost:51020/`
- App: `http://localhost:51020/starknet/`

The server also answers with the `/starknet` prefix intact, so it works
unchanged behind the gateway (`/{mod}` → app, prefix kept).

## API

| endpoint | what |
| --- | --- |
| `GET /status` | network, chain id, head block, active RPC |
| `GET /block?id=latest\|<n>\|<hash>&full=1` | block (full=1 includes txs) |
| `GET /tx?hash=` · `GET /receipt?hash=` | transaction / receipt |
| `GET /account?address=` | ETH+STRK balances, nonce, class hash in one view |
| `GET /balance?address=&token=eth\|strk\|usdc\|0x..` | any ERC-20 balance |
| `GET /nonce?address=` · `GET /class_hash?address=` | account state |
| `GET /storage?address=&key=` | raw contract storage |
| `GET /selector?name=balanceOf` | starknet_keccak of an entrypoint (offline) |
| `GET\|POST /call` | read any contract: `{contract, entrypoint\|selector, calldata}` |
| `POST /rpc` | raw JSON-RPC escape hatch: `{method, params}` |

Every read takes `?network=mainnet|sepolia`. Errors return HTTP 400 with a
JSON body (never 5xx — proxies strip those bodies).

```bash
curl -s localhost:51020/status
curl -s 'localhost:51020/balance?address=0x0498...&token=strk'
curl -s localhost:51020/call -d '{"contract":"0x049d...","entrypoint":"balanceOf","calldata":["0x0498..."]}'
```

## Env

| var | meaning |
| --- | --- |
| `PORT` | API + app port (default 51020) |
| `STARKNET_NETWORK` | default network (`mainnet`) |
| `STARKNET_RPC` | your own node — always tried first |
| `STARKNET_TIMEOUT` | per-endpoint timeout, seconds (default 15) |

## Test

```bash
python3 chain.py     # offline vectors (keccak, selector) + live status
python3 mod.py       # same, plus local API health if serving
```

The offline vectors pin `keccak256(b'') = c5d24601…` and
`selector('balanceOf') = 0x2e4263af…` — the live chain agrees (verified with
a real `starknet_call` on mainnet).

## v0.1 scope

Reads only. No keys are held anywhere, so there is nothing to leak. Writes
(account deployment, invoke transactions, a keystore under
`~/.mod/starknet/`) are a later version, following the near module's shape:
testnet by default, explicit confirm for mainnet, secrets never in the repo.
