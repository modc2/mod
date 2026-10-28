# ztensor: Anonymous Incentive Subnets from Scope-Linkable Ring Signatures, with One Signer and Verifiers on Six Execution Environments

*October 2026 · measurement snapshot 2026-10-20 · PDF: `paper/ztensor.pdf` (LaTeX source `paper/ztensor.tex`). Every number below is generated from `paper/data/snapshot.json` by `paper/figures.py`.*

## Abstract

In incentive subnets of the Bittensor kind, miners produce work, validators score it, and a consensus rule turns scores into emission. Both roles act under persistent public identities, so a validator can favour or punish a known miner and a miner can identify, and retaliate against, the validator that scored it down. **ztensor** keeps the rules of such a subnet while removing the identities behind every action. Miners and validators form two public sets of keys. Every action is a scope-linkable ring signature (LSAG) whose key image enforces exactly one action per member per scope. A miner's image in an epoch scope is its pseudonymous *tag*. A validator's image in a per-tag scope is a *nullifier*. A claim re-creates the miner's tag and so can only be made by its owner. **Zuma consensus** is a variant of Yuma consensus for validators without identities: every validator weighs one, and each score is clipped to the highest score that half of the validator set supports. We prove that a coalition smaller than half the set cannot raise a tag's consensus above what the honest validators support. One signer produces every signature, and six independently written verifiers check them byte for byte: Cairo on Starknet, Rust/wasm on NEAR, Solidity/Yul on Ethereum, sBPF on Solana, an ink! contract on Substrate's pallet-contracts, and an off-chain wasm contract with ML-DSA receipts. Verification cost is linear in the ring size on every chain we fitted ($R^2 \ge 0.9999$). On Substrate it is $6.4 + 5.71\,n$ ms of reference time, so a 64-member ring uses 29% of the per-extrinsic limit. At the mainnet prices of 2026-10-20, a ring-4 score costs from USD 0.0019 (Starknet, estimated) to USD 3.52 (Ethereum L1); on Astar, a public pallet-contracts chain, it costs USD 0.0061. The paper also states what the construction does not give, including a ring-intersection leak in our earlier ring-selection rule and its fix.

## 1. Introduction

In a Bittensor subnet every participant is a public hotkey, and so every score is attributable. Two problems follow:

1. **Favouritism.** A validator that knows which miner it is scoring can favour an affiliated miner or punish a competitor.
2. **Retaliation and collusion markets.** A miner that knows who scored it down can retaliate or negotiate. A miner and a validator that can recognise each other can contract on scores.

Both need the parties to know *who* they are dealing with, and nothing more. Every rule a subnet enforces can be stated without identities: registered miners; one submission per miner per epoch; at most one score per validator per submission; payouts by a consensus rule; one claim per payout, by the miner that earned it. ztensor enforces all of them on chain, by contracts that never learn which member acted.

**Approach.** Replace identity with *membership plus a scope-bound pseudonym*. A linkable ring signature proves "some member of this public ring signed". Its key image $I = x\cdot H_p(\sigma)$ is deterministic in the secret $x$ and a scope $\sigma$: the same key in the same scope gives the same image, while images in different scopes cannot be linked. Choosing the scopes defines the protocol.

**Non-goals.** ztensor does not hide *that* someone is a member, how many members there are, or the content of the work. Sybil resistance stays with whoever admits keys. No tokens move yet: payouts are credits in contract storage. Planned transfers run on a dedicated postquant-based L1 on which every wallet is shielded by default (§9).

## 2. Related work

Ring signatures (Rivest, Shamir and Tauman, 2001); linkable spontaneous anonymous group signatures, LSAG (Liu, Wei and Wong, 2004); key images in CryptoNote and Monero RingCT; traceable ring signatures with issue-dependent tags (Fujisaki and Suzuki, 2007), which is our *scope*; Zerocash nullifiers; empirical erosion of ring anonymity in Monero (Kumar et al., 2017; Möser et al., 2018); Bittensor (Rao et al., 2020) and Yuma consensus; Poseidon (Grassi et al., 2021); Cairo and STARKs; Straus double-scalar multiplication, Montgomery multiplication and batch inversion.

## 3. The signature scheme

