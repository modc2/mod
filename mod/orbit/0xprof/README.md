# 0xprof

**A marketplace for zero-knowledge proofs, and five independent ways to check one.**

A proof is a claim about a claim. Somewhere in the chain you either do the
arithmetic or you trust the person who says they did — and every zk product in
practice puts a single "✓ verified" badge at that spot and hopes you don't ask
who computed it. This module is an argument that you should ask, and that the
answer should be five names instead of none.

Every proof published here is checked by every method this box can bring to
bear. Each answer is kept separately, published separately, and never averaged:

| method | where it runs | independent of |
|---|---|---|
| `native` | this process — `py_ecc` pairings, hand-written secp256k1 | node, the network |
| `snarkjs` | a node subprocess — iden3's reference implementation | this module's arithmetic |
| `node` | a node subprocess — an independent BigInt implementation of the sigma protocols and the tree walk | the Python |
| `evm` | a public Ethereum node's `alt_bn128` precompiles (`0x06`/`0x07`/`0x08`) over `eth_call` | this box entirely |
| `solidity` | the **real verifier contract** — rendered from the verification key, compiled with solc, and executed by a public node through an `eth_call` state override | this box entirely |
| `browser` | the visitor's own tab, snarkjs wasm | this box entirely — a *witness*, never a verification |
| `attest` | somebody else's deployment, signed | everything here |

The last two are witnesses. They are recorded and shown, they can *contest* a
proof, and they can never promote one — because a claim about a verification is
not a verification, and pretending otherwise would give away the only thing
this module is careful about.

The `solidity` method is the one worth stopping on. `eth_call` takes a state
override, so the verifier contract that a rollup or a bridge would deploy can
be handed to a node as code-at-an-address, executed, and thrown away. Nothing
is deployed, no gas is spent, no key is needed, and the boolean comes back from
an EVM implementation nobody here wrote. It is what gives plonk and fflonk a
second opinion at all.

## Statuses

```
unverified   nothing has checked it, or every method errored
claimed      exactly one authoritative method says valid — a claim, not a verification
verified     two or more independent methods agree it is valid
invalid      a method rejected it and none disagreed
disputed     they disagree. One of these implementations is wrong, and finding
             out which is the entire reason for running five
```

An **error is not a rejection**. A verifier that reports its own crash as a
false proof will eventually mark a good proof false, so `error` and `invalid`
are different words here and always will be.

## Methods verify. People sign.

Every run of the methods is filed against the address that asked for it, and
every listing carries that roster next to its verdicts — who published it, who
has re-run it since, who checked it in their own browser, and when. The two
kinds of row are never merged: *`native` said valid* is a claim about
arithmetic, *`0x7d7c…` re-ran it an hour ago* is a claim about a person. One you
check by doing the maths, the other by recovering an address from a signature.

Which is why re-verifying costs a `personal_sign`:

```bash
curl -sX POST localhost:50610/proofs/$ID/verify/challenge \
     -H 'content-type: application/json' -d '{"address":"0x…"}'
# → {"message":"0xprof re-verify\nproof: <id>\naddress: 0x…\nissued: …"}
curl -sX POST localhost:50610/proofs/$ID/verify \
     -H 'content-type: application/json' \
     -d '{"address":"0x…","message":"…","signature":"0x…"}'
m 0xprof/recheck id=$ID          # the same thing, signed with this box's key
m 0xprof/checks   id=$ID         # every run on it, and who asked for each
m 0xprof/verifiers               # the addresses that have checked things here
```

Checking a *loose* proof at `/verify` is free, anonymous and always will be —
it is the useful part of this module and putting friction there would mean the
honest thing to do with a suspicious proof is the thing with a login. Re-running
a *listing* is a different act: it overwrites what that listing says its
verifiers think, in front of the people deciding whether to buy it. That gets a
name on it.

The signature names the proof, so it cannot be moved to another one; it expires
in ten minutes; and the check log is its replay list, so it buys exactly one
run. `ZKPROF_OPEN` does not waive it — open mode means "believe whoever the
caller says they are", and a signature is not a claim about identity that a
server can decide to accept. It is evidence a reader checks later without this
box in the room, and no environment variable can manufacture one.

The row that matters most on that roster is the browser one. A witness who ran
the proof in their own tab and got `invalid` is either wrong or has caught this
box lying, and there is no third option — so it gets its own column, and it
never changes a status by itself.

## Signing in, with or without a wallet

An address is the whole account: no password, no email, nothing to register.
There are two ways to prove you hold one and the server cannot tell them apart,
because there is nothing there to tell apart — both end at an address recovered
from a `personal_sign` signature.

```
wallet     an extension signs the challenge from POST /auth/challenge
anonymous  the console generates a secp256k1 key in the tab (src/app/keys.js —
           keccak-256 and the curve in BigInt, no dependencies) and signs the
           same challenge with it
token      a mod-protocol token, for anything calling from the CLI
```

An anonymous account is not a guest mode. It publishes, buys, posts bounties
and re-verifies like any other address, its runs appear on the roster under
their own name, and a reader can recover that address from the signature
without this box in the room. What differs is custody: that key lives in one
browser's `localStorage`, so clearing site data destroys the account and
everything owed to it. The WALLET tab shows the key, copies it out and imports
one back, and says so in those words — a key you cannot take with you is not
really yours.

## The market

- **Proofs** are the goods. The statement — the verification key and the public
  signals — and every verdict are always public; a price gates the proof bytes
  and nothing else, so you can read what you are buying and how it checked out
  before paying for it.
