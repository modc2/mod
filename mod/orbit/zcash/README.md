# zcash

Explorer, wallet and cross-chain bridge for Zcash — transparent sends, and a
real Sapling shielded account.

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
| **Shielded** | Sapling keys, `zs1` + unified addresses, note decryption |
| **Bridge** | ZEC ⇄ Ethereum, Base, Arbitrum, Solana, BTC, Tron and ~30 more |

Shielded ZEC can be **received and read** here but not **sent** — see
[Shielded](#shielded) below for exactly where the line is and why.

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

### Where the line is

| | |
|---|---|
| Receive to a `zs1` / `u1` address | ✅ |
| Read your notes: value, memo, sender's own sends | ✅ |
| Spend a note, or pay a shielded address | ❌ needs a Groth16 proof |
| Orchard, in either direction | ❌ not implemented |

Creating a shielded output or spending a note requires a zk-SNARK proof, which
is not feasible in pure Python. The module does not pretend otherwise — it
hands you the key instead:

```bash
m zcash/shielded_export name=savings password=<pw>
# → secret-extended-key-main1…   import into Zashi / Ywallet / zingo / zcashd
# → zxviews1…                    watch-only export
```

With a node configured, the node does the proving:

```bash
export ZCASH_RPC_URL=http://127.0.0.1:8232
export ZCASH_RPC_USER=... ZCASH_RPC_PASSWORD=...
m zcash/shielded_node_import name=savings password=<pw>
m zcash/shielded_send name=savings password=<pw> to=zs1... amount=0.1 broadcast=True
```

Unified addresses from this module carry a **Sapling receiver and a transparent
one, never Orchard** — advertising a pool whose payments we cannot detect would
lose funds. A unified address that publishes a transparent receiver *can* be
paid by `send`, transparently, and the response says so.

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

## Tests

```bash
python3 -m pytest tests/ -q                 # includes live network checks
python3 -m pytest tests/ -q -m "not live"   # offline only
```
