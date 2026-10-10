# id

**One identity, made of many accounts.** An Ethereum wallet, a Solana wallet, a
Bitcoin address, a Cosmos key, a GitHub login — each proves itself by signing a
statement that names the identity it is joining, and the set of them becomes a
single `id`.

There is no account table, no password, and no login. The identity *is* an
append-only log of signatures, and every claim this module makes is a replay of
that log.

```
m id/demo                                   the whole flow, with keys made on the spot
m id/challenge chain=eth address=0x…        the exact text to sign
m id/submit nonce=… signature=0x…           hand back the signature
m id/whois address=solana:9xQe…             which identity is this, and what else is in it
m id/audit id=id_…                          re-check every signature in the log, offline
m id/serve                                  API :50650 + console :50651
```

---

## What an identity is

```
id_6738702e03c3fcef  "demo"
  ├ ethereum:0x0c542c87ece203e82820a885906065de87f5b5e8   root · key
  ├ solana:Fkk3WwHsNnNgdsnpUecEk4Kxq1Wfdohw4hvXQ1FhKHjh   let in by 0x0c54… · key
  ├ bitcoin:bc1ql2xcga7gcyzvmj3cgk368sz39sv0xr9aa4pv4t    let in by 0x0c54… · key
  └ github:octocat                                        let in by 0x0c54… · publication
```

Underneath is `~/.mod/id/ids/id_6738702e03c3fcef.jsonl` — one signed event per
line, in the order they happened. Delete the index and nothing is lost
(`m id/rebuild` recomputes it from the logs, which is the proof that the index
is only a cache). Copy the log to another machine and it still verifies, because
every event carries the statement that was signed and the signature over it.

## The rule that matters

Proving you hold a key is easy. The hard question is *who may add an account to
someone else's identity* — and if a valid signature were enough, anyone could
attach their wallet to yours and stand next to you in it.

So a join takes **two signatures**:

| | signs | why |
|---|---|---|
| the joining account | its own key | proves it holds the key |
| a current member | its own key | proves the identity consents |

The second one is what a *session* is. Proving control of a member account mints
a session that lasts an hour, and the signature that minted it is **copied into
every event that session authorises**. The log therefore holds the whole chain of
consent — *B joined, B signed for itself, A signed to allow it, A was a member at
the time* — and `audit` walks all of it offline.

Genesis is the exception, and only because the first account of a new identity
has nobody to ask.

The rest follows from the same idea:

- **merge** — two identities that both exist become one when a member of *each*
  signs the same pair. The order is fixed by age, so both sides sign identical
  words, and neither can be absorbed quietly. The older name survives; the other
  one still resolves to it.
- **leave** — your own signature is always enough to remove yourself. Being in
  somebody's identity is not a thing you should need permission to stop.
- **evict** — removing *someone else* takes the root account, the one that
  created the identity.
- **nothing is deleted** — unlinking appends an event, merging leaves the
  absorbed log where it is. The history is the evidence.

## Two kinds of proof, and the difference is stated everywhere

**`key`** — a signature. Re-checkable offline, by anyone, forever. This is what
every wallet gives.

**`publication`** — a token published where only the holder can write: a public
gist, a post, a DNS TXT record, a page. A GitHub account has no key to sign
with, so this is the only thing available — and it is weaker in two specific
ways the console says out loud: it can be undone by deleting the thing, and it
proves control *at the moment of fetching* and no later. `audit live=true`
re-fetches it; a key proof needs no network at all.

## The chains

Eleven of them, across four different ideas of what a wallet signs:

| | curve | what actually gets hashed |
|---|---|---|
| **Ethereum** + every EVM chain | secp256k1 | EIP-191 `\x19Ethereum Signed Message:\n` + len + text, keccak256, key **recovered** from the signature |
| **Bitcoin**, Litecoin, Dogecoin | secp256k1 | `\x18Bitcoin Signed Message:\n`, double-SHA256, key recovered from the 65-byte header+r+s blob |
| **Cosmos** (Hub, Osmosis, Celestia, …) | secp256k1 | ADR-036 Amino JSON sign document — the public key must be sent, because this signature is not recoverable |
| **Tron** | secp256k1 | TIP-191, same shape as Ethereum, base58check address |
| **Solana** | ed25519 | the statement bytes, unmodified — the address *is* the public key |
| **Sui** | ed25519 | `blake2b256(intent ‖ uleb128(len) ‖ text)` — signPersonalMessage |
| **Aptos** | ed25519 | the statement bytes; address is `sha3_256(key ‖ 0x00)` |
| **NEAR** | ed25519 | the statement bytes |
| **Polkadot / Kusama / Bittensor** | ed25519 | the statement, raw or `<Bytes>`-wrapped as the extension sends it |

