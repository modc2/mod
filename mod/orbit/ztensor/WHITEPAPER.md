# ztensor: anonymous subnets with Zuma consensus, on any chain

*v0.4 · October 2026 · technical paper*

## Abstract

In a Bittensor-style subnet, miners do work and validators score it. Both sides
are identified, so a validator can favour a friend's miner and a miner can tell
who scored it down. ztensor removes the identities and keeps the rules. Miners
and validators are public **sets** of keys. Every action is a **scope-linkable
ring signature** proving "one member of this set did this", and its key image
enforces one action per member per scope. Emission is split on chain by **Zuma
consensus**, a majority-clipped rank that needs no validator identity. The
contract learns that the rules were followed and never learns who followed them.

One signer produces every signature. Four chains verify it with their own
contract (Starknet in Cairo, NEAR in Rust/wasm, Ethereum in Solidity/Yul,
Solana in sBPF), and a fifth adapter runs it as a replayable wasm contract on a
post-quantum ledger. Every chain's test suite pins its verifier to the same
reference numbers.

::: fig overview | One signer, one wire format, a verifier per chain. Adding a chain is adding an adapter package.

## 1. The problem

A subnet needs four guarantees:

1. Only registered miners submit, at most once per epoch.
2. Only registered validators score, at most once per submission.
3. A miner claims its share after the epoch closes, exactly once.
4. Nobody can link a submission, a score or a payout to a member.

Guarantees 1–3 normally come from identity: `msg.sender` *is* the miner.
Guarantee 4 rules that out. ztensor replaces identity with **set membership
plus a scope-bound pseudonym**.

## 2. Preliminaries

Let $E$ be the STARK curve over $\mathbb{F}_p$,

$$
E:\; y^2 = x^3 + x + \beta, \qquad p = 2^{251} + 17\cdot 2^{192} + 1,
$$

with generator $G$ of prime order $q$ and no cofactor. Each member holds a
secret $x \in \mathbb{Z}_q$ and publishes $P = x\,G$. We write
$\mathcal{H}(\cdot)$ for Poseidon (Hades permutation, Starknet constants) with a
domain tag as the first input, and $\mathcal{H}_p : \mathbb{F}_p \to E$ for
hash-to-point.

**Hash to point.** For a scope $\sigma$, $\mathcal{H}_p(\sigma)$ is found by
try-and-increment: the least counter $k$ such that

$$
X_k = \mathcal{H}(\texttt{zuma-h2p},\, \sigma,\, k)
$$

is a valid $x$-coordinate, taking the smaller root as $y$. Nobody knows
$\log_G \mathcal{H}_p(\sigma)$.

**Rings.** A ring $\mathcal{R} = (P_0,\dots,P_{n-1})$ must be sorted by strictly
increasing $\mathrm{id}(P) = \mathcal{H}(\texttt{zuma-key}, P)$, so it cannot be
padded with duplicates to look larger than it is.

## 3. Scope-linkable LSAG

ztensor uses Liu–Wei–Wong LSAG with Poseidon as the random oracle. A signer
with index $\pi$ signs message $m$ in scope $\sigma$; let $H = \mathcal{H}_p(\sigma)$.

$$
\begin{aligned}
I &= x_\pi\, H && \text{key image} \\
d &= \mathcal{H}(\texttt{zuma-msg},\, \sigma,\, m,\, I,\, n,\, P_0,\dots,P_{n-1}) && \text{digest} \\
L_i &= s_i\, G + c_i\, P_i \\
R_i &= s_i\, H + c_i\, I \\
c_{i+1} &= \mathcal{H}(\texttt{zuma-chal},\, d,\, L_i,\, R_i) && i \bmod n
\end{aligned}
$$

The signature is $\Sigma = (c_0,\, s_0,\dots,s_{n-1},\, I)$ and is valid iff the
ring closes:

$$
\mathsf{Verify}(\mathcal{R}, \sigma, m, \Sigma) = 1 \iff c_n = c_0 .
$$

