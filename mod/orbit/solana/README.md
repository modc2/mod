# solana

Solana as one mod: twenty-six MCP tools, a REST API and a browser console, all
running the same code on one port. Read the chain, rank every token on it by the
liquidity actually behind it, move value, and deploy, load and call programs.

```
m solana/account 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM
m solana/portfolio <wallet>
m solana/tx <signature>
m solana/quote SOL USDC 10
m solana/tokens sort=liquidity limit=50
m solana/liquidity BONK
m solana/venues
m solana/swap SOL USDC 0.5 confirm=1
m solana/program whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc
m solana/deploy clone=memo network=devnet wallet=hot
m solana/invoke <program> data=text:hello accounts='["s:self"]' wallet=hot
m solana/serve
```

API `:50710` (`/api/solana`) · console `/solana` · MCP `POST /mcp` (26 tools)

## The problem it is shaped around

A Solana address is 32 opaque bytes and will not tell you what it is. The same
string could be somebody's wallet, a token mint, a token account, a stake
account or a deployed program — and every one of those wants a different
question asked of it.

So `sol_account` comes first. Give it any address and it says which of those it
turned out to be, then returns the detail that matches. Everything else branches
from there.

## What each tool is for

**Identify and hold**
- `sol_account` — what an address IS. Start here.
- `sol_balance` — SOL for one address or many, in USD.
- `sol_portfolio` — SOL plus every SPL position across both token programs,
  merged per mint and sorted by value. Dust is counted and excluded rather than
  padding the list; unpriced tokens are reported as a count. The total is what
  could be sold, not what is nominally held.
- `sol_stake` — stake accounts, and whether each is active, activating,
  deactivating or cooled down. Staked SOL never shows up in a balance, so this
  is the missing half of "how much does this wallet control".

**Understand what happened**
- `sol_history` — recent signatures, newest first. `detail=true` summarises each
  one *for the address you asked about*.
- `sol_tx` — one transaction, decoded: who paid, net SOL per account, net token
  change per **owner** and mint, which programs ran and what they were asked to
  do. Not a wall of account indexes.

**Value**
- `sol_price` — by mint or by symbol.
- `sol_tokens` — every routable token on Solana, ranked by liquidity. The
  universe, filterable and sortable. See below.
- `sol_liquidity` — one token's liquidity three ways, one of which is measured
  rather than reported.
- `sol_pools` — every pool holding a token, deduped across indexes and screened.
- `sol_venues` — where the chain's depth actually sits, by DEX.
- `sol_token` — a mint in full, including the two authorities that decide
  whether someone can still inflate the supply or freeze your account.
- `sol_quote` — Jupiter's best route, with real price impact.
- `sol_swap` — take that route. Jupiter builds the transaction, this module
  signs the exact bytes it was handed with a keystore key and sends them, so the
  price you were quoted is the price you traded. Mainnet only, guarded by
  `SOLANA_SPEND_USD` like a transfer.

**The chain**
- `sol_network` — slot, epoch, TPS, supply, inflation, health.
- `sol_validators` — the set by stake, with the Nakamoto coefficient.

**Writing**
- `sol_wallet` — the off-tree keystore.
- `sol_transfer` — SOL or SPL tokens, signed here.
- `sol_airdrop` — devnet/testnet faucet.
- `sol_rpc` — any JSON-RPC method, for the long tail.

**Programs**
- `sol_program` — what is deployed at an address: loader, upgrade authority,
  code size, syscalls, IDL, and the accounts it owns.
- `sol_idl` — a program's interface. `action=set` teaches this module one the
  program never published.
- `sol_deploy` — put an ELF on chain, or upgrade one in place. A background job.
- `sol_invoke` — build one instruction and simulate it; `send=true` signs it.
- `sol_pda` — derive a program address from seeds.
- `sol_authority` — hand over the right to upgrade, revoke it forever, or close
  a program and take the rent back.

## Liquidity is three different numbers, and they disagree

"How much liquidity does this token have" has no single answer, and any venue
that prints one is hiding that. Three separate quantities get the same name:

| | what it counts | who says so |
|---|---|---|
| **quotable** | what an aggregator believes it can route right now, one-sided | the router |
| **pool reserves** | the USD in every pool the token trades in, *both sides* | the pool indexes |
| **executable** | the largest sell that still clears under a 1% all-in cost | **measured here** |

