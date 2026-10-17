# zcash

Explorer, wallet (transparent + Sapling shielded) and cross-chain bridge for
Zcash.

## Orientation

- `zcash/keys.py` — BIP39, BIP32, t-address encoding, secp256k1 signing,
  shielded address decoding
- `zcash/tx.py` — v5 (ZIP-225) transactions, ZIP-244 digests, ZIP-317 fees
- `zcash/jubjub.py` — the Jubjub curve, group hash, Pedersen hashes
- `zcash/sapling.py` — ZIP-32 keys, FF1 diversifiers, bech32/bech32m, F4Jumble,
  unified addresses, note encryption and decryption
- `zcash/bundles.py` — reading Sapling bundles out of v4/v5 transactions and
  out of explorer rows
- `zcash/shielded.py` — the wallet-facing layer: derive, scan, summarize, node
- `zcash/lightclient.py` — the proving backend: drives a locally built
  `zcash-devtool` light client so shielded sends actually work
- `zcash/chain.py` — UTXOs, tip, consensus branch id, broadcast, shielded rows
- `zcash/wallet.py` — encrypted wallet files in `~/.mod/zcash/wallets/`
- `zcash/bridge.py` — NEAR Intents + Maya routes, and the shielded-recipient
  rewrite that makes bridging *into* the pool work
- `zcash/learn.py` — the written lessons and glossary (hand-written, not generated)
- `zcash/agent.py` — `ask`: intent matching, retrieval, grounding, optional LLM
- `zcash/mod.py` — the `Mod` class exposed to the fleet
- `api.py` — loopback REST API backing the web app
- `app/` — Next.js front end (explorer / learn / ask / wallet / shielded /
  send / bridge / private / mcp). `learn.tsx` and `private.tsx` are separate
  files from `page.tsx` deliberately — several sessions edit this app at once.

## Things that will bite you

**Shielded sending is a three-rung ladder, and each rung fails differently.**
A spend needs a zk-SNARK proof, so: the prover has to be built on this host
(`shielded_backend_install` → `~/.mod/zcash/bin/zcash-devtool`), the wallet's
light client has to have scanned to the tip (`shielded_sync_start`, poll
`shielded_sync_status`), and only then does `shielded_send` work. Every
failure names the missing rung rather than erroring out of a subprocess — if
someone reports "shielded send is broken", read `error` first, it says which.

**`scan_queue.priority` is a label, not a to-do flag.** A scanned range keeps
priority 10 (`ScanPriority::Scanned`) forever, so "blocks left to scan" is
`priority > 10`, not `priority > 0`. Reading it wrong makes a fully synced
wallet look permanently behind and blocks every send. Pinned in
`tests/test_lightclient.py`.

**The light client's birthday is 100 blocks below the wallet's.** A spend is
anchored several confirmations back, so a scan starting exactly at the tip has
nothing to anchor to and the prover says "Must scan blocks first" — which
reads like a bug and is an off-by-a-few-blocks. `BIRTHDAY_MARGIN` handles it.

**Spending functions are dry runs by default.** `send` and `bridge_send` build
and sign but do not submit unless `broadcast=True`. If someone reports "it
didn't send", check that flag before anything else — the response always states
`mode: DRY RUN` or `BROADCAST`.

**A unified address with a transparent receiver is a transparent
destination.** ZIP-316 lets the sender pick any receiver it supports, and a
solver offered a cheap transparent one takes it. The wallet's own `u1` carries
Sapling *and* P2PKH, so handing it to a bridge unchanged would very likely be
paid in the clear. `bridge.shielded_recipient()` re-encodes it without the
transparent receiver before the router ever sees it. Never bypass that by
passing an address straight to `bridge.quote()` and calling the result private.

**Whether the router pays a shielded address at all is the router's call.**
It has answered both ways: `u1` accepted and `zs1` rejected (201 vs 400
`recipient is not valid`), and — since 2026-09-02 — `recipient is not valid`
for both, while the same swap quotes fine to a `t1`. `shielded_quote()` raises
`ShieldedRouteUnavailable` for exactly that answer, and `bridge_shielded_in`
turns it into the transparent-then-shield fallback: nothing reserved, both legs
labelled, `shielded: false`. Never catch it as a plain `BridgeError` and
report "bad address" — the address is fine. `tests/test_learn_bridge.py` pins
this live and fails loudly when the router changes its mind again.

**Which shielded pools are safe to advertise comes from `capabilities()`.**
`mod._readable_pools()` reads it, and any receiver in a pool the module cannot
decrypt is stripped from a bridge recipient. A payment into an unreadable pool
is real, confirmed and invisible to every balance shown here — worse than a
refusal. This is deliberately capability-driven: when Orchard reading lands,
bridging into Orchard starts working with no edit to the bridge.

**Bridging out of shielded without a node is a `RESERVED` state, not a
failure.** It reserves the deposit address and returns `manual_payment` plus
`how` — the user completes the spend in a proving wallet. Do not "fix" this
into an error; the swap is live and completable.

