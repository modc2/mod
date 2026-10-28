---
name: ethdesk
description: The eth bench with a rebuilt console (sticky chain/identity bar, a picker that goes gold on a real chain, a live block heartbeat, compile and save shortcuts) — write, test, share and deploy Ethereum smart contracts: a Solidity bench whose projects live in the store module and are shared by CID, a test runner that deploys to a testnet and asserts on real receipts, plus keystore accounts, ERC-20s and event logs across 14 EVM chains. CLI, API :50750, console :50751/ethdesk, MCP server (42 tools, schema at GET /mcp).
type: orbit-module
---

# ethdesk

Everything you need a chain for, on any EVM: write a contract, test it on a
testnet, deploy it, share it, hold keys, read state, move money. One engine
behind a console, a REST API, a CLI and 42 MCP tools, so a person and an agent
do the identical thing.

## When to reach for it

- "write me a contract and check it works" → `save` → `test` → `deploy`
- "send me your contract" / "open this CID" → `share`, `open`, `fork`
- "deploy me a token / NFT / multisig" → `templates` then `deploy`
- "what is this address / tx / contract" → `balance`, `tx`, `contract`, `token`
- "call this contract" → `read` (free) or `write` (gas)
- "what did I deploy, and where" → `contracts`, `history`
- "what will this cost" → `gas`, `estimate`
- Solidity you want checked but not deployed → `compile`

## Projects live in the store, not on this box

`save` uploads the project (its files and its suites) to the **store module**
and keeps the CID that comes back. A version is a CID; sharing is handing
somebody a CID; `…/eth/?open=<cid>` opens it for anyone, signed in or not.

Every call to the store carries the **caller's own** protocol token, so the
store's whitelist, quota and terms apply to whoever asked — this module holds no
store credentials and cannot reach anything the caller could not. When the store
refuses, the save still lands locally with the reason attached and `cid: null`;
`m eth/store` says what is blocking it.

## Testing means a real chain

`test` compiles, deploys and sends every write for real, then reports what came
back. A suite is JSON:

```json
{"name": "basics", "args": [5], "cases": [
  {"name": "starts at 5",  "fn": "count",  "expect": 5},
  {"name": "bump emits",   "fn": "bump",   "expect_event": "Bumped"},
  {"name": "guard holds",  "fn": "setTo", "args": [500], "expect_revert": "too big"}
]}
```

Free call or signed transaction is read off the ABI, not off the case.
Expectations: `expect`, `expect_gt`/`gte`/`lt`/`lte`, `expect_contains`,
`expect_event`, `expect_revert`, `expect_status`. Placeholders `$deployer`,
`$contract`, `$zero`, `$account:<name>` expand in arguments and expectations,
and `"10**18"` works anywhere a number does. With no suite, every zero-argument
getter is called — that proves the deploy, not the behaviour. Each report gets
its own CID.

## The two rules

**Real money asks once, out loud.** Any write on a chain where `testnet` is
false is refused unless the call carries `confirm=true`. `local` (anvil on
:8545) and the testnets are free. Do not set `confirm` unless the user asked
for a real transaction on that chain.

**A key signs only while unlocked.** `unlock name=… password=… ttl=…` holds it
in memory for up to 15 minutes; otherwise every write carries the password.

## Auth in one line

Everything that touches a key or your private index takes a mod-protocol token
(`m.mod('auth')().token({})`) as `Authorization: Bearer …`. Accounts and
deployments are namespaced by the signing address. Reads need nothing.

## CLI