**Parameters.** $p = 2^{251}+17\cdot2^{192}+1$; $\mathbb{G}$ is the STARK curve $y^2 = x^3+x+\beta$ over $\mathbb{F}_p$, of prime order $q$ and cofactor 1, with generator $G$. $H$ is Poseidon (Starknet's Hades parameters) with domain tags `zuma-h2p`, `zuma-msg`, `zuma-chal`, `zuma-key`, `zuma-mine` and `zuma-score`. Challenges are used as scalars mod $q$; since $|p-q|<2^{127}$, this is within statistical distance $2^{-124}$ of uniform.

**Hash to curve.** $H_p(\sigma) = (X_k, \min(y, p-y))$ for the least $k$ with $X_k = H(\texttt{zuma-h2p},\sigma,k)$ on the curve. A verifier may take $y$ as a hint. It checks the root, checks that $y$ is the smaller one, and proves every earlier $X_j$ a non-residue with Euler's criterion, so a wrong hint never changes the result.

**Canonical rings.** $\mathrm{id}(P) = H(\texttt{zuma-key},P_x,P_y)$. Rings must have strictly increasing ids, so the same key cannot appear twice.

**Sign** $m$ in scope $\sigma$ with secret $x$ at position $\pi$:

$$I = x\,H_p(\sigma),\qquad d = H(\texttt{zuma-msg},\sigma,m,I,n,P_0,\dots,P_{n-1})$$
$$L_i = s_iG + c_iP_i,\quad R_i = s_iH_p(\sigma) + c_iI,\quad c_{i+1} = H(\texttt{zuma-chal},d,L_i,R_i)$$

with $c_{\pi+1}$ from $\alpha G, \alpha H_p(\sigma)$ and $s_\pi = \alpha - c_\pi x \bmod q$. **Verify:** the ring is canonical, every point is on the curve, and the recomputed chain closes, $c_n = c_0$.

**Theorem 1.** In the random-oracle model the scheme is unforgeable with respect to insider corruption, scope linkable ($k$ secrets give at most $k$ distinct images per scope) and non-frameable under discrete log, and anonymous in a fresh scope under DDH. *(Proof sketch in the PDF: forking-lemma extraction binds each accepting signature to one member's secret consistent with its image; anonymity programs $H_p(\sigma^*)$ to a DDH instance.)* **Corollary.** Images of one key in distinct scopes are indistinguishable from images of two independent keys.

## 4. The protocol

| action | signer | scope $\sigma$ | message | key image |
|---|---|---|---|---|
| submit | a miner | $H(\texttt{zuma-mine},\mathfrak{s},e)$ | work commitment | the miner's **tag** $\tau$ |
| score | a validator | $H(\texttt{zuma-score},\mathfrak{s},e,\tau)$ | score 0..100 | a **nullifier** $\nu$ |
| settle | anyone | — | $(e,\tau)$ | — |
| claim | a miner | $H(\texttt{zuma-mine},\mathfrak{s},e)$ | recipient | $\tau$ again |

$\mathfrak{s}$ is the subnet element, derived from the contract's own address, so a signature for one deployment is valid in no other. $e = \lfloor \text{block}/\text{tempo}\rfloor$. Relayers post every anonymous action; the contracts never check the caller.

**Proposition 3.** Under Theorem 1: (P1) at most one tag per miner per epoch, and only registered miners have tags; (P2) at most one score per validator per tag; (P3) a credit is claimed once, only by the tag's creator; (P4) a relayer cannot redirect a claim; (P5) an observer learns only ring membership, and a miner's tags across epochs and a validator's nullifiers across tags are unlinkable.

## 5. Zuma consensus

Every validator weighs one (membership is the stake); $\kappa = \tfrac12$; $V$ is snapshotted at the epoch's first submit; non-voters count as 0; $V_\tau = \max(V,n_\tau)$; $k = \lceil V_\tau/2\rceil$.

$$C_\tau = \max\{s : |\{i : s_i \ge s\}| \ge k\},\qquad R_\tau = \sum_i \min(s_i, C_\tau),\qquad \mathrm{pay}_\tau = \Big\lfloor E\,\frac{R_\tau}{\sum_{\tau'} R_{\tau'}}\Big\rfloor$$

On chain, settle walks the histogram from score 100 down, taking at most 101 reads: $R_\tau = S_\tau - m(s) + s\cdot a(s)$.

**Proposition 4 (sub-quorum coalitions cannot raise consensus).** For a coalition of $a<k$ validators, $C_\tau \le \hat s_{k-a}$, the $(k-a)$-th highest honest score, and the coalition adds at most $a\,\hat s_{k-a}$ to $R_\tau$. Proof: $|\{i: s_i\ge s\}| \le a + |\{j:\hat s_j\ge s\}|$.

Yuma's bonds and validator dividends need a persistent validator identity, which the nullifiers remove, so validator rewards are future work.

## 6. One signer, six verifiers

One Cairo prover (`src/core/prover`), plus a bit-identical Python port, signs for every chain; each chain only verifies, behind a 19-method adapter interface with an automatic conformance test.

| chain | verifier | curve + hash | subnet element $\mathfrak{s}$ |
|---|---|---|---|
| Starknet | Cairo | native EC + Poseidon builtins | contract address |
| NEAR | Rust → wasm (near-sdk 5) | 9×29-bit limbs, Straus, hinted $H_p$ | sha256(account) mod $2^{251}$ |
| Ethereum | Solidity + Yul | Yul field ops, Poseidon contract | contract address |
| Solana | sBPF | verification split into units under 1.4 M CU | felt in the subnet account |
| **Substrate** | **ink! 5 → wasm** | NEAR's `fp/lsag/poseidon`, verbatim | blake2_256(AccountId) mod $2^{251}$ |
| postquant | pqc1 wasm, off chain | NEAR's sources + ML-DSA-44 receipts | sha256(contract path) mod $2^{251}$ |

**Substrate.** `src/networks/substrate/contract` is an ink! 5.1.1 contract for pallet-contracts with no copy of the cryptography: `build.rs` includes NEAR's verifier sources with `alloc` imports, so one source verifies on three platforms. Felts are 32-byte big-endian and rejected, never reduced, if $\ge p$; errors are a typed enum that reverts; the Zuma histogram is sparse. Build traps: lambdaworks-math pulls `getrandom`/`js-sys` (std) on wasm32 and never calls it, so a stand-in is patched in; cargo-contract 5.0.3 needs rustc 1.88; `TMPDIR` is set so the relative `[patch]` path resolves from cargo-contract's temporary manifest. The wasm is 149 KB. The client dry-runs every call via `ContractsApi_call` (value, revert flag, weight), then submits `Contracts.call` with the weight plus 20%.

## 7. Evaluation

Measured on one host (Xeon Silver 4116) against local nodes: starknet-devnet 0.2.4, neard 2.0.0, anvil 1.7.1, solana-test-validator 4.3.2, substrate-contracts-node 0.42.0. The quantity is each chain's own deterministic resource accounting, not wall-clock time.

**Correctness.** On all six environments a full epoch (4 miners, 4 validators, 3 tags, 12 scores, ring 4) settles exactly as the reference does and rejects the replayed claim. All 86 native tests pass: Starknet 10, NEAR 32, Ethereum 15, Solana 13, Substrate 13, postquant 3.

**Cost per action, ring 4, USD on mainnet (2026-10-20):**

| chain | unit | submit | score | settle | claim |
|---|---|---|---|---|---|
| Starknet | Cairo steps | 15 979 · 0.0017 | 17 133 · 0.0019 | 13 862 · 0.0016 | 16 091 · 0.0017 |
| NEAR | Tgas | 29.6 · 0.014 | 38 · 0.018 | 2.61 · 0.0013 | 29.5 · 0.014 |
| Ethereum | gas | 2.29 M · 3.45 | 2.34 M · 3.52 | 174 537 · 0.262 | 2.27 M · 3.41 |
| Solana | CU (all units) | 38.6 M · 0.024 | 38.9 M · 0.025 | 5 739 · 0.0011 | 38.5 M · 0.024 |
| **Substrate** | ms ref_time | 28.7 · 0.0059 | 29.7 · 0.0061 | 1.38 · 0.0003 | 28.8 · 0.0059 |
| postquant | ms wasm, off chain | 6.79 | 8.48 | 0.036 | 6.89 |

**Cost models** $\mathrm{cost}(n) = a + b\,n$, fitted by least squares to ring sweeps:

| chain | unit | $a$ | $b$ per member | $R^2$ | $n_{\max}$ | binding limit |
|---|---|---|---|---|---|---|
| NEAR | Tgas | 6.27 | 7.51 | 0.999998 | 32 | 300 Tgas per call (fit predicts $n\le39$; ring 40 failed) |
| Ethereum | kgas | 412.07 | 409.37 | 0.999990 | 32 | $2^{24}$ gas per tx, EIP-7825 (fit predicts $n\le39$) |
| Solana | MCU | 2.08 | 9.44 | 0.999993 | 32 | 1.4 M CU per tx, per unit |
| **Substrate** | ms | 6.41 | 5.71 | 0.999998 | 64 | 1.3 s ref_time per extrinsic (fit allows ≈226) |

Substrate's proof_size is $234 + 10.08\,n$ KB, which is pallet-contracts' benchmarked worst-case charge per registry read. Ring 64 uses 372 ms (29% of the limit); a ring-72 submit is rejected on chain with `RingAboveMax`.

**Mainnet economics:**

| chain | token | block (s) | 1 h tempo (blocks) | USD, n=4 | n=32 | n=64 | ring-4 actions/block |
|---|---|---|---|---|---|---|---|
| Starknet (est.) | STRK | 1.67 | 2 156 | 0.0019 | 0.015 | 0.030 | — |
| NEAR | NEAR | 0.614 | 5 864 | 0.018 | 0.120 | — | 26 |
| Ethereum | ETH | 12 | 300 | 3.11 | 20.33 | — | 26 |
| Solana | SOL | 0.218 | 16 518 | 0.026 | 0.173 | — | 2 |
| **Substrate (Astar)** | ASTR | 6.3 | 572 | 0.0060 | 0.038 | 0.075 | 50 |

Astar's fee is linear in ref_time: 327.4 ASTR per second of weight, times a fee multiplier of 0.1 that day, plus a length fee; proof_size is not charged.

**Observations.** (1) For every chain fitted, cost is linear in the anonymity set: doubling the set roughly doubles the fee. (2) Without a STARK-curve precompile and at L1 gas prices, Ethereum is 600 to 1 900 times dearer than Substrate or Starknet. (3) NEAR and Substrate run the *same* verifier source; per member, Substrate uses 0.44% of its limit against NEAR's 2.5%. (4) Settle is cheap everywhere: at most 101 histogram reads, and no signature.

## 8. Reducing cost without giving up privacy

An epoch costs $M\,\mathrm{sub}(n) + VM\,\mathrm{sig}(n) + M\,\mathrm{set} + M\,\mathrm{clm}(n)$. The score term grows with miners × validators and each term is a full ring verification, so it dominates at scale.

- **Free levers (leak nothing).** Longer epochs (cost/day ∝ 1/hours). Ring size as a privacy *floor*: cost is linear in $n$, so pick the smallest set you accept, enforce it as $n_{\min}$, and let the fixed partition give everyone exactly that set. Chain choice (≈1 400× between Ethereum L1 and Starknet). Relay batching (saves only base fees, but mixes senders).
- **Cuts that link actions.** *Batch* (one signature per validator over its score vector, like `set_weights`) links one validator's scores into a vector that can fingerprint it across epochs. *Lazy* claims link $K$ of a miner's epoch tags. *Proof* inherits both.
- **Cuts that keep (P5).** *Secret committees*: a validator may score tag $\tau$ only if $H(\nu) < (C/V)\,p$, where $\nu$ is the nullifier it publishes anyway. The committee is hidden until it acts, verifiable by anyone, cannot be ground (key fixed, scope carries an epoch beacon), and leaves ≈$C$ scores per tag instead of $V$. *Private settlement*: every submit, score and claim is verified off chain, and one validity proof per epoch lands on chain. Epoch-scoped images are checked inside the proof and never stored. Scores themselves need not be published, which is more private than today. The prover can delay an epoch but cannot forge one, and the direct path stays as a fallback.

USD per day, $M=256$, $V=64$, $n=16$, 1 h epochs, $K=24$, $C=32$ (only "today" is measured; the rest is modeled in `src/core/forecast.py`; * = proof priced at 30 ring-16 scores):

| chain | today | batch | +lazy | +proof | committee | +private | saving |
|---|---:|---:|---:|---:|---:|---:|---:|
| Starknet | 3052.81 | 260.13 | 219.10 | 8.55 | 1574.04 | 9.93 | 307× |
| NEAR | 24952.88 | 995.51 | 624.83 | 20.90 | 12867.55 | 7.53 | 3313× |
| Ethereum | 4261949.26 | 185464.82 | 117201.16 | 3887.86 | 2203365.91 | 1223.55 | 3483× |
| Solana | 36074.28 | 1268.90 | 777.18 | 25.12 | 18553.49 | 6.25 | 5776× |
| Substrate* | 8051.55 | 302.49 | 187.43 | 10.47 | 4146.63 | 7.13 | 1129× |

**Committees cost security, not privacy.** The quorum becomes $\lceil\max(C,n_\tau)/2\rceil$ per committee. A coalition with fewer than $C/2$ members can never reach it. With $V=64$, $C=32$ and 25% colluding, a tag is captured with probability $2.2\times10^{-7}$ and starved (the coalition abstains, too few honest members are drawn) with probability 0.0066. With $C=16$ these become 0.0041 and 0.061. Starved tags can be re-scored by a retry committee. Once verification is off chain, linking buys almost nothing: +private costs the same order as +proof. **Recommended path: committees, then private settlement.**

## 9. Limitations

- **The anonymity set is the ring.** A ring of 4 hides one validator among 4. Padding is impossible, but no contract can create members.
- **Ring intersection: a leak in our earlier ring selection, now fixed.** A claim shares its image with its submission. The client used to sample a *fresh random* ring per action whenever a set exceeded the maximum ring, so the two rings could be intersected and the claimant's anonymity fell from $n$ to about $n^2/|\mathcal{M}|$, or to 1 in the worst case. The client now cuts each registry into fixed blocks of $n$, in registration order, with the last block borrowing to stay full (`networks/base.py: partition_ring`). Both linked actions therefore show the same $n$ keys. Two things remain: a reshuffle of the last block between submit and claim, and on-chain enforcement of canonical rings.
- **Metadata.** The relayer, timing, network addresses and recognisable work can each identify a member.
- **Collusion and Sybils.** Proposition 4 assumes fewer than half of the snapshot validators collude; admission policy decides whether that holds.
- **No quantum security.** Shor's algorithm breaks unforgeability and anonymity (DLP and DDH). The postquant deployment's ML-DSA receipts protect the history, not anonymity. Lattice-based linkable ring signatures or STARK membership proofs are the main open direction.
- **The cost reductions are modeled.** Committees and private settlement are not deployed; committees trade the deterministic sub-quorum bound for the probabilistic one above, and private settlement adds a prover that can delay but not forge an epoch.
- **Validator rewards** that preserve nullifier unlinkability are future work.
- **Shielded transfers (planned).** Token payouts will run on a ztensor L1 built from the postquant system, where every wallet is shielded automatically: balances are SHA3 note commitments in a Merkle tree, spends reveal nullifiers $\mathrm{SHA3}(sk,\rho)$ with a hash-based STARK of membership and value balance, and spend authority is ML-DSA. A claim mints directly into a shielded note. Scores and per-tag claim sizes stay public. The STARK is the same component as the post-quantum membership proof above.
- **Threats to validity.** Costs are local measurements; Starknet's USD is an estimate and extrapolated from ring 4; ETH figures scale with the base fee; the proofs are sketches with Poseidon modelled as a random oracle; a bug shared by the signer and every verifier would not show up as cross-chain disagreement.

## Reproduce

```bash
python3 -m pytest                                 # offline: codecs, adapters, Zuma, ring rule
ZTENSOR_NATIVE=1 python3 -m pytest -m native      # snforge, cargo, forge suites
ZTENSOR_LIVE=1   python3 -m pytest -m live        # one full epoch per local chain
m ztensor/simulate chain=substrate                # epoch on substrate-contracts-node :51337
m ztensor/gas_sweep chain=substrate sizes=4,8,16,32,48,64
python3 paper/collect.py && ./paper/build.sh      # snapshot -> tables, figures, PDF
```