The first two are reports. The third is a measurement: `sol_liquidity` prices
real sells of $1k, $10k, $100k and $1m through the live router, then bisects
between the two rungs either side of the limit and quotes again until it has the
number. Cost is `1 − received ÷ nominal`, so it includes the fees and the
route's own spread, not just the impact figure the router prints.

The gap is not a rounding error. At the time of writing:

```
BONK   quotable $736k · reserves $882k · executable $36.5k     a 20× gap
JUP    quotable $3.19m ·                 executable $237k      a 13× gap
```

A $1m JUP sell returns about $18k. That is the whole reason this exists: the
headline says the token is fine and the exit says otherwise, and only one of
them is a measurement.

Everything around that number is there to make it readable:

- **Pools, deduped by address.** Two indexes describing the same pool is one
  pool, not double the liquidity. Where they disagree about its reserves the
  **smaller** reading is counted and both are kept on the row — overstating
  depth costs someone money on a sell that will not fill; understating it costs
  nothing but caution.
- **Fake depth is screened out, not hidden.** A pool claiming more reserves than
  the token has market cap, or a thousand times what its siblings hold, stays in
  the list with the reason attached and is excluded from every total.
- **Concentration.** An HHI over the pool shares, because one deep pool and nine
  dust ones is not ten pools of liquidity — it is a market maker who can leave.
- **Flags that finish the sentence.** Not `mint_authority: true` but *"supply
  can still be inflated — the mint authority is live"*. Not `turnover: 400` but
  *"volume is 400× the liquidity in 24h — usually bots, not demand"*.
- **`redeemable`.** Jupiter reports staked-SOL wrappers as having liquidity
  equal to their entire market cap. That is redemption, not a book, and
  unflagged it puts twenty LSTs above SOL itself in any ranking.

`sol_tokens` ranks the whole universe — about 3,200 mints holding some $7.8bn of
quotable liquidity, of which the top ten are two thirds and a third of the list
has no market at all. Sort by liquidity, volume, market cap, holders, turnover,
24h move, age or how much of the cap the liquidity actually is; filter by tag,
size, or whether the mint and freeze authorities are still live.

### Adding a source

Sources are plug-in and `Book` never names one. A source answers what it can —
`universe`, `tokens`, `pools`, `top_pools` — and raises `Unsupported` for the
rest; one being down degrades a column, never the answer. Adding a venue is a
class and a line in `SOURCES`:

```python
class MyIndex(Source):
    name, measures, per_minute = 'myindex', 'reserves', 60

    def pools(self, mint, limit=50):
        return [{'address': …, 'dex': …, 'pair': …, 'liquidity_usd': …}]
```

Each source declares its own per-minute budget and stands down when it is spent,
rather than discovering the public cap by being blocked. Jupiter is the one
everything routes through — prices, symbols and every quote — so calls to it are
*paced* instead: a burst waits its turn, and only a wait longer than anyone
would sit through becomes an error.

## Programs: deploy one, load one, call it

A program is an ELF of sBPF bytecode in an account, and the chain will not tell
you what is in there. `sol_program` opens it up: which loader owns it, **who can
still replace the code**, how big it is, which syscalls it imports —
`sol_invoke_signed_` means it signs for its own PDAs and can move tokens those
addresses hold — and, if it publishes an anchor IDL, every instruction it takes
with argument types and account lists.

Then call one. `sol_invoke` simulates against live cluster state first, every
time: logs, compute units, the writable accounts as they would look afterwards,
and a plain sentence when it fails (an anchor custom error becomes the name and
message from its own IDL). Nothing is signed until `send=true`, and a call that
fails simulation is not sent at all unless you add `force=true`.

With an IDL, arguments and accounts go by name — the sysvars, your wallet and
any PDA whose seeds the IDL declares are filled in, and anything still missing
is named back at you. Without one, pass `data=` as hex, base64 or
`text:the literal characters` and list the accounts yourself
(`ws:<address>` for writable+signer, `self` for your wallet).

Deploying takes an ELF from a `.so` on this box, from base64, or — when there is
no Rust toolchain in reach — from a program that already exists:

```
m solana/deploy clone=memo network=devnet wallet=hot
```

