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
- `zcash/chain.py` — UTXOs, tip, consensus branch id, broadcast, shielded rows
- `zcash/wallet.py` — encrypted wallet files in `~/.mod/zcash/wallets/`
- `zcash/bridge.py` — NEAR Intents + Maya routes
- `zcash/mod.py` — the `Mod` class exposed to the fleet
- `api.py` — loopback REST API backing the web app
- `app/` — Next.js front end (explorer / wallet / shielded / send / bridge)

## Things that will bite you

**Spending functions are dry runs by default.** `send` and `bridge_send` build
and sign but do not submit unless `broadcast=True`. If someone reports "it
didn't send", check that flag before anything else — the response always states
`mode: DRY RUN` or `BROADCAST`.

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
