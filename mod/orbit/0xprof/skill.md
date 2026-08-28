# 0xprof — skill

A market for zk proofs where the verifier is the product. Reach for this module
when something needs a proof **checked** — by more than one implementation —
or when proofs need to be published, priced, or paid for before they exist.

## The one idea

A proof gets run through every method that can check it, and each answer is
kept separately. Two independent methods agreeing makes it `verified`; one
makes it `claimed`; a disagreement makes it `disputed`. An error is never a
rejection. A browser or a peer can report what *their* verifier saw — that is a
witness, it can contest a proof, and it can never promote one.

## Verify something (no account, nothing stored)

```bash
m 0xprof/verify proof=proof.json vkey=verification_key.json public_signals=public.json
curl -sX POST localhost:50610/verify -H 'content-type: application/json' \
  -d '{"proof":{…},"vkey":{…},"public_signals":["1000"]}'
```

The system is sniffed from the file shape unless you pass `system=`. The reply
has one entry per method plus a `status`/`why` pair that spells out how it was
reached.

## Make one

```bash
m 0xprof/prove system=schnorr secret=42 context=demo      # also dleq, merkle
m 0xprof/prove system=groth16 zkey=circuit.zkey wasm=circuit.wasm inputs=input.json
```

Everything proved here is immediately re-verified by the independent methods
before it is returned.

## Sell one, or pay for one

```bash
m 0xprof/publish proof=proof.json vkey=vkey.json public_signals=public.json \
                 title="over the line" price=5 tags=solvency
m 0xprof/proofs status=verified for_sale=true
m 0xprof/buy id=<proof id>
m 0xprof/bounty system=groth16 reward=25 vkey=vkey.json \
                require='[{"index":1,"min":1000}]' title="prove you clear 1000"
m 0xprof/submit id=<bounty id> proof=proof.json public_signals=public.json
```

Bounty specs are mechanical: the verification key pins the circuit, and
`equals` / `min` / `max` rules pin the public signals. A submission that
verifies but fails a rule is recorded, and not paid.

## Re-verify a listing (signed)

```bash
m 0xprof/recheck id=<proof id>       # signs the challenge with this box's key
m 0xprof/checks  id=<proof id>       # every run of the methods, and who asked
m 0xprof/verifiers                   # the addresses that have checked things here
```

Over HTTP it is two calls: `POST /proofs/{id}/verify/challenge {"address"}`
returns a message naming that proof, and `POST /proofs/{id}/verify` takes
`{address, message, signature}` from `personal_sign`. Checking a loose proof at
`/verify` stays free and anonymous; re-running a *listing* rewrites what it
says its verifiers think, under a name, so it is signed. The signature is bound
to that one proof, expires in ten minutes, and buys exactly one run — the check
log is the replay list.

## Which method can check what

`m 0xprof/methods` — and it changes with the box. `native` needs `py_ecc`;
`snarkjs`, `node` and `solidity` need `npm install`; `evm` and `solidity` need
an RPC (`ZKPROF_RPC` to override the public defaults). A method that cannot run
returns `unavailable`, which is not a verdict and never fails a proof.

groth16 has four checkers here, plonk and fflonk two, and the sigma/merkle
systems two. Nothing in this module is verifiable by only one implementation —
if it were, it could never get past `claimed`.

## Gotchas worth knowing

- **`ZKPROF_OPEN=1` is development only.** It treats every caller as signed in
  — except for re-verification, which needs a real signature either way: open
  mode can fabricate an identity, and it cannot fabricate evidence.
- **A verdict's `by` is attribution, not authority.** The method did the maths;
  the address only made it run. Two people re-running `native` is still one
  `native` verdict, and only a witness — somebody's own verifier — can
  contradict this box.
- **The proof bytes are the only thing a price hides.** Statement and verdicts
  are public by design; a market where you pay to find out what you bought is
  not one.
- **A proof published twice is one record** — the id is the hash of
  (system, statement, proof).
- **`solidity` compiles per verification key** and caches under
  `~/.mod/0xprof/solidity/`. First check on a new circuit takes a few seconds;
  after that it is one `eth_call`.
- **The public RPC must support `eth_call` state overrides** for the `solidity`
  method. geth, reth and erigon do; some hosted endpoints strip it, and the
  method says so rather than guessing.
- **Rebuilding fixtures needs circom** (`fixtures/build.sh`). Verifying does
  not — circom is only in the loop when circuits change.
