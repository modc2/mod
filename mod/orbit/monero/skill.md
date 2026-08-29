# monero

Explorer, encrypted wallet, local view-key scanner, spending and swaps for Monero.

## Orientation

- `monero/crypto.py` — Keccak-256, ed25519, CryptoNote base58, addresses, key derivation
- `monero/mnemonic.py` — the 25-word seed phrase (+ `english.txt`, the 1626-word list)
- `monero/daemon.py` — node RPC with public-node failover, xmrchain.net fallback
- `monero/scan.py` — view-key output scanning
- `monero/wallet.py` — encrypted wallet files in `~/.mod/monero/wallets/`
- `monero/walletrpc.py` — monero-wallet-rpc client (the only thing that can spend)
- `monero/bridge.py` — the one keyless XMR swap route
- `monero/mod.py` — the `Mod` class exposed to the fleet
- `api.py` — loopback REST API backing the web app
- `app/` — Next.js front end (explorer / wallet / scan / send / swap)

## Things that will bite you

**Keccak-256 is not SHA3-256.** `hashlib.sha3_256` uses a different padding
byte and produces a different digest. Every address checksum, every derivation
and every view tag in Monero uses original Keccak. `crypto.keccak256` is the
only correct one here; the test pins it to its published vector.

**Monero has no address balance, and no amount of API hunting will find one.**
If someone asks why `address` does not return a balance, that is the answer.
Finding funds means `wallet_scan`, which needs the view key and time.

**A scan reports what arrived, not what is left.** Spent detection needs key
images, which need the private spend key and the hash-to-point map
(`ge_fromfe_frombytes_vartime`). That map is not implemented, on purpose: it
cannot be validated against anything we have, and a wrong one reports a wrong
balance in silence. `balance` reads the true figure from monero-wallet-rpc,
which holds the key images. Do not "fix" the scanner by guessing at Hp.

**This module does not build transactions and should not start.** A spend needs
CLSAG over a 16-member ring plus Bulletproofs+. `send`/`sweep` drive
monero-wallet-rpc. If it is not running, `send` returns the exact command to
start one — that error is the feature, not a gap to paper over.

**Spending functions are dry runs by default.** `send` and `sweep` build and
sign but pass `do_not_relay` unless `broadcast=True`. If someone reports "it
didn't send", check that flag first — the response always states
`mode: DRY RUN` or `BROADCAST`. `send_confirm(tx_metadata=...)` relays the
*exact* previewed transaction rather than building a second one.

**Subaddresses must be derived before they can be recognised.** The scanner
matches by recovering the candidate spend key and looking it up in a table, so
`subaddresses=N` bounds how many are watched. An output paid to subaddress
0/9 will be missed if only 5 are watched. This is not a bug in the scan; there
is no way to test an unbounded range.

**The private view key is encrypted at rest.** In Monero it is not public
information — it reveals every payment ever received — so `wallet_scan` needs
the wallet password. Do not move it to the plaintext half to make scanning
password-free.

**`get_info` on restricted public nodes blanks `hard_fork_version`.**
`daemon.info()` falls back to `hard_fork_info`. Similarly `get_coinbase_tx_sum`
is disabled on every public node, which is why `supply()` comes from CoinGecko
and says so — the explorer's `/emission` endpoint counts only the blocks that
explorer indexed and is wrong by millions of XMR.

**`fns` exposure comes from `config.json`, not from `Mod.fns`.** Adding a method
to the class does not expose it; add it to both. A test asserts they match.

**The fleet gate auth-gates everything not in its global `PUBLIC_FNS`.** That is
why the app talks to `api.py` on :8940 rather than the mod-protocol port :50690.
Explorer reads are open there; anything using a key needs the bearer token from
`~/.mod/monero/server.secret`. Note `wallet_scan` is in `GUARDED_FNS` even
though it only reads the chain — it uses a view key.

**Scanning is network-bound, not CPU-bound.** Roughly 0.3 blocks/second through
a public node, and one `get_block` plus one batched `get_transactions` per
block. If you speed anything up, speed up the round trips, not the maths. The
crypto is already ~2 ms per transaction thanks to view tags and the windowed
scalar multiplication.

## Verifying the crypto

A scanner that matches nothing looks exactly like a wallet with no funds, so
"it ran" proves nothing. Changes to `crypto.py` or `scan.py` must be checked:

```bash
python3 -m pytest tests/ -q          # 29 offline, 6 live
m monero/test                        # the same checks, as a fleet function
```

`test_scanner_finds_a_payment_to_the_main_address` and its subaddress twin
construct outputs the way a *sender* does — `R = rG`, the shared secret from
the sender's side, the masked amount, the view tag — and require the scanner to
recover them from the receiver's side. `test_donation_address_decodes_and_round_trips`
pins base58 and the Keccak checksum against the Monero project's own donation
address. If those fail, do not ship.

## Bridging

There is no trustless bridge for Monero and there cannot be one: a bridge
contract has to observe a deposit, and Monero has neither contracts nor public
amounts. NEAR Intents (used by `zcash`) does not list XMR; Maya has no XMR
pool. `bridge.py` uses Exolix, a custodial instant exchange, and every response
carries `custodial: true`. Do not present it as a bridge in UI copy.