```bash
m eth/status                                  # network up? solc? accounts?
m eth/networks check=1                        # ping all 14 chains

m eth/account name=dev password=…             # a new key (mnemonic=1 for a phrase)
m eth/import_account name=dev password=… secret=0x…
m eth/unlock name=dev password=… ttl=600
m eth/balance address=dev network=base

m eth/save name=Counter path=./Counter.sol    # → a CID from the store
m eth/projects                                # what you have, and its CIDs
m eth/generate_tests project=counter          # a starter suite off the ABI
m eth/test project=counter account=dev password=… network=base-sepolia
m eth/tests                                   # past runs · m eth/report run_id=3
m eth/share project=counter                   # → /eth/?open=<cid>
m eth/open cid=Qm…                            # read anybody's shared project
m eth/fork cid=Qm…                            # …and make it yours
m eth/store                                   # can this address upload?

m eth/templates                               # the nine shipped contracts
m eth/compile template=token                  # abi + size, nothing sent
m eth/deploy account=dev template=token args='["Mod","MOD",18,1000000]' network=sepolia
m eth/deploy account=dev path=./MyThing.sol args='[42]' network=local

m eth/contract address=0x…                    # its reads, writes and events
m eth/read address=0x… function=balanceOf args='["0xabc…"]'
m eth/write address=0x… function=transfer args='["0xabc…", 100]' account=dev
m eth/transfer token=0x… to=0x… amount=12.5 account=dev     # ERC-20
m eth/send to=vitalik.eth value=0.01 account=dev network=mainnet confirm=1

m eth/history                                 # every tx this box sent for you
m eth/serve                                   # pm2: eth-api + eth-app
```

## API

```
GET  /status /networks /balance /block /tx /gas /code /logs /templates
GET  /open?cid=…                               # a shared project, no token
POST /compile /test/generate                   # all of the above need no token

GET  /projects            POST /projects       {name, source|files, entry}
GET  /projects/{p}        PUT  /projects/{p}   # a new version = a new CID
POST /projects/{p}/share  POST /projects/{p}/unshare
POST /fork                {cid}
GET  /store               POST /store/terms
POST /test                {project|source, account, network, suites, confirm}
GET  /tests               GET  /tests/{run_id}

POST /accounts            {name, password}     # or {secret} to import
POST /accounts/{n}/unlock {password, ttl}
POST /deploy              {account, template|source, args, network, confirm}
POST /contracts/{addr}/read   {function, args}
POST /contracts/{addr}/write  {account, function, args, confirm}
POST /send                {account, to, value, confirm}
GET  /contracts /history
```

## MCP

```bash
claude mcp add --transport http ethdesk http://localhost:50750/mcp \
  --header "Authorization: Bearer <token>"
```

`GET /mcp` is the whole schema as a document. The arc an agent should follow:

```
eth_status → eth_networks → eth_new_account → eth_unlock
           → eth_save_project → eth_generate_tests → eth_test   (a testnet)
           → eth_deploy → eth_read / eth_write
           → eth_share_project                                  (a CID)
```

`eth_open_project` reads anybody's shared CID with no account — that is how one
agent hands a contract to another. `eth_store` says why a save has no CID.

## Amounts

A decimal string is the human unit (`"0.1"` = 0.1 ETH, `"12.5"` = 12.5 tokens
whatever the decimals are). A bare integer, or a `"…wei"` suffix, is wei. ERC-20
decimals are read off the contract, so you never scale by hand.

## Templates

`counter` · `token` (ERC-20) · `nft` (ERC-721) · `storage` (key→value registry)
· `anchor` (timestamp a CID) · `vault` (timelock) · `escrow` · `splitter` ·
`multisig`. All self-contained — no imports, no package manager.

## Gotchas

- **Test on `local` or a testnet before you deploy anywhere that costs.** A
  contract cannot be edited once it is out there — `eth_test` is the cheap way
  to find that out.
- A generated suite asserts *nothing*. It proves the constructor ran and the
  getters answer; the expectations are yours to write.
- A project's CID changes on every save. The old one still resolves, so a link
  you shared yesterday shows yesterday's code — reshare after a change.
- A restart of the API forgets every unlock. That is the design, not a bug.
- Losing an account password loses the account. `export_account` and
  `/accounts/{n}/keystore` exist so you can take a backup — do it before you
  fund anything.
- Event logs: narrow the block range. Public RPCs refuse wide ones, and
  `latest` to `latest` is a single block.
- Public RPCs are rate-limited. Set `ETH_RPC_<NAME>` to your own endpoint for
  anything sustained.
