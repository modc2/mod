# monero

Explorer, encrypted wallet, local view-key scanner, spending and swaps for Monero.

```
m monero/capabilities         # what works, what needs help, what is refused
m monero/test                 # self-test: primitives, seeds, scanner, network
```

## What it does

| | |
|---|---|
| **Explorer** | blocks, transactions, mempool, fees, ring members, price, supply |
| **Keys** | 25-word seed phrases, standard / sub / integrated addresses — pure Python |
| **Wallet** | full or view-only, AES-256-GCM at rest |
| **Scan** | finds your own outputs with your view key, **locally** |
| **Send** | builds, previews and relays real transactions via `monero-wallet-rpc` |
| **Swap** | XMR ⇄ 630 assets through a custodial provider |

Two facts about Monero shape all of it, and this module states both rather than
working around them quietly.

**There is no address balance.** Nothing on chain links an address to a
transaction. Finding your own money means testing every output in every block
against your view key. `wallet_scan` does that here, in Python, and the key
never leaves the host.

**Building a spend is not something to improvise.** A transaction needs a CLSAG
signature over a 16-member ring and a Bulletproofs+ range proof. This module
does not reimplement those — see [Sending](#sending).

## Wallets

```bash
m monero/wallet_create name=savings password=<pw>      # prints the seed ONCE
m monero/wallet_restore name=old password=<pw> seed_phrase="word word ..."
m monero/wallet_watch name=cold password=<pw> \
    address=4... view_secret_key=<64 hex>              # view-only
m monero/wallet_new_address name=savings password=<pw> # a subaddress
m monero/wallet_info name=savings
```

Wallets live in `~/.mod/monero/wallets/<name>.json`, never in the repo. The
address and subaddresses sit in the clear so you can show a receive address
without a password; the seed phrase, spend key **and view key** are sealed with
AES-256-GCM behind PBKDF2-SHA256 (600k iterations).

The view key is encrypted deliberately. On a transparent chain a viewing key
would be public information; in Monero it reveals every payment you have ever
received, so scanning asks for a password.

Take each payment on a fresh subaddress. Subaddresses are unlinkable to each
other and to the main address; reusing one address for everything is the only
real privacy mistake a receiver can make. Integrated addresses
(`wallet_integrated`) exist for payers who can only be given one address, but
they reveal the base address they were built from.

## Scanning

```bash
m monero/wallet_scan name=savings password=<pw> start_height=3740000 blocks=50
```

Bounded on purpose. Every window reports its own rate and where to resume:

```json
{"blocks_scanned": 50, "transactions_scanned": 812, "outputs_found": 0,
 "blocks_per_second": 0.33, "next_start_height": 3740050,
 "caveat": "This is what arrived, not what is left..."}
```

Roughly 0.3 blocks/second through a public node — network-bound, not
CPU-bound, so your own `monerod` on the same host is far faster. Set a restore
height (`wallet_restore_height`) so scanning never starts at block 0.

Two limits worth being clear about:

* a scan finds what **arrived**. A view key cannot tell whether an output has
  since been spent — that needs key images, which need the private spend key
  and the hash-to-point map. `balance` reads the true spendable amount from
  `monero-wallet-rpc`, which holds them;
* subaddresses have to be derived before they can be matched, so
  `subaddresses=N` sets how many are watched.

### Why the scanner can be trusted

A scanner that never matches anything looks exactly like a wallet with no
funds, so "it ran" proves nothing. `m monero/test` plays the sender: it builds
an output the way a real sender does — `R = rG`, the shared secret from the
sender's side, the masked amount, the view tag — and requires the scanner to
recover it from the receiver's side, to the main address and to a subaddress,
while ignoring an output built for somebody else.

The primitives underneath are pinned the same way. Keccak-256 matches its
published vector, the ed25519 base point matches its encoding, and the Monero
project's own donation address round-trips through base58 and its checksum —
a value we did not choose.

## Sending

`send` is a **dry run by default**. It builds and signs the real transaction
through `monero-wallet-rpc` and returns the exact fee, weight and hash without
relaying it.

```bash
# preview — real, signed, not relayed
m monero/send to=4... amount=0.1

# publish it
m monero/send to=4... amount=0.1 broadcast=True

# or relay the exact transaction that was previewed
m monero/send_confirm tx_metadata=<from the preview>

m monero/sweep to=4... broadcast=True     # everything unlocked
```

This needs a wallet RPC:

```bash
monero-wallet-rpc --wallet-file ~/wallets/mine \
    --rpc-bind-port 18083 --disable-rpc-login \
    --daemon-address node.example:18081

m monero/rpc_load_wallet name=savings password=<pw>   # hand it a wallet
m monero/balance
```

Monero payments are final once mined. There is no recall, and no explorer
lookup that will tell you where they went.

## Swapping

Monero cannot be bridged the way most assets can, and it is worth saying why
rather than shipping a bridge tab that fails opaquely. A bridge works because a
contract on the origin chain can observe a deposit; Monero has no contracts and
no public amounts. NEAR Intents lists 35 chains and Monero is not one of them;
Maya has no XMR pool.

What exists is an instant-swap provider taking custody for a few minutes:

```bash
m monero/bridge_routes                                  # including what does not work
m monero/bridge_quote to_asset=BTC amount=1
m monero/bridge_start to_asset=BTC amount=1 recipient=bc1... refund_to=4...
m monero/bridge_status order_id=...
```

Assets are `BTC`, `ETH`, or chain-qualified as `TRX:USDT`, `ETH:USDC`.
Recipients are checked against that chain's address rules locally, so a typo
fails here rather than after the deposit. Nothing here can spend from a wallet
on its own: `bridge_start` reserves a deposit address, and paying it is a
separate, explicit step.

## Running it

```bash
m monero/serve       # REST API on :8940 + web app on :50691/monero
m monero/status
```

Two HTTP surfaces exist deliberately:

* **:50690** — the mod-protocol server, the fleet's front door. Every function
  needs owner auth through the shared gate.
* **:8940** — the local REST API the web app talks to, bound to loopback.
  Explorer reads are open; anything that uses a key needs the bearer token from
  `~/.mod/monero/server.secret` (`m monero/token`).

The web app asks for that token once and keeps it in browser storage.

## Configuration

| variable | purpose |
|---|---|
| `MONERO_DAEMON_URL` | your own monerod (otherwise a public node is picked) |
| `MONERO_DAEMON_USER` / `_PASSWORD` | digest auth for that node |
| `MONERO_WALLET_RPC_URL` | monero-wallet-rpc, default `http://127.0.0.1:18083` |
| `MONERO_WALLET_RPC_USER` / `_PASSWORD` | its `--rpc-login` |
| `MONERO_WALLET_DIR` | where wallets are stored |
| `MONERO_STATE_DIR` | where the API token lives |

Without a node configured the module picks a public one and says so in
`node_info`. A public node cannot see what you found, but it does see which
blocks you asked for — which is most of a scan's metadata. Run your own for
anything that matters.

## Tests

```bash
python3 -m pytest tests/ -q                 # includes live network checks
python3 -m pytest tests/ -q -m "not live"   # offline only
```