That reads the deployed bytes of the mainnet memo program and redeploys them
under an address you control. Behind it: a buffer account, one Write
transaction per 900 bytes, a read-back that rewrites whatever did not land, and
`DeployWithMaxDataLen`. It runs as a background job — a real program is hundreds
of transactions — and every keypair it generates is written to the keystore
*before* it is used, because losing a program keypair mid-deploy makes that
address unusable forever.

Deploy costs rent, and rent is the real number: roughly `2 × ELF bytes` of
programdata at ~0.007 SOL per kilobyte, refundable only by closing the program.
The estimate comes back before anything is signed, and a mainnet deploy needs
`confirm=true`.

Two constraints worth knowing before you hit them:

- **A program cannot grow.** `max_data_len` is fixed at deploy time (default:
  twice the ELF). A later upgrade that no longer fits has to go to a new
  address, and the module refuses it with that sentence rather than a failed
  transaction.
- **Old ELFs.** Clusters are turning off deployment of SBPF v0/v1/v2 binaries.
  Most programs deployed years ago are v0, so cloning one to a cluster that has
  activated SIMD-0500 fails with *"Detected sbpf_version required by the
  executable which are not enabled"*. Devnet and mainnet still take them today;
  a stock `solana-test-validator` does not.

## Symbols are not unique

Anyone can mint a token called USDC, and a search index will happily return it.
`SOL`, `USDC` and `USDT` are pinned to their canonical mints and never looked
up. Every other symbol resolves to the deepest-liquidity match, and **the mint
it chose comes back with the answer** — check it before trusting the number.

## Keys and money

There is no house wallet. A transfer is signed in-process with a seed that came
from the caller, from `SOLANA_SECRET_KEY`, or from `~/.mod/solana/keys.json`
(mode 0600, off the source tree), and the signed bytes go straight to the node.
The seed is never logged and never leaves the process.

`base58` and `ed25519` are implemented in-tree so the module has no hard
dependencies; when PyNaCl or `cryptography` is installed it signs with that
instead, because a C signature is far faster and the bytes are identical. The
test suite pins the pure-Python path against an RFC 8032 vector and against
whichever fast backend is present.

Two guards:

- **Value guard.** A transfer worth more than `SOLANA_SPEND_USD` (default $25)
  comes back as `needs_confirm` with a full plan and moves nothing. Call again
  with `confirm=true`.
- **Write gate.** Anything touching the keystore or moving lamports needs
  `Authorization: Bearer <~/.mod/solana/server.secret>`. With no secret file
  those routes answer only on loopback. The gate covers `/mcp` tool calls as
  well as the REST routes — a gate on one and not the other would be no gate.

Reads are open. The chain is public.

## Networks

`network=mainnet|devnet|testnet`, or a full RPC url. `rpc=` overrides per call
and `SOLANA_RPC` sets the default — worth doing, because the public endpoints
throttle. When Jupiter throttles, USD fields go null and the response says so in
`warnings` rather than implying zero.

## Connect an agent

```json
{"mcpServers": {"solana": {"type": "http", "url": "http://localhost:50710/mcp"}}}
```

or `python3 mcp.py` for stdio.

## Tests

```
python3 -m pytest -q            # 85 tests, 19 of them live
SOLANA_OFFLINE=1 python3 -m pytest -q
```

The offline half pins the cryptography and the wire format — base58 leading
zeros, RFC 8032 vectors, ATA derivation against a known on-chain value, account
ordering in a serialized message, borsh against both IDL dialects, anchor's
discriminator hashes, and the loader's bincode lengths. Those must be exactly
right or a transaction is either rejected or, worse, accepted and wrong.

The program path was proved end to end against a local validator: clone the
mainnet memo program, deploy it (84 write transactions, ~13 s), call it for
real, upgrade it in place, hand the authority to another key, watch the upgrade
be refused, then close it and get the rent back.

```
solana-test-validator --deactivate-feature B8JJXCy5amZyWG9r7EnUYLwzXSXTxG7GZ1qZ1qggo83g
m solana/deploy clone=memo network=http://127.0.0.1:8899 wallet=dev
```

(that feature is SIMD-0500 — without deactivating it a stock validator will not
accept the older ELFs that most live programs still are.)
