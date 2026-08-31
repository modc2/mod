# zcash

Explorer, wallet and cross-chain bridge for Zcash — transparent sends, and a
real shielded account you can spend from.

```
m zcash/capabilities          # what works, and what does not
m zcash/test                  # self-test: chain, signer, bridge, explorer
```

## What it does

| | |
|---|---|
| **Explorer** | blocks, transactions, addresses, mempool, price, network |
| **Wallet** | BIP39/BIP44 HD wallets, WIF import, balances, UTXOs |
| **Send** | builds, signs and broadcasts NU5 v5 transparent transactions |
| **Shielded** | Sapling + Orchard keys, `zs1` + unified addresses, note decryption, real sends |
| **Bridge** | ZEC ⇄ Ethereum, Base, Arbitrum, Solana, BTC, Tron and ~30 more |
| **Private bridge** | another chain → straight into your **shielded** pool, no transparent hop |
| **Learn** | plain-language lessons and a glossary, for someone starting from zero |
| **Ask** | an agent that answers Zcash questions from those lessons plus live reads |

Shielded ZEC is received, read **and sent** here. Sending needs a zk-SNARK
prover, which is built once from source into `~/.mod/zcash/bin/` — see
[Sending shielded ZEC](#sending-shielded-zec). Bridging *in* needs no prover
at all — the solver on the other chain creates the shielded output. See
[Private bridging](#private-bridging).

## Wallet

```bash
m zcash/wallet_create name=savings password=<pw>     # prints the seed ONCE
m zcash/wallet_restore name=old password=<pw> mnemonic="word word ..."
m zcash/wallet_import  name=savings password=<pw> wif=L...
m zcash/wallet_balance name=savings
m zcash/wallet_new_address name=savings password=<pw>
```

Wallets are stored in `~/.mod/zcash/wallets/<name>.json`, never in the repo.
Addresses are kept in the clear so balances work without a password; the
mnemonic and any imported keys are sealed with AES-256-GCM behind
PBKDF2-SHA256 (600k iterations). A password is required for anything that
spends.

## Sending

`send` is a **dry run by default**. It builds and fully signs the transaction,
verifies its own signature, and returns the raw hex without submitting it.

```bash
# preview — signs but does not broadcast
m zcash/send name=savings password=<pw> to=t1... amount=0.1

# actually publish
m zcash/send name=savings password=<pw> to=t1... amount=0.1 broadcast=True
```

Fees follow ZIP-317 (10,000 zatoshi for an ordinary 1-in/2-out transaction).
Change returns to the sending address; change below the dust threshold is
folded into the fee instead of creating an unspendable output.

### Why the signer can be trusted

Getting a Zcash signature wrong produces a transaction the network silently
rejects, so the implementation is pinned against the chain itself:

* a mined mainnet v5 transaction re-serializes **byte for byte**;
* its **ZIP-244 txid** is reproduced exactly;
* its **real signature verifies** against our computed ZIP-244 sighash;
* a transaction we sign ourselves is accepted by `zcashd`'s parser — pushing
  one that spends a nonexistent output is rejected with `Missing inputs`, not
  a decode error.

`tests/test_zcash.py` keeps the first three as regression tests.

The **consensus branch id is read from the chain**, not hardcoded — it changes
at every network upgrade, and a stale constant would invalidate every
signature. It is taken from the header of a recent v5 transaction (or from a
node, when one is configured).

## Bridging

Zcash has no native smart-contract bridge, so value moves through solver
networks. You get a deposit address, send to it, and the solver pays out on the
destination chain.

```bash
m zcash/bridge_chains                        # what is reachable
m zcash/bridge_quote  to_asset=ETH amount=1 recipient=0x... refund_to=t1...
m zcash/bridge_start  to_asset=eth:USDC amount=1 recipient=0x... refund_to=t1...
m zcash/bridge_status deposit_address=t1...
```

Assets are `ETH`, `BTC`, `SOL` (bare symbols resolve to their home chain) or
qualified as `eth:USDC`, `base:ETH`, `arb:USDC`, `tron:USDT`.

To quote **and** pay from a wallet here in one step:

```bash
m zcash/bridge_send name=savings password=<pw> to_asset=ETH amount=1 \
    recipient=0x...                     # dry run
m zcash/bridge_send ... broadcast=True  # reserves and pays
```

Bridging *into* ZEC works the same way with `from_asset` — you fund the
returned deposit address from your wallet on the origin chain.

EVM recipients are checked against the EIP-55 checksum before anything is
reserved, so a mistyped address fails locally rather than eating the deposit.

**Routes:** NEAR Intents (primary, ~2 min, no API key) and Maya Protocol via
the native `ZEC.ZEC` pool. Maya halts periodically; `bridge_maya` reports its
real state rather than failing opaquely.

## Private bridging

The two directions are not symmetric, and the module keeps them apart rather
than hiding the difference behind a flag.

```bash
m zcash/bridge_shielded_plan     # what works here, what it needs, what it leaks
```

### In — works, and lands encrypted

The 1Click router accepts a **ZIP-316 unified address** as the ZEC recipient
and rejects a bare `zs1`. A unified address whose only receiver is shielded
leaves the solver no transparent option, so the payment arrives as a Sapling
note: no transparent hop, no second transaction, and nothing for this module to
prove.

```bash
m zcash/bridge_shielded_in from_asset=eth:USDC amount=250 \
    recipient=zs1... refund_to=0x...            # quote, reserves nothing
m zcash/bridge_shielded_in ... reserve=True     # real deposit address
```

`recipient` takes a `zs1` or a `u1`; `name=<wallet>` uses that wallet's own.
`refund_to` is on the **origin** chain — a refund is paid by the solver, and no
solver can pay into the shielded pool.

The address you give is never passed through untouched. `bridge_shielded_address`
shows what actually goes to the router:

* a bare `zs1` is wrapped into a unified address (same receiver, new envelope);
* a unified address carrying a **transparent** receiver is re-encoded without
  it — ZIP-316 lets the sender pick any receiver it supports, and a solver
  offered a cheap transparent one will take it. The wallet's default `u1` is
  exactly that shape, so this is the common case, not the corner case;
* a receiver in a pool this module **cannot decrypt** is removed too. Funds
  paid there would be real, confirmed and invisible to every balance shown
  here. Which pools count is read from `capabilities()`, so the rule follows
  the code rather than being hardcoded.

The response says what was removed and why, every time.

### Out — works, but cannot be private

The solver's deposit address is an ordinary t-address. Value has to become
transparent to leave Zcash at all, so the amount is public at that moment and
links to the destination address by timing. The spend still hides *which* notes
paid. If that link matters: unshield to a fresh t-address, wait, then bridge
from there as an ordinary transparent bridge.

Mechanically it also needs a Groth16 proof, which this module cannot produce:

```bash
m zcash/bridge_shielded_out name=savings password=<pw> \
    to_asset=ETH amount=0.5 recipient=0x...       # dry run
m zcash/bridge_shielded_out ... broadcast=True
```

* With `ZCASH_RPC_URL` set, the node proves and sends — one step.
* Without one, it still **reserves the deposit address** and returns the exact
  payment to make (from address, to address, amount, deadline) plus the steps
  to make it from Zashi/Ywallet/zingo with the key from `shielded_export`. That
  is a completable swap, not a failure, and the response says so.

Both directions carry a `privacy` block naming what is hidden, what is still
visible, and what to do differently. The inbound one is graded `good` and still
lists what leaks.

## Learning, and the agent

Someone who does not know what a shielded pool is cannot use this module
safely — they can lose money by pasting the right address into the wrong box.
So the explanation ships with the code, and it is not gated: reads are open,
and gating the explanation behind the token that the explanation explains is
how people end up guessing.

```bash
m zcash/learn                              # 11 lessons, ~35 minutes
m zcash/learn path=beginner                # a reading order
m zcash/learn topic=private-bridging       # one lesson in full
m zcash/learn glossary=True                # 47 terms
m zcash/explain term=zaddr                 # understands how beginners type
```

Lessons are hand-written to match what this module actually does, and the test
suite pins that: every cross-reference resolves, and no sentence claims
something can spend shielded ZEC without naming what that needs.

```bash
m zcash/ask question="how do I bridge USDC into a shielded address?"
```

`ask` recognises the questions people actually ask, answers from the lessons,
calls **read-only** functions to ground the answer in live data, and returns
`actions` — the exact calls to make next. It never calls anything that spends,
deletes or reveals a secret; those come back as actions to run deliberately.
The grounding allowlist is asserted in tests.

It needs no language model. Set `ZCASH_LLM_URL` (any OpenAI-compatible
`/chat/completions` endpoint) plus `ZCASH_LLM_KEY` and a model writes the prose
over the same sources — but the citations and the suggested calls still come
from the written corpus, so a hallucinated function name cannot reach the user
as a button. Without a model configured it answers from the lessons directly,
which is the default and works offline. `agent_status` says which is running.

## Shielded

The Sapling pool is implemented for real, in pure Python: ZIP-32 key
derivation, `zs1` and ZIP-316 unified addresses, note decryption with the
incoming and outgoing viewing keys, Pedersen note commitments and nullifiers.

```bash
m zcash/shielded_address    name=savings                       # zs1… and u1…
m zcash/shielded_new_address name=savings password=<pw>        # fresh slot
m zcash/shielded_scan       name=savings password=<pw> blocks=2000
m zcash/shielded_scan_tx    txid=<txid> name=savings password=<pw>
m zcash/shielded_export     name=savings password=<pw>         # keys, to spend elsewhere
```

A wallet's shielded account comes from the **same mnemonic** as its transparent
addresses (`m/32'/133'/account'`), so the seed you already wrote down restores
both pools, and the `zs1` address this module prints is the same one Zashi,
Ywallet or `zcashd` derive from those words.

### Sending shielded ZEC

A shielded spend carries a zk-SNARK proof — Groth16 for Sapling, Halo 2 for
Orchard — which is not something pure Python produces. So the proving is done
by a **local light client**, built once from source and driven by
`zcash/lightclient.py`. It is not a full node: it syncs compact blocks from a
lightwalletd server, keeps the note commitment trees itself, builds the proof
and broadcasts — the same trust model Zashi or Ywallet run on a phone.

```bash
m zcash/shielded_backend_install                       # once per host (~10 min)
m zcash/shielded_sync_start  name=savings password=<pw>   # returns immediately
m zcash/shielded_sync_status name=savings                 # poll until synced
m zcash/shielded_spendable   name=savings                 # what can be sent
m zcash/shielded_send name=savings password=<pw> to=zs1... amount=0.1
m zcash/shielded_send name=savings password=<pw> to=zs1... amount=0.1 broadcast=True
```

The light client is restored from the **same mnemonic** the rest of the module
holds, at the same ZIP-32 path, so it is literally the same account: the
unified address `shielded_address` prints and the one the light client derives
are byte-for-byte identical, and a note received at one is spendable by the
other.

| | |
|---|---|
| Receive to a `zs1` / `u1` address | ✅ |
| Read your notes: value, memo, sender's own sends | ✅ |
| Spend a note, or pay a shielded address | ✅ with the local prover, or a node |
| Shield transparent funds (`shielded_shield`) | ✅ with the local prover |

Three things are worth knowing before the first send:

* **The scan is the slow part, not the proof.** A wallet created here has a
  birthday at the current tip and syncs in seconds; a seed restored from years
  ago has millions of blocks to read. `shielded_sync_start` is a background
  job for exactly that reason, and `shielded_sync_status` reports a percentage.
* **The light client starts a hundred blocks below the birthday.** A spend is
  anchored to a commitment tree state several confirmations back, so a wallet
  whose scan begins exactly at the tip has no depth to anchor against and the
  prover refuses. The margin is automatic.
* **`shielded_send` is a dry run unless `broadcast=True`,** like everything
  else here. The dry run checks the address, the sync state and the spendable
  balance and prices the fee; it stops before the proof, because a proof that
  is not broadcast is a proof thrown away.

The `~/.mod/zcash/lightwallets/<name>/` directory holds the light client's own
copy of the seed, sealed by `zcash-devtool` to an `age` key — and that key is
in turn sealed with this module's AES-256-GCM under the wallet password, so
nothing on disk opens without it. It is written to a 0600 temporary file only
for the seconds a send needs it.

### Spending somewhere else instead

The seed opens this account in any Zcash wallet, so the export path still
works and needs no prover at all:

```bash
m zcash/shielded_export name=savings password=<pw>
# → secret-extended-key-main1…   import into Zashi / Ywallet / zingo / zcashd
# → zxviews1…                    watch-only export
```

A configured node also proves, and takes precedence when it holds the key:

```bash
export ZCASH_RPC_URL=http://127.0.0.1:8232
export ZCASH_RPC_USER=... ZCASH_RPC_PASSWORD=...
m zcash/shielded_node_import name=savings password=<pw>
m zcash/shielded_send name=savings password=<pw> to=zs1... amount=0.1 broadcast=True
```

Unified addresses from this module carry **both shielded receivers, Sapling and
Orchard, alongside a transparent one** — every pool advertised is a pool this
module can decrypt, which is the rule that matters: advertising one whose
payments we could not detect would lose funds. A unified address that publishes
a transparent receiver *can* be paid by `send`, transparently, and the response
says so — which is exactly why it is the wrong address to hand a bridge. See
[Private bridging](#private-bridging).

### Two things a scan cannot do without a node

* **Tell spent from unspent.** A note is spent when its nullifier appears on
  chain, and the nullifier depends on the note's position in the commitment
  tree. That position needs `z_gettreestate` from a node. Without one, `scan`
  reports *received* value and leaves `unspent_zec` as `null` — unknown, not
  zero.
* **Scan the whole chain cheaply.** Without a node the scan runs against the
  public explorer, which serves ~100 transactions (ciphertexts included) per
  request. Fine for days of chain, not for years — which is why a wallet
  records its **birthday** height when it is created, and why `wallet_restore`
  takes `birthday=` for an older seed.

### Why the shielded code can be trusted

The same standard as the signer: every primitive is pinned to the official
[zcash-test-vectors](https://github.com/zcash/zcash-test-vectors) fixtures,
copied into `tests/vectors/`.

* Jubjub generators, group hash and point encoding — 10/10 vectors;
* key components: `ask`, `nsk`, `ovk`, `ak`, `nk`, `ivk`, the default
  diversifier's `pk_d`, the Pedersen **note commitment** and the **nullifier**;
* **note decryption** with both the incoming and the outgoing viewing key, on
  the official note-encryption vectors;
* 60 unified-address vectors, and 27 Sapling addresses derived from a seed
  through ZIP-32 and FF1 diversifiers;
* a real mainnet Sapling transaction parses byte-exactly, and a stranger's
  notes do **not** decrypt.

One trap is worth naming: the public explorer serves 32-byte shielded fields
**reversed** (the way it prints txids) while leaving the ciphertexts alone.
Get that wrong and nothing fails — the scan simply finds no notes and reports
"none received". The scanner undoes the reversal and checks the first ephemeral
key of each transaction is in the prime-order subgroup, so a changed byte order
raises instead of quietly losing your money.

## Running it

```bash
m zcash/serve       # REST API on :8930 + web app on :50149/zcash
m zcash/status      # says which of the three ports is actually answering
```

Two HTTP surfaces exist deliberately:

* **:50148** — the mod-protocol server, the fleet's front door. Every function
  needs owner auth through the shared gate.
* **:8930** — the local REST API the web app talks to, bound to loopback.
  Reads are open; anything that can move funds or reveal a seed needs the
  bearer token from `~/.mod/zcash/server.secret` (`m zcash/token`).

The web app asks for that token once and keeps it in browser storage.

The app never talks to :8930 from the browser. `${basePath}/api/<fn>` is a Next
route handler that forwards to it server-side, and if :8930 is not answering it
starts the backend (`pm2 restart zcash-api`, or `api.py` directly when there is
no pm2 entry) and waits before retrying. Opening the page is enough to bring the
module up; a page that still cannot reach a backend says so and offers a retry
instead of rendering a blank explorer.

## Configuration

| variable | purpose |
|---|---|
| `ZCASH_RPC_URL` / `_USER` / `_PASSWORD` | use a zcashd/zebrad node |
| `BLOCKCHAIR_API_KEY` | raises the public rate limit |
| `ZCASH_WALLET_DIR` | where wallets are stored |
| `ZCASH_STATE_DIR` | where the API token lives |
| `ZCASH_LLM_URL` / `_KEY` / `_MODEL` | optional model for `ask`; without it the agent answers from the written lessons |

## Tests

```bash
python3 -m pytest tests/ -q                 # includes live network checks
python3 -m pytest tests/ -q -m "not live"   # offline only
```
