# id — working on this module

An identity is a **set of accounts** held together by signatures. The log is the
identity; everything else is a cache of it. Two commands orient you:

```
m id/demo                # the whole flow with throwaway keys, including the refusals
m id/audit id=id_…       # replay a log and re-check every signature in it
```

## Layout

```
src/crypto/     keccak, ripemd160, secp256k1, ed25519, base58, bech32 — written here
src/chains.py   one entry per chain: parse an address, build the digest, match the key
src/accounts.py the accounts with no key (github, x, dns, web) — publication proofs
src/statement.py the exact bytes that get signed, rendered deterministically
src/store.py    ~/.mod/id — the append-only logs and the caches over them
src/identity.py the rules: genesis, join, merge, unlink, name, audit, export
src/signers.py  throwaway wallets for every chain — used by the demo and the tests
src/api.py      FastAPI :50650 — transport only, no rules live here
src/app.py      the console :50651/id — drives MetaMask and Phantom in the browser
```

## Things that will bite you

**`hashlib.sha3_256` is not Keccak.** NIST changed the padding byte (0x01 → 0x06)
after Keccak was submitted, and Ethereum kept the original. Use
`src/crypto/keccak.py`. Every EVM address depends on it.

**`hashlib.new('ripemd160')` raises on this host.** OpenSSL 3 moved it to the
legacy provider. Every Bitcoin and Cosmos address needs it, so it is in
`src/crypto/ripemd160.py`.

**Signature encodings overlap.** An 88-character base58 Solana signature is also
valid base64 and decodes to 66 bytes of junk — silently. `chains.unhex()` takes
an `expect=(64,)` tuple for exactly this reason; always pass it.

**Adding a chain** means answering three questions in `chains.py` — `parse`
(canonical spelling), the digest (what the wallet actually hashes), `check`
(does the recovered or supplied key print as this address). Getting the digest
wrong does not fail loudly, it just never matches. Then add a signer in
`signers.py`, and the parametrised test covers it automatically:

```python
@pytest.mark.parametrize('chain', sorted(signers.MAKERS))
def test_every_chain_verifies_its_own_wallet_and_rejects_a_forgery(chain): ...
```

**The statement is a wire format.** `statement.render()` is fixed-width labels,
single spaces, `\n` endings, no alignment computed from values. Anything clever
is something a verifier on another host has to reproduce byte for byte. Changing
it invalidates every stored proof — bump the protocol string if you ever must.

**Never add a path that links an account without a signature.** Not for
convenience, not for an admin, not for tests (the tests use real throwaway keys).
The two-signature join rule is the module.

**The consent proof is stored in the event.** `authorized_by` is what makes the
log auditable offline — a session is a UX shortcut over it, not the record. If
you change how sessions work, keep the proof copying.

## Writing to the store

Every mutation goes through `identity.submit()`, which takes a burned nonce and a
verified proof. `_record()` appends; nothing else writes to a log. The audit
compares each event's header fields (`account`, `kind`, `address`, `strength`)
against the proof underneath it — a hand-edited log changes the summary and
leaves the signature alone, and that is exactly what gets caught.

## Testing

`store.sandbox()` is a context manager that repoints the whole store at a temp
directory. Every test uses it via an autouse fixture, and `m id/demo` uses it so
that watching the demo never touches a real identity.

Run: `m id/test` or `python3 -m pytest -q`. All 57 must pass; the parametrised
chain test alone is 11 of them.

## Ports and state

API 50650, console 50651 at `/id`, state `~/.mod/id`, `route: false` (flip it in
`config.json` and run `m caddy/apply` to publish it). Env: `ID_DIR`, `ID_PORT`,
`ID_APP_PORT`, `ID_HOST`.
