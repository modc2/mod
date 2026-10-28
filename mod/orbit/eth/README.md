# eth

**Write a smart contract, test it on a testnet, deploy it, share it — for
people and for agents.**

One engine behind four faces. A console you can click, a REST API, a CLI, and
42 MCP tools. Same gas estimation, same keystore, same refusal to spend real
money without being told to, whoever is driving.

```
console   http://localhost:50731/eth        · plain ES modules, no build step
api       http://localhost:50730            · FastAPI, mod-protocol auth
cli       m eth/test project=my-token account=dev network=base-sepolia
mcp       POST http://localhost:50730/mcp   · or `python3 mcp.py` on stdio
```

The arc the console is built around, left to right:

```
   write it  ──▶  save it  ──▶  test it  ──▶  deploy it  ──▶  share it
   editor        a CID from    a real         an address     the same CID
                 the store     testnet run    on a chain     is the link
```

## What it does

**The bench.** One screen does the whole arc: your projects on the left, the
code in the middle, what happened on the right. The head of the bench says what
the buffer is — unsaved, private, public, a copy, whose — because that decides
what **save** is about to do. **share** is one press from writing to a link: it
saves what is on screen (publishing last week's version is worse than a click),
publishes it, and hands you the URL. **deploy** compiles first if it has to,
and answers with the address, the gas it cost, an explorer link and a button
that opens the thing you just deployed on the interact tab.

**Projects.** A project is one or more Solidity files, an entry contract and
the test suites that check it. Saving does not write a file on this box — it
uploads the project to the **store module** and keeps the CID that comes back.
That has three consequences worth having:

- **a version is a CID.** Saving again mints a new one; the old one still
  resolves, so history is free and nothing is overwritten.
- **sharing is handing somebody a CID.** `share` publishes the object; the link
  `…/eth/?open=<cid>` opens the project in this console for anyone, signed in
  or not, because the store serves a public object to anybody.
- **a fork is honest.** Opening somebody's CID puts their project on the bench,
  says whose it is above the code, and gives you a fork button. Typing into it
  turns it into your copy on the spot rather than letting you edit something
  you cannot save; either route records `origin_cid`, so a copy always says
  where it came from.

If the store refuses — it is asleep, or your address is not on its whitelist —
the save still lands in the local index with its bytes cached, and the console
says exactly what is blocking the CID. Losing somebody's source to an upload
error would be a worse bug than not having a CID.

**Tests, on a real chain.** `POST /test` compiles the project, deploys it,
sends every write for real and reports what the chain thought. The default
network is a testnet, and not by convention: a suite is nothing but writes, and
a write on a non-testnet chain is refused without `confirm: true`.

A suite is JSON, so it survives a round trip through the store and an MCP
client:

```json
{
  "name": "erc20 basics",
  "args": ["Test", "TST", 1000],
  "cases": [
    {"name": "the name is set",  "fn": "name",      "expect": "Test"},
    {"name": "minted to me",     "fn": "balanceOf", "args": ["$deployer"],
                                 "expect_gt": 0},
    {"name": "transfer emits",   "fn": "transfer",  "args": ["$zero", 1],
                                 "expect_event": "Transfer"},
    {"name": "cannot overspend", "fn": "transfer",  "args": ["$zero", "10**60"],
                                 "expect_revert": true}
  ]
}
```

A case names one function. Whether that is a free `eth_call` or a signed
transaction is read off the ABI, not off the case — `view` and `pure` are
already the declaration of intent, and making the author repeat it is how a
suite ends up asserting on a call that never ran.

| in a case | what it checks |
|---|---|
| `expect` | the returned value, after normalising (`"5"` == `5`, addresses are case-insensitive) |
| `expect_gt` `expect_gte` `expect_lt` `expect_lte` | a numeric bound |
| `expect_contains` | a substring of the returned value |
| `expect_event` | an event fired — `"Transfer"`, or `{"name": …, "args": {…}}` to pin its arguments |
| `expect_revert` | it reverted — `true`, or a string the revert reason has to contain |
| `expect_status` | the receipt status |

Placeholders expand in arguments *and* expectations: `$deployer`, `$contract`,
`$zero`, `$account:<name>`. `"10**18"` is accepted anywhere a number is, because
a token amount written out in full is nineteen digits nobody proof-reads.

With no suite at all, every zero-argument getter on the ABI is called. That
proves the deploy, not the behaviour, and the report says so.

Every run is recorded, and the report goes to the store like everything else —
so "it passed on Base Sepolia" is a CID somebody can fetch and read, with the
transaction hashes in it, rather than a screenshot.

**Accounts.** Keystore-v3 keys, scrypt-encrypted under a password this module
never stores, namespaced by the address that created them. Create, import a
private key or a BIP-39 mnemonic, unlock for a bounded window, export, back up.

**Reads.** Balances (native and ERC-20), nonces, blocks, transactions and
receipts, contract code, storage slots, event logs decoded against a known ABI,
ERC-20 metadata, live gas — across 14 built-in EVM chains and any you add.

**Writes.** Send the native currency, call any contract function, move and
approve ERC-20s. Every write is gas-estimated first, so a transaction that
would revert is never broadcast.

**Deploys.** Solidity in, an address out. `solc` is found on the box (foundry's
svm store, hardhat's cache, `which solc`) before anything is downloaded, and a
fetched compiler is checksummed against the published keccak list. The ABI, the
source, the constructor arguments and the exact compiler settings are recorded
against the address, so the contract is still usable a year later.

**Nine contracts that ship with it** — self-contained Solidity, no imports, no
package manager:

| template   | what it is |
|------------|------------|
| `counter`  | the smallest real deploy — proves account, network and receipts work |
| `token`    | an ERC-20 you control the supply of |
| `nft`      | an ERC-721 with per-token metadata and an optional public mint |
| `storage`  | a key→value registry where the first writer keeps the key |
| `anchor`   | timestamp a CID or hash on chain — cheap proof you had it first |
| `vault`    | ETH you cannot spend until a date you set |
| `escrow`   | buyer funds, seller delivers, an arbiter breaks ties after a deadline |
| `splitter` | incoming ETH divided by fixed shares, claimed on demand |
| `multisig` | n-of-m approval before anything leaves the contract |

## Two properties worth knowing

**A non-testnet write is refused without `confirm: true`.** Not a config flag,
not a default the environment can flip — an argument on the request that spends
the money. It is enforced in the engine, so the console, the CLI and an agent's
MCP call all hit the same wall.

```
$ m eth/send account=dev to=0x… value=0.1 network=mainnet
mainnet is not a testnet — this spends real funds. Send confirm=true to go
ahead, or use a testnet (base-sepolia, holesky, sepolia, …)
```

**A key is only usable while it is unlocked.** `unlock` decrypts into memory
for at most 15 minutes; a restart or the timeout forgets it. Everything else
carries the password for one call. Nothing signs with a key the caller did not
just prove they can open.

## Quickstart

```bash
./serve.sh                     # pm2: eth-api (:50730) + eth-app (:50731)
anvil                          # optional: a free local chain on :8545
```

Then open <http://localhost:50731/eth> and sign in with a browser wallet, or:

```bash
m eth/status
m eth/account name=dev password=…                  # a new key
m eth/balance address=dev network=local

# write it, test it, share it
m eth/save name=Counter path=./Counter.sol         # → a CID from the store
m eth/generate_tests project=counter               # a starter suite
m eth/test project=counter account=dev password=… network=base-sepolia
m eth/share project=counter                        # → /eth/?open=<cid>
m eth/open cid=Qm…                                 # read somebody else's
m eth/fork cid=Qm…                                 # …and make it yours

# or deploy straight from a template
m eth/deploy account=dev template=counter args=[0] network=local
m eth/read address=0x… function=value network=local
m eth/write address=0x… function=add args=[5] account=dev network=local
```

## Networks

`local` (anvil/hardhat on :8545) · `mainnet` · `sepolia` · `holesky` · `base` ·
`base-sepolia` · `optimism` · `op-sepolia` · `arbitrum` · `arbitrum-sepolia` ·
`polygon` · `polygon-amoy` · `bsc` · `avalanche`

Defaults are public RPCs, which is what makes this useful with no
configuration. Replace any of them, most specific first:

```bash
ETH_RPC_MAINNET=https://eth-mainnet.g.alchemy.com/v2/<key>   # env
m eth/network_add name=mychain rpc=https://… chain_id=1234    # ~/.mod/eth/networks.json
```

A network whose RPC reports a different chain id than its name claims is
reported as **not ok** — that mismatch is how a testnet deploy ends up
somewhere that costs money.

## For agents

```bash
claude mcp add --transport http eth http://localhost:50730/mcp \
  --header "Authorization: Bearer <mod-protocol token>"
```

`GET /mcp` is the whole schema as a document — protocol, transports, auth,
every tool's inputSchema — so a client is not needed to read it. Reads
(`eth_status`, `eth_balance`, `eth_block`, `eth_read`, `eth_compile`,
`eth_templates`, …) need no token. Anything that touches a key does.

A typical agent arc:

```
eth_status → eth_networks → eth_new_account → eth_unlock
           → eth_save_project → eth_generate_tests → eth_test   (a testnet)
           → eth_deploy → eth_read / eth_write
           → eth_share_project                                  (a CID)
```

`eth_open_project` reads anybody's shared CID with no account at all, which is
how one agent hands a contract to another.

## Layout

```
mod.py          the CLI and Python face
api/api.py      FastAPI — the API on :50730
app/            the console (:50731)
  index.html      the shell
  app.css         one palette, dark and light
  app.js          the chrome and the non-bench tabs
  build.js        the build bench: projects, editor, tests, deploy, share
  editor.js       a Solidity editor — gutter, highlighting, no dependencies
  server.py       static files + a proxy for /eth/_api
mcp.py          42 MCP tools, stdio + the POST /mcp transport
chains.py       the network registry
wallet.py       keystore-v3 accounts and bounded unlocks
compiler.py     solc over standard JSON — found, or fetched and checksummed
catalog.py      the nine templates, described from their own source
ledger.py       sqlite: deployments, attached ABIs, every tx this module sent
ops.py          the chain operations themselves — every face calls these
store_link.py   the bridge to the store module — always the caller's token
projects.py     projects: bytes to the store, an index here
harness.py      the test runner — deploy, push it, report what came back
templates/      the nine .sol files
test/           105 tests; chain ones skip without :8545, store ones without
                a store that will take an upload from this box
```

## State

Everything lives in `~/.mod/eth` and none of it is committed:

```
accounts/<caller>/<name>.json   keystore v3 — the key, encrypted
eth.db                          deployments, attached ABIs, tx history,
                                the project index and past test runs
networks.json                   chains the owner added
owner.json                      who claimed this deployment
solc/                           compilers fetched on demand
```

Note what is *not* here: contract sources. Those live in the store module,
addressed by CID. `eth.db` holds the index and a cache of the last bundle, so
the console still renders your work when the store is asleep — a cache is
allowed to be stale, and is never the source of truth.

Losing an account password loses the account. There is no recovery hook,
because a recovery hook is a second way to spend the money.

## Environment

| variable | what it does |
|---|---|
| `ETH_DIR` | state dir (default `~/.mod/eth`) |
| `ETH_NETWORK` | default network (default `local`) |
| `ETH_RPC_<NAME>` | override one chain's rpc |
| `ETH_PRIVATE_KEY` | a key exposed as the account `env` — for a headless box that is deliberately trusted |
| `ETH_MAX_UNLOCK` | ceiling on an unlock ttl in seconds (default 900) |
| `ETH_SOLC_DOWNLOAD` | `0` = never fetch a compiler |
| `ETH_OPEN` | `1` = every caller is one local identity (development only) |
| `ETH_API_PORT` / `ETH_APP_PORT` | ports (50730 / 50731) |
| `ETH_STORE_URL` | where the store module is (default `http://127.0.0.1:50152`) |
| `ETH_ACTIVATOR_URL` | the activator that wakes a slept store (default `http://127.0.0.1:9000`; empty turns waking off) |

## Tests

```bash
python3 -m pytest test/ -q
```

They run against a throwaway state dir — never `~/.mod/eth`, which holds real
keys. The chain tests use anvil's first default account (funded on any fresh
node, worthless anywhere else) and skip when nothing is listening on :8545. The
store tests talk to the **real** store module and skip when it will not take an
upload from this box: a mocked store would only prove this module can talk to
a mock.
