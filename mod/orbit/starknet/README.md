# starknet

Starknet as one mod — the chain and its STRK20 privacy pool behind an API,
an app and an MCP server, local first, zero dependencies.

- **Any contract, by its own ABI**: Starknet stores every class's ABI on
  chain, so `abi.py` fetches it (cached by class hash) and reads any
  function by name — typed JSON args in, structs / enums / u256 / arrays /
  ByteArray / events decoded out.
- **STRK20 privacy pool** ([docs](https://strk20.starknet.io/docs)): the
  mainnet pool `0x040337b1…e812a` — live parameters, its public event tape,
  per-address registration and public edges, notes and nullifiers, and every
  `privacy_invoke` anonymizer contract it has called (with each one's
  signature, checked against the `Span<OpenNoteDeposit>` contract).
  `strk20_invoke_action` ABI-encodes the `InvokeExternal` client action for
  a helper. Nothing proves, signs or submits — that needs the user's viewing
  key, which belongs in their wallet / the Privacy SDK.
- **MCP**: 20 read-only tools over stdio (`python3 mcp.py`) and Streamable
  HTTP (`POST /mcp` on the same port).

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
abi.py         Cairo ABI codec: iface / read / encode / events for any contract
strk20.py      the STRK20 privacy pool: state, activity, users, notes, helpers, docs
mcp.py         MCP server (JSON-RPC 2.0, stdio + mounted at POST /mcp)
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

| `GET /contract?address=` | a contract's functions + events from its on-chain ABI |
| `GET\|POST /read` | `{contract, function, args}` — typed call, decoded result |
| `GET\|POST /encode` | `{contract, function, args}` — calldata only |
| `GET /events?address=&name=&limit=` | decoded events, newest first |
| `GET /strk20/pool` | pool version, fee, proof window, keys, holdings |
| `GET /strk20/activity?event=` | the pool's public tape |
| `GET /strk20/user?address=` | registered? channels, deposits, withdrawals |
| `GET /strk20/note?id=` · `/strk20/nullifier?value=` | one note / spend check |
| `GET /strk20/helpers` · `/strk20/helper?address=` | anonymizer contracts |
| `POST /strk20/invoke_action` | `{helper, args}` → InvokeExternal action felts |
| `GET /strk20/docs?page=&q=` | the official docs as markdown |
| `POST /mcp` | MCP JSON-RPC 2.0 · `GET /tools` lists, `POST /tools/<name>` runs |

Every read takes `?network=mainnet|sepolia`. Contract arguments accept the
aliases `eth`, `strk`, `usdc` and `strk20`. Errors return HTTP 400 with a
JSON body (never 5xx — proxies strip those bodies).

```bash
curl -s localhost:51020/status
curl -s 'localhost:51020/balance?address=0x0498...&token=strk'
curl -s localhost:51020/call -d '{"contract":"0x049d...","entrypoint":"balanceOf","calldata":["0x0498..."]}'
```

## MCP

```json
{"mcpServers": {"starknet": {"type": "http", "url": "http://localhost:51020/mcp"}}}
```

or stdio: `{"command": "python3", "args": ["/path/to/starknet/mcp.py"]}`.

Tools: `starknet_status block tx account balance contract read encode events
storage rpc` and `strk20_pool activity user note nullifier helpers helper
invoke_action docs`. All are read-only (`readOnlyHint`).

## Env

| var | meaning |
| --- | --- |
| `PORT` | API + app port (default 51020) |
| `STARKNET_NETWORK` | default network (`mainnet`) |
| `STARKNET_RPC` | your own node — always tried first |
| `STARKNET_TIMEOUT` | per-endpoint timeout, seconds (default 15) |
| `STRK20_POOL[_SEPOLIA]` | override the pool address (no sepolia pool is built in) |
| `STARKNET_ABI_CACHE` | ABI cache dir (default `~/.mod/starknet/abi`) |

## Test

```bash
python3 chain.py     # offline vectors (keccak, selector) + live status
python3 mod.py       # same, plus local API health if serving
```

The offline vectors pin `keccak256(b'') = c5d24601…` and
`selector('balanceOf') = 0x2e4263af…` — the live chain agrees (verified with
a real `starknet_call` on mainnet).

## Scope

Reads only (v0.2). No keys are held anywhere, so there is nothing to leak.
STRK20 private transactions need a viewing key and a Stwo proof; this module
explains and encodes them but leaves proving/signing to the wallet or the
`@starkware-libs/starknet-privacy-sdk`. Writes
(account deployment, invoke transactions, a keystore under
`~/.mod/starknet/`) are a later version, following the near module's shape:
testnet by default, explicit confirm for mainnet, secrets never in the repo.