::: fig ring | A ring of five validators. The verifier sees the whole ring light up; the signer (dashed) is indistinguishable. The key image is fixed by the key and the scope.

Two properties carry the protocol.

- **Anonymity.** $\Sigma$ reveals nothing about $\pi$ beyond $P_\pi \in \mathcal{R}$
  (under DDH in the random-oracle model).
- **Scope linkability.** The same key in the same scope yields the same image,
  $I = x\,\mathcal{H}_p(\sigma)$. The same key in two scopes $\sigma \ne \sigma'$
  yields images $x\,\mathcal{H}_p(\sigma)$ and $x\,\mathcal{H}_p(\sigma')$ that
  are unlinkable under DDH.

Linkability is the whole rate limiter: a contract stores the images it has seen
in a scope and rejects a repeat.

## 4. The protocol

Let $\varsigma$ be the subnet felt (it binds every scope to one deployment) and
$e = \lfloor \text{block} / \text{tempo} \rfloor$ the epoch.

| action | signer | scope $\sigma$ | message | the key image is | rule it enforces |
|---|---|---|---|---|---|
| `submit` | a miner | $\mathcal{H}(\texttt{zuma-mine}, \varsigma, e)$ | work commitment | the miner's **tag** $t$ | one submission per miner per epoch |
| `score` | a validator | $\mathcal{H}(\texttt{zuma-score}, \varsigma, e, t)$ | score $s \in [0,100]$ | a **nullifier** | one score per validator per tag |
| `settle` | anyone | — | — | — | Zuma consensus for one tag, once, after $e$ |
| `claim` | a miner | the submit scope again | recipient | the same $t$ | one claim per tag, only by its owner |

::: fig flow | One epoch. Submit and score are ring-signed by the matching set; settle is public arithmetic; claim re-signs the mine scope, so only the tag's owner can reproduce its image.

A validator sees a tag, never a miner. A miner's tags in different epochs are
unlinkable, because the mine scope contains $e$. A validator's scores on two tags
are unlinkable, because each tag has its own score scope. A claim reproduces the
tag only with the key that made it, and a second claim hits the same image.

**Relayers.** The contracts never check the caller on anonymous actions. A
transaction sender is always public, so members hand signed payloads to a relayer.

## 5. Zuma consensus

Validators are anonymous, so there is no per-validator stake: **membership is the
stake** and every validator weighs 1. Fix $\kappa = \tfrac12$. Let $V$ be the
validator count snapshotted at the epoch's first submit, $S_t$ the multiset of
scores on tag $t$, $n_t = |S_t|$, and $V_t = \max(V, n_t)$. Non-voters count as 0.

$$
\begin{aligned}
\mathrm{need}_t &= \left\lceil \kappa\, V_t \right\rceil \\[2pt]
C_t &= \max\left\{\, c \in [0,100] \;:\; \#\{ s \in S_t : s \ge c \} \ge \mathrm{need}_t \right\} && \text{consensus } (0 \text{ if } n_t < \mathrm{need}_t) \\[2pt]
R_t &= \sum_{s \in S_t} \min(s,\, C_t) && \text{clipped rank} \\[2pt]
\mathrm{pay}_t &= \mathrm{emission} \cdot \frac{R_t}{\sum_{u} R_u}
\end{aligned}
$$

::: fig consensus | Four validators score one tag 10, 10, 10, 100. Consensus is the highest score half the set reached, $C = 10$; the pumped 100 is clipped to 10 and adds nothing.

**Proposition (minority clipping).** A coalition of $k < \mathrm{need}_t$
validators cannot raise $C_t$ above the $\mathrm{need}_t$-th largest honest score.

*Proof.* $C_t$ is the $\mathrm{need}_t$-th order statistic of $S_t$ padded with
zeros to $V_t$. Raising $k$ entries changes which entries sit above the
$\mathrm{need}_t$-th position only if $k \ge \mathrm{need}_t$. $\square$

So a validator who is secretly a miner gains nothing by pumping its own tag,
and the late-registration attack is closed by snapshotting $V$.

**On-chain cost.** `score` increments a 101-bucket histogram $h_t[s]$ and tracks
the top score. `settle` walks down from the top, accumulating votes $a$ and mass
$M = \sum_{s > C_t} s\, h_t[s]$ until $\mathrm{need}_t$ is covered, then

$$
R_t = \Big(\sum_{s} s\, h_t[s]\Big) - M + C_t \cdot a ,
$$

at most 101 storage reads whatever the set size. `src/core/yuma.py` is the
reference; each chain's native suite asserts the same numbers.

**Omitted on purpose.** Validator dividends and EMA bonds both need a validator
identity that persists across tags and rounds, which is exactly what the
nullifiers remove. `cairo.zuma` layers per-round bonds and stake-tier rings on
the same signatures.

## 6. One signer, five verifiers

| | Starknet | NEAR | Ethereum | Solana | postquant |
|---|---|---|---|---|---|
| contract | Cairo | Rust/wasm, near-sdk 5 | Solidity + Yul | Rust sBPF | Rust pqc1 wasm, off chain |
| curve math | native EC builtin | `fp.rs`, $9 \times 29$-bit limbs | `StarkCurve.sol` in Yul | NEAR's `fp.rs` | NEAR's `fp.rs` |
| subnet felt $\varsigma$ | contract address | $\mathrm{sha256}(\text{account})$ | contract address | program state | contract name |
| max ring | 64 | 32 (300 Tgas) | 32 (EIP-7825) | 32 (session layout) | 64 |
| verify, ring 4 | ~15.8k steps, 19 `ec_op` | ~36 Tgas | ~2.07M gas | ~40M CU over 42 units | ~5 ms wasm |
| native tests | snforge 10 | cargo 25 | forge 11 | cargo 8 | cargo 3 |

The signer is `src/core/lsag.py`, a line-for-line port of `lsag.cairo` that is
**bit-identical** to the Cairo prover (asserted by
`test_native_signer_bit_identical_to_cairo`) and ~80 ms per signature against
0.9–3.7 s for `cairo-run`. Only $\varsigma$ differs between chains, so
signatures are interchangeable and cross-chain replay is impossible: every scope
contains $\varsigma$.

::: fig costs | Verification cost grows linearly in the ring. Each chain hits its own ceiling: a per-call gas limit, a per-transaction gas cap or an account-layout limit.

## 7. Making the STARK curve affordable without a builtin

Starknet gets the curve for free. Elsewhere every field operation is paid for.
The first NEAR port called lambdaworks directly and cost **214 Tgas for a ring
of 4**, capping rings at ~5 members. Four changes fixed it.

1. **A field shaped for the prime.** Elements are 9 limbs of 29 bits. A
   $29 \times 29$ product is below $2^{58}$, so a schoolbook column of up to 9
   products fits a `u64` with no carry handling. In radix $2^{29}$ the modulus
   has three nonzero limbs and $p \equiv 1 \pmod{2^{29}}$, so Montgomery
   reduction is multiply-free: $m = -t_i$ and $m\,p$ is three shifted adds.
   Squaring needs 45 products instead of 81.
2. **No `__multi3`.** rustc lowers every checked `u64` and every `u128` multiply
   to a call in wasm. The field core uses wrapping arithmetic whose bounds are
   proven by construction.
3. **Better curve formulas.** Jacobian coordinates with $a = 1$ doubling
   ($1M + 8S$) and mixed addition ($7M + 4S$). Each $L_i, R_i$ is one Straus
   double-scalar multiplication with 4-bit windows: 252 shared doublings and at
   most 126 additions; normalisation uses batch inversion.
4. **Hinted hash-to-point.** The client fetches $y$ from a free view and the
   contract checks $y^2 = X^3 + X + \beta$ instead of a square root (the field's
   2-adicity is 192). Earlier counters are proven non-squares by Euler's
   criterion, $X^{(p-1)/2} = -1$ with $\tfrac{p-1}{2} = 2^{250} + 2^{195} + 2^{191}$:
   250 squarings and 2 multiplies.

| primitive (in-contract, NEAR) | before | after |
|---|---|---|
| field multiply | 1.85 Ggas | 0.87 Ggas |
| double-scalar mul $aA + bB$ | $2 \times 11.1$ Tgas | 2.68 Tgas |
| Poseidon, 6 inputs | 1.57 Tgas | 1.06 Tgas |
| **submit, ring 4** | **214 Tgas** | **36 Tgas** |

Measured, submit costs about $7.5\,\text{Tgas} \cdot n + 6\,\text{Tgas}$, so NEAR
caps rings at $n = 32$. Ethereum reuses the same ideas in Yul (~0.41M gas per
member, capped by EIP-7825's $2^{24}$ per-transaction gas). Solana splits one
verification into a session plus per-member units, each under 1.4M CU.

## 8. Mainnet economics

Each adapter reports live gas price, block time and capacity; `economics()`
prices every action in USD. On a public network `deploy` refuses an epoch
shorter than one hour of measured block time, so relayer time and blockspace
stay bounded.

| ring-4 score, at launch | Starknet | NEAR | Solana | Ethereum |
|---|---|---|---|---|
| USD per action | ~\$0.002 | ~\$0.019 | ~\$0.024 | ~\$0.46 |

Starknet's figure is an estimate (Sierra-gas weights; the account's own
validation is not counted). The COSTS tab shows the live numbers.

### Reducing cost without giving up privacy

An epoch costs $M\,\mathrm{sub}(n) + VM\,\mathrm{sig}(n) + M\,\mathrm{set} + M\,\mathrm{clm}(n)$. The score term grows with miners × validators and each term is a full ring verification, so it dominates at scale.

- **Free levers (leak nothing).** Longer epochs (cost/day ∝ 1/hours). Ring size as a privacy *floor*: cost is linear in $n$, so pick the smallest set you accept, enforce it as $n_{\min}$, and let the fixed partition give everyone exactly that set. Chain choice (≈1 400× between Ethereum L1 and Starknet). Relay batching (saves only base fees, but mixes senders).
- **Cuts that link actions.** *Batch* (one signature per validator over its score vector, like `set_weights`) links one validator's scores into a vector that can fingerprint it across epochs. *Lazy* claims link $K$ of a miner's epoch tags. *Proof* inherits both.
- **Cuts that keep unlinkability.** *Secret committees*: a validator may score tag $\tau$ only if $H(\nu) < (C/V)\,p$, where $\nu$ is the nullifier it publishes anyway. The committee is hidden until it acts, verifiable by anyone, cannot be ground (key fixed, scope carries an epoch beacon), and leaves ≈$C$ scores per tag instead of $V$. *Private settlement*: every submit, score and claim is verified off chain, and one validity proof per epoch lands on chain. Epoch-scoped images are checked inside the proof and never stored. Scores themselves need not be published, which is more private than today. The prover can delay an epoch but cannot forge one, and the direct path stays as a fallback.

USD per day, $M=256$, $V=64$, $n=16$, 1 h epochs, $K=24$, $C=32$ (only "today" is measured; the rest is modeled in `src/core/forecast.py`; * = proof priced at 30 ring-16 scores):

| chain | today | batch | +lazy | +proof | committee | +private | saving |
|---|---:|---:|---:|---:|---:|---:|---:|
| Starknet | 3052.81 | 260.13 | 219.10 | 8.55 | 1574.04 | 9.93 | 307× |
| NEAR | 24952.88 | 995.51 | 624.83 | 20.90 | 12867.55 | 7.53 | 3313× |
| Ethereum | 4261949.26 | 185464.82 | 117201.16 | 3887.86 | 2203365.91 | 1223.55 | 3483× |
| Solana | 36074.28 | 1268.90 | 777.18 | 25.12 | 18553.49 | 6.25 | 5776× |
| Substrate* | 8051.55 | 302.49 | 187.43 | 10.47 | 4146.63 | 7.13 | 1129× |

**Committees cost security, not privacy.** The quorum becomes $\lceil\max(C,n_\tau)/2\rceil$ per committee. A coalition with fewer than $C/2$ members can never reach it. With $V=64$, $C=32$ and 25% colluding, a tag is captured with probability $2.2\times10^{-7}$ and starved (the coalition abstains, too few honest members are drawn) with probability 0.0066. With $C=16$ these become 0.0041 and 0.061. Starved tags can be re-scored by a retry committee. Once verification is off chain, linking buys almost nothing: +private costs the same order as +proof. **Recommended path: committees, then private settlement.**

The FORECAST and MAX cards on the COSTS tab plot both ladders live (`forecast`, `experiment strategy=committee|private`).

### Transfers on a shielded post-quantum L1 (planned)

Credits are points today. Transfers are planned on a dedicated **ztensor L1**
built from the postquant system (modular consensus, ML-DSA / SLH-DSA keys,
SHA3-256), where **every wallet is shielded automatically**: no transparent
addresses, no public balances, no opt-in.

- A balance is a set of notes $cm = \mathrm{SHA3}(v, pk, \rho, r)$ in an
  append-only SHA3 Merkle tree.
- A spend reveals nullifiers $nf = \mathrm{SHA3}(sk, \rho)$ and output
  commitments, with a hash-based STARK that each input is in the tree and
  $\sum v_\text{in} = \sum v_\text{out}$. Spend authority is ML-DSA (FIPS 204).
- A Zuma claim mints its amount directly as a note to the miner's shielded
  address, so the payout is linked neither to the registered key nor to later
  transfers.
- Scores, the emission split and per-tag claim sizes stay public. Shielding
  hides recipients and flows, not amounts earned per tag.

The STARK is the same component the post-quantum membership proof needs (§9),
so both ship together. Nothing here is implemented yet.

## 9. Security and limits

- **The anonymity set is the ring.** With 3 validators a score points to 1 of 3.
  `min_ring` is enforced on chain and padding is impossible, but no contract can
  create members who do not exist.
- **Metadata.** Senders, timing, IP addresses and work content can identify a
  member. Use a relayer; never post from a registered account.
- **Collusion.** A validator who is also a miner knows its own tag. Zuma
  consensus clips every score to the majority (§5), so pumping pays only if a
  majority colludes.
- **Cost cuts are modeled.** Committees and private settlement are not
  deployed. Committees trade the deterministic majority bound for a small,
  quantified capture/starve probability; batched weights and lazy claims are
  cheaper only without a prover, and they link actions.
- **Public books.** Credits are points in contract storage; no token moves
  yet. Planned transfers run on the ztensor L1 with shielded wallets by
  default (§8); the subnet's scores and emission split stay public there.
- **Not post-quantum.** The postquant adapter makes the *execution record*
  quantum-safe (ML-DSA receipts, SHA3 commitments). The anonymity layer is still
  discrete-log based: an adversary who can compute $x = \log_H I$ links every
  published image to its key, retroactively. A PQ membership proof (a hash-based
  STARK of $\mathcal{H}(sk)$ in a SHA3 Merkle set) is future work.

## 10. Reproduce

```bash
m ztensor/test                          # every chain's native suite
m ztensor/simulate chain=starknet       # full epoch on starknet-devnet :51332
m ztensor/simulate chain=near           # ... near-sandbox :51333 (eth, solana, postquant too)
m ztensor/gas_sweep chain=near          # cost by ring size -> data/near_gas.json
m ztensor/economics fresh=1             # mainnet USD per action
m ztensor/deploy network=sepolia account=<sncast account>
```

The console at `/ztensor/` runs any chain live, shows each codebase, compares the
measured costs and renders this paper.