**`ask` grounds by calling functions.** `agent.GROUNDING_FNS` is the allowlist
and it is asserted in tests to exclude everything that spends. Adding a
function to an intent's `ground` list without adding it there fails the call,
by design. Suggested `actions` are checked against the live module before they
go out, so a renamed function drops the button instead of shipping a broken one.

**Lessons are load-bearing text.** `test_lessons_do_not_promise_shielded_spending`
fails the suite if a lesson claims something can spend shielded ZEC without
naming what that needs. If you edit `learn.py`, keep the qualifier in the same
sentence as the claim.

**The consensus branch id must come from the chain.** It changes at every
network upgrade and is committed to by every signature. `chain.consensus_branch_id()`
reads it from a recent v5 transaction header. Do not replace this with a
constant; `FALLBACK_BRANCH_ID` exists only for when discovery fails.

**Blockchair's `limit` is a `transactions,utxos` pair.** Passing `limit=0`
returns an empty utxo array, which looks exactly like a zero balance. Use
`"0,1000"`. This already caused one silent bug.

**Blockchair reports a negative `circulation` for Zcash** (shielded-pool
accounting). Do not surface it as supply — `_supply()` in `mod.py` sanity-checks
it against the 21M cap and falls back to the supply implied by
`market_cap_usd / market_price_usd`, labelling which one it used in
`circulation_source`.

**`fns` exposure comes from `config.json`, not from `Mod.fns`.** Adding a
method to the class does not expose it; add it to `config.json` too.

**The fleet gate auth-gates everything not in its global `PUBLIC_FNS`.** That is
why the app talks to `api.py` on :8930 rather than the mod-protocol port :50148.
Reads are open there; spending needs the bearer token from
`~/.mod/zcash/server.secret`.

**A stopped `api.py` used to render the whole app as "Internal Server Error"**,
because `${basePath}/api/*` was a next.config rewrite pointing at a dead port.
It is now a route handler (`src/app/api/[fn]/route.ts`) that starts the backend
and retries, so the module heals on page load. Keep the ownership rule: it asks
pm2 (`zcash-api`) first and only spawns `api.py` itself when there is no pm2
entry — two owners racing for :8930 crash-loop each other.

**Rebuilding the Next app under a live `next start` breaks it** — the served
HTML references chunk hashes the running server will not serve. Use
`app/build.sh` (staged build + swap) and restart the app process after it.

## Verifying the signer

A wrong signature produces a transaction the network rejects, so changes to
`tx.py` or `keys.py` must be checked against the chain:

```bash
python3 -m pytest tests/ -q          # includes the mainnet consensus test
```

`test_mainnet_transaction_reproduces_txid_and_signature` re-serializes a mined
v5 transaction byte for byte, reproduces its ZIP-244 txid, and verifies its
real signature against our sighash. If that test fails, do not ship.

## Shielded

Sapling is implemented: keys, addresses, note decryption, commitments,
nullifiers. Orchard is not. **Spending is still impossible** — a Sapling spend
or output needs a Groth16 proof, which is not feasible in pure Python. Do not
add code that appears to create one. `shielded_export` hands the extended
spending key to a proving wallet; `ZCASH_RPC_URL` hands the proving to a node.

```bash
python3 -m pytest tests/test_shielded.py -q
```

Every primitive is pinned to the official zcash-test-vectors fixtures in
`tests/vectors/`. If those fail, the keys are wrong — an address whose payments
the wallet can never find is worse than no address at all. Do not ship.

**The explorer reverses 32-byte shielded fields.** Blockchair prints `cv`,
`cmu`, `ephemeralKey` and `nullifier` in display order (reversed) but leaves
`encCiphertext` and `outCiphertext` alone. Undo it with
`bundles.explorer_hash()`. This matters more than it looks: a reversed
ephemeral key does not raise, it just decrypts nothing, and the scan reports
"no notes received" for a wallet that has money. That is why
`outputs_from_explorer` runs a prime-order **subgroup check** on the first
output of every transaction — random or misordered bytes clear the cheap
on-curve test about a quarter of the time, but the subgroup test only one time
in sixteen.

**Half of all diversifier indices are unusable.** `FullViewingKey.address_at()`
walks forward and returns the index it actually landed on; bookkeeping must
advance past *that*, or `shielded_new_address` hands out the same address twice.

**A shielded scan needs the password, unlike a transparent balance.** An
incoming viewing key reveals every payment the account ever received, so it
stays inside the encrypted blob; only the addresses themselves are plaintext.

**Spend detection needs a node.** Nullifiers depend on a note's position in the
commitment tree, which comes from `z_gettreestate`. Without it `unspent_zec` is
`null`, never `0` — and `summarize(positions_known=…)` decides that from the
scan, not from whether any note happened to have a nullifier.

**Transaction versions newer than v5 are not guessed at.** `bundles.parse()`
returns `layout: "unknown"` for an unrecognised version group id and the scan
says so out loud (`unreadable_transactions`), rather than reporting zero notes
from a bundle it could not read.