- **Bounties** are the demand side: pay for a proof that does not exist yet.
  The reward sits in escrow from the moment you post, and the spec is
  mechanical — a verification key (which pins the circuit) plus constraints on
  the public signals (`equals`, `min`, `max`). Whether a submission qualifies
  is decided by arithmetic, not by argument.
- **Staked rounds** put skin on the prover's side too. A bounty posted with a
  `stake` (a dollar, by default) runs in rounds: a prover signs in by locking
  the stake for one numbered token — no token, no submission — and nothing
  settles until the reset, the next UTC midnight (`settle=daily`) or the
  seventh one out (`weekly`). At the reset every token liquidates and the
  proof settles in one motion: among accepted submissions the lowest token
  number wins — first to *sign*, not first to upload — and takes the reward,
  their stake back, and the stakes of every seat that never submitted.
  A holder who submitted anything, even a failed proof, liquidates at par.
  An unwon round returns every stake and resets daily with the same escrow,
  until the bounty's own TTL sends it home. Nobody — poster, winner, owner —
  can settle early: the lock is what the stake bought.
- **Refunds** are the guarantee that makes the rest mean anything: a proof sold
  as verified that stops verifying can be unwound against the seller's balance,
  which is allowed to go negative. Selling a bad proof and withdrawing is not a
  strategy here.

Credits are internal. An owner grants them; after that they only move between
accounts. They are not a token and they do not leave the module.

## Proof systems

| system | zero-knowledge | setup | checked here by |
|---|---|---|---|
| groth16 (bn128, bls12-381) | yes | per-circuit ceremony | native, snarkjs, evm, solidity, browser |
| plonk | yes | universal | snarkjs, solidity, browser |
| fflonk | yes | universal | snarkjs, solidity, browser |
| merkle inclusion | **no** | none | native, node, browser |
| schnorr (Fiat-Shamir) | yes | none | native, node, browser |
| chaum-pedersen (DLEQ) | yes | none | native, node, browser |
| pedersen opening | **no** | none | native, node, browser |

The two `no`s are not oversights. Inclusion proofs and commitment openings are
most of what gets *called* a proof in practice, they verify perfectly well, and
they hide nothing — so they are carried, and labelled, rather than left to be
sold as something they aren't.

## Running it

```bash
npm install                 # snarkjs, solc, circomlib — the node half
pip install py_ecc          # the pairing arithmetic for the native method
m 0xprof/serve              # API :50610, console :50611/0xprof
```

The console is plain ES modules behind a stdlib proxy, so the app half stays up
while the API restarts. Behind the gateway the same page works unchanged: it
asks its own origin for `_api`.

It is drawn as an 8-bit cabinet — hard edges, four-pixel drop shadows, scanlines
over the whole tube, and Press Start 2P served from `src/app/fonts/` so the look
does not depend on a CDN. One rule keeps it usable: **chrome is pixel, data is
mono.** A 5×7 bitmap face cannot tell `0` from `O` or `8` from `B`, which is
exactly the wrong property for a page full of addresses, curve points and public
signals — so labels, tabs, buttons and headings are the pixel font, and every
hex string stays in a face you can read a nonce out of.

## From the CLI

```bash
m 0xprof                                    # the card
m 0xprof/methods                            # who can verify what, right now
m 0xprof/verify proof=fixtures/threshold_g16_proof.json \
                vkey=fixtures/threshold_g16_vkey.json \
                public_signals=fixtures/threshold_g16_public.json
m 0xprof/prove system=schnorr secret=42 context=demo   # also dleq, pedersen, merkle
m 0xprof/publish proof=… vkey=… price=5 title="over the line"
m 0xprof/recheck id=…                       # re-run the methods, signed by this box
m 0xprof/checks id=…                        # every run on it, and who asked
m 0xprof/verifiers                          # the people, as opposed to the methods
m 0xprof/bounty system=groth16 reward=25 vkey=… require='[{"index":1,"min":1000}]'
m 0xprof/bounty system=groth16 reward=25 vkey=… stake=1 settle=daily   # a staked round
m 0xprof/join id=…                          # 1 credit for token #N, locked until the reset
m 0xprof/settle id=…                        # run the reset, once the clock says so
m 0xprof/test                               # 48 tests, most of them adversarial
```

Paths are accepted anywhere JSON is, because the three files a prover produces
are on disk.

## The fixtures are real

`fixtures/build.sh` compiles `circuits/*.circom` with circom, runs a real
(single-contributor — **not** production-safe) powers-of-tau and phase-2
ceremony with snarkjs, and proves both circuits under all three protocols. The
tests then check each verifier against those proofs *and* against tampered
versions of them, because a verifier suite that only tests valid proofs is
passed by a function that returns `true`.

The demo circuit is worth reading: `circuits/threshold.circom` proves *"the
number I committed to is at least N"* — a Poseidon commitment pins the value
before the threshold is chosen, and the proof reveals nothing else. Solvency,
age, score and reputation are all that shape.

## What a purchase actually guarantees

That this box ran the proof through every method it has, published each answer,
and is holding the same bytes it checked. Afterwards you can run those methods
again — and the browser button runs one in your own tab, which is the only
check that does not require trusting this server.

What it cannot guarantee is that the *statement* is one you care about. A
perfectly valid proof of an uninteresting claim is still valid. Reading the
statement is the buyer's job, and it is free.

## State

Everything — proofs, bounties, the ledger — lives in the `store` mod under the
`0xprof/` prefix, with blobs under their own SHA-256 in the shared `blobs/`
namespace. `~/.mod/0xprof` holds only the HMAC session secret, the owner claim
and the solc output cache. There is no private database, on purpose: a market
whose records sit in a file beside its own process is a market you have to take
on faith, and this one is in the business of not being taken on faith.