One EVM address is the same 20 bytes on Ethereum, Base, Arbitrum, Optimism,
Polygon and everywhere else, so it is recorded **once**, not once per network. A
Cosmos key prints under every chain prefix, so the equivalents are reported to
stop the same key being linked twice by accident. Bitcoin verifies legacy
(`1…`), nested segwit (`3…`) and native segwit (`bc1q…`) by deriving all three
forms from the recovered key and matching whichever one you gave.

Two honest gaps, refused with a reason rather than fudged:

- **taproot** (`bc1p…`) — message signing for it (BIP-322) is not widely shipped.
- **sr25519** — the Polkadot-JS default. Verifying it needs Schnorrkel, which no
  pure-Python implementation does correctly. Ed25519 Substrate accounts work.

## The crypto is written here

Keccak-256, RIPEMD-160, secp256k1 (recover / verify / sign), Ed25519, Base58,
Base58Check, Bech32, Bech32m and SS58 are all implemented in `src/crypto/`.

That is not reinvention for its own sake. Verification is the one thing this
module cannot be wrong about, and two things follow from writing it out:

- `hashlib` cannot help. `sha3_256` is **not** Keccak (NIST changed the padding
  byte after submission, and Ethereum kept the original), and OpenSSL 3 moved
  RIPEMD-160 into the legacy provider, so `hashlib.new('ripemd160')` raises on
  most modern hosts — while every Bitcoin and Cosmos address needs it.
- An exported identity can be re-checked on any host with a Python interpreter,
  no network, and nothing installed — years after the wallet SDK that produced
  the signature has gone.

The test suite pins every primitive against the reference implementation
(`eth_hash`, `eth_keys`, `eth_account`, `pynacl`) and against the published
BIP-173 / BIP-350 / RFC-8032 vectors wherever those are present, so the suite is
not just this module agreeing with itself.

## Try it without a wallet

```
m id/demo
```

Makes an Ethereum, a Solana, a Bitcoin and a Cosmos key on the spot, runs the
entire flow, and deletes the directory afterwards. It is not a mock — the
signatures are real and go through exactly the code a browser wallet drives. Two
of the fifteen steps are *failures*, on purpose: an uninvited wallet with a
perfectly valid signature is refused, and a replayed nonce is refused. The last
step edits the log by hand and shows the audit catching it:

```
15 someone edits the log by hand to insert an account
   ok: false
   caught: event says account='ethereum:0x7629…', but the signature
           underneath it is for 'solana:7K7b…'
```

## The console

```
m id/serve          # API :50650, console http://localhost:50651/id
```

MetaMask and Phantom are driven straight from the page — the statement the API
returns is handed to `personal_sign` and to Phantom's `signMessage` unmodified.
Every other chain is a paste: the statement is displayed, you sign it wherever
the key lives (Electrum, Sparrow, Keplr, a hardware wallet, an air-gapped
machine), and you paste the result back.

No key ever enters the page, and the page never asks for one. The only thing it
holds is a session token — which is another account's *consent*, not a
credential of yours — and it expires in an hour.

## Moving an identity

```
m id/export id=id_… path=me.json     # every proof, no secret
m id/load path=me.json               # re-verifies all of it before writing a byte
```

`load` refuses the whole document if a single proof in it does not check out. An
identity is portable because it is self-verifying, not because a server vouches
for it.

## The API

```
POST /challenge  {chain, address, op, id, other, name, ttl}   the exact text to sign
POST /submit     {nonce, signature, pubkey, source, session}  the proof
POST /verify     {chain, address, message, signature}         one-shot, stores nothing
GET  /whois?account=solana:9xQe…                              who is this
GET  /id/{id} | /id/{id}/log | /id/{id}/audit?live=
GET  /ids | /chains | /services | /merge?id=&other=
GET  /export/{id} | POST /import  (loopback)
GET  /demo
```

`/import` and `/rebuild` are loopback-only. There is no endpoint that returns a
private key, because none is ever held, and no endpoint that links an account
without a signature, because that would make everything above decorative.

## State

`~/.mod/id/`, never the checkout:

```
ids/<id>.jsonl     the identity — append-only, one signed event per line
index.json         address → identity, and merged-away → survivor.   a cache
sessions.json      live consents, hashed, an hour each.              a cache
challenges.json    outstanding nonces, burned on use.                a cache
```

Public keys, addresses, statements and signatures — all of it things the holder
published on purpose. There is no private key here and nowhere to put one.

## Tests

```
m id/test        # or: python3 -m pytest -q
```

57 tests. The primitives against outside implementations and published vectors;
then every rule with its **refusal** tested, not only its success — a join
without consent, a session used on the wrong identity, an account in two
identities, a one-sided merge, a non-root eviction, a burned nonce, a nonce that
survives a failed attempt, and four different ways of editing the log by hand,
each of which the audit names precisely.
