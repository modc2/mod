# ztao: anonymous subnets with Zuma consensus, on any chain

ztao runs a Bittensor-style subnet where **miners and validators don't know
each other.** Each one only knows that the other is in the registered set.
Emission is split by **Zuma consensus**, computed on chain.

- The **sets are public.** The owner (or anyone, while registration is open)
  registers STARK-curve public keys as `miner` or `validator`.
- Every **action is a ring signature** over a ring of members of the right
  role. The proof says "one of these N miners/validators signed this" and
  nothing more. A Starknet contract verifies it with the native EC builtin.

| action | who | proves | key image is… | enforces |
|---|---|---|---|---|
| `submit(ring, work, sig)` | miner | one of the miners, for `(subnet, epoch)` | the miner's **tag** for this epoch | one submission per miner per epoch |
| `score(ring, tag, score, sig)` | validator | one of the validators, for `(subnet, epoch, tag)` | a **nullifier** | one score per validator per tag |
| `settle(epoch, tag)` | anyone | — | — | Zuma consensus for one tag once the epoch closes: consensus + clipped rank |
| `claim(epoch, ring, to, sig)` | miner | same key as the tag (same scope gives the same image) | the tag again | one claim per tag, after every tag is settled; credits `emission · rank / Σ rank` to `to` |


## What ztao adds over ztensor (v0.5)

ztao is a fork of `ztensor` 0.4. It uses the same contracts, signer and adapters, on its own ports.

- **Pre-flight guard** (`src/core/guard.py`). Before anything is signed,
  `submit` / `score` / `claim` refuse actions the chain would revert: a ring
  below `min_ring`, a score that isn't an integer from 0 to 100, a score for
  a tag with no submission this epoch, or a claim before every tag is settled
  (or one already claimed). A reverted tx still costs the relayer its fee.
  Pass `check=False` to skip the guard.
- **`preflight(action, name, ...)`** runs the same checks as a dry run and
  returns `{ok, reason}`. It needs loopback, because it uses key names.
- **`anonymity(role, network)`** is a public read. It reports how big a
  crowd each member hides in: ring size, number of rings, bits, and warnings.
- Ports: API/console :50122, starknet-devnet :51502, near-sandbox :51503
  (p2p :51501), anvil :51504, postquant :51506, substrate :51507,
  solana :51510. Env vars are `ZTAO_*`.

## Zuma consensus

Validators are anonymous, so there is no per-validator stake. A ring is one
stake tier, and **membership is the stake**: every validator weighs 1. With
κ = ½:

- **Consensus** `C` for a tag is the highest score that at least half of the
  validator set gave it. A validator who didn't score the tag counts as 0.
- **Rank** `R = Σ min(score, C)`: every score is clipped to consensus.
- A miner's share is `emission · R / Σ R` over the epoch's tags.

A minority can't raise consensus. One validator pumping a tag from 10 to 100
adds nothing, because three others said 10. The validator set `V` is
snapshotted at the epoch's first submit, so registering validators late
can't move payouts.

`score` adds to a 0..100 histogram per tag. `settle` walks the histogram down
from the top score until half the set is covered. That takes at most 101
reads, whatever the set size. `src/core/yuma.py` is the reference that every
chain's contract is tested against, and `m ztao/yuma scores='{"a":[90,90,90,100]}'
validators=4` runs it by hand. Validator dividends and bonds are not paid:
bonds need one validator identity across rounds, which anonymity removes (see
`cairo.zuma` for per-round bonds with stake-tier rings).

Validators score tags. They never learn which miner is behind a tag. Tags in
different epochs can't be linked to each other, and they can't be linked to
the miner's key. Miners see their scores but never learn who gave them. When
one validator scores two different tags, the two scores can't be linked to
each other either. Payouts are **public books**: every credit is visible, but
nobody can tell which member it belongs to.

## One signer, a verifier per chain

Every chain is an **adapter** in `src/networks/<chain>/`. They all verify the
same signature, which one Cairo prover (`src/core/prover/`) makes.

| | Starknet | NEAR | Ethereum | Solana | postquant |
|---|---|---|---|---|---|
| contract | Cairo (`ztao.cairo`) | Rust/wasm (near-sdk 5) | Solidity + Yul | Rust sBPF program | Rust pqc1 wasm, run off chain |
| curve math | native EC builtin | `fp.rs`, 9x29-bit limbs | `StarkCurve.sol` in Yul | NEAR's `fp.rs`, in units | NEAR's `fp.rs` |
| local chain | starknet-devnet :51502 | near-sandbox :51503 | anvil :51504 | solana-test-validator :51510 | own postquant node :51506 |
| ring ≤ | 64 | 32 (300 Tgas/call) | 32 (EIP-7825) | 32 (session layout) | 64 (5 s replay budget) |
| verify, ring 4 | ~15.8k steps, 19 ec_op | ~37 Tgas | ~2.1M gas | ~40M CU over ~41 units | ~5 ms wasm + one 44.7k-gas ML-DSA anchor |
| native tests | snforge (10) | cargo (25) | forge (11) | cargo (8) | cargo (3) |

Every write fn takes `network=` any adapter's net (`devnet|sepolia|mainnet|
near|near-testnet|eth|eth-sepolia|solana|solana-devnet|postquant|postquant-1`); `simulate
chain=<chain>` runs a full epoch on that chain's local net. Only the scope's
`subnet` felt is chain-specific, so adapters read it from their contract.
**Adding a chain = adding a package**: see `src/networks/README.md`.

**NEAR build traps (all handled by `src/networks/near/client.py`):** modern rustc's std
carries bulk-memory + overlong call_indirect → `PrepareError:
Deserialization`; `near/tools/wasmlower` lowers and validates to MVP. Without
`--cfg near`, near-sdk 5.2x panics are a bare `unreachable` trap and sha256
runs in wasm. Sandbox txs must reference the OPTIMISTIC head (after
`sandbox_fast_forward` the final block is outside the validity window).

## Post-quantum: ztao on postquant

`src/networks/postquant/` runs the subnet on [postquant](../postquant), the
fleet's post-quantum L1 (ML-DSA / SLH-DSA signatures, SHA3-256 everywhere, no
elliptic curve in its trust path). postquant does not execute contracts on
chain, so the subnet contract is a **pqc1 contract**: one importless wasm
blob, `pqc_run(state, input) -> next state`, a pure function
(`contract/src/lib.rs`; the LSAG, field and Poseidon code are NEAR's files,
included by path, so the same signatures verify).

- The contract owner (the relayer, as on every other chain) runs each action
  off chain. The node signs a **pqr1 receipt** `{code, state_in, input,
  state_out, prev}` with an ML-DSA-44 wallet and anchors its SHA3 hash at
  `contract/<name>/receipt`. Each action costs one block.
- Anyone checks the books by replaying: fetch the blobs by hash, re-run the
  wasm, compare. `simulate chain=postquant` ends with an **audit**: every
  receipt of the run is replayed, its ML-DSA signature checked, and each one's
  `state_in` linked to the last one's `state_out`. A receipt that accepted a
  bad ring signature, a double score or a double claim does not replay.
- There is no clock inside a pure function. Each input carries the block
  height the runner claims; it can never go backwards, and the receipt's
  anchoring block is the public check on it. `epoch = height / tempo`.
- State is one blob under the protocol's 48 KiB output cap. The last 8
  closed epochs are kept (claim inside that window), and nullifiers are
  dropped once their epoch closes.

**What is quantum-safe and what is not.** The execution record is: receipts
are ML-DSA (FIPS 204), commitments are SHA3-256, and a quantum adversary can
neither forge a run nor rewrite the history. **The anonymity layer is not.**
It is still LSAG over the STARK curve, the same as on the other four chains.
An adversary that can solve discrete logs could forge ring signatures and
compute `x` from a key image `I = x·Hp(scope)`, which links tags and
nullifiers back to registered keys. That includes every image already
published, so recorded anonymity does not survive it. Making membership
proofs post-quantum needs a different scheme (a hash-based STARK proof of
`H(sk)` in a SHA3 Merkle set, with `H(sk, scope)` as the image). That is the
next step, and nothing here claims it.

Networks: `postquant` = our own node (postquant's `src/api.py` with its data
and keystore under `data/postquant/`, chain id `ztao-pq`, block loop parked
so only our writes and `advance()` move the clock; wallets `relayer` (funded
from the genesis treasury) and `payee`). `postquant-1` = any running postquant
node: `ZTAO_PQ_URL` (default the fleet's :51030), `ZTAO_PQ_TOKEN`
(its write bearer) and `ZTAO_PQ_WALLET` (a funded wallet there). That path
is written but not exercised. No USD view, because postquant has no mainnet
token price.

## Transfers: shielded wallets by default (planned)

Today no token moves (credits are points, see *Scope*). When transfers ship,
they will not go out on the four public chains. They will run on a **ztao
L1**, a dedicated chain built from the postquant system (its modular consensus,
ML-DSA / SLH-DSA keys, SHA3-256 commitments). On that chain **every wallet is
shielded automatically**. There is no transparent address, no public balance
and no opt-in step:

- **Balances are notes.** A note is a commitment
  `cm = SHA3(value, owner_pk, rho, r)` appended to a SHA3 Merkle tree. The
  chain stores commitments, never amounts or owners.
- **Spending** publishes a nullifier `nf = SHA3(sk, rho)` (one per note, so no
  double spend) and new output notes, with a proof that the input note is in
  the tree and that value in equals value out. Spend authority is an ML-DSA
  signature, so no step relies on an elliptic curve.
- **Payouts land shielded.** A Zuma `claim` mints its amount straight into a
  note for the miner's shielded address. `earned` never passes through a
  public account, so the payout is not linked to the miner's registered key,
  and later transfers are not linked to the claim.
- **What stays public.** The subnet's books do not change: scores, the
  emission split and the size of each claim per tag are still on chain.
  Shielding hides who received a payout and where it went afterwards, not how
  much a tag earned.

Status: **design, not built.** The proof needs a hash-based STARK over SHA3
(membership in the note tree plus value balance). That is the same component
the post-quantum anonymity layer needs (a STARK of `H(sk)` in a SHA3 Merkle
set), so both land together. Until then the postquant adapter keeps credits as
points, and no wallet code exists in this module.

## The scheme

LSAG (Liu–Wei–Wong) over the STARK curve, with Poseidon challenges:

```
I   = x · Hp(scope)                     key image (Hp: try-and-increment poseidon -> curve x)
L_i = s_i·G + c_i·P_i
R_i = s_i·Hp(scope) + c_i·I
c_{i+1} = poseidon('zuma-chal', poseidon('zuma-msg', scope, msg, I, ring), L_i, R_i)
valid  iff  the ring closes: c_n == c_0
```

`src/networks/starknet/contract/src/lsag.cairo` is the reference. The Starknet
contract verifies with it. `src/core/prover/` is a Cairo program that links the **same module** to
handle keygen, signing and verification off-chain, run through
`scarb cairo-run`. The Python anchor never touches curve math, so the signer
and the verifier can't drift apart.

## The set-size limitation (read this)

Your anonymity set is the ring, and nothing more:

- **Small sets leak.** With 3 validators, each score has a 1-in-3 chance of
  pointing to any given one. With 1 validator there is no anonymity at all.
  The contract enforces `min_ring` (set at deploy) and rejects rings with
  duplicate keys (key ids must be strictly increasing). It **can't** create
  members who don't exist.
- **Big rings cost gas, and the cost grows linearly.** A ring of 4 costs
  about 16k Cairo steps and 19 `ec_op` per verify (measured on devnet).
  Rings are capped at **64** (`MAX_RING`). That gives ~250k steps, which is
  well inside Starknet's limits but costs real fees. Default rings use every
  member, up to 64.
- **Metadata still leaks.** The Starknet tx sender is public, so a member must
  never post from their own registered account. All actions go through a
  **relayer** account, because the contract never checks the caller. Timing,
  IP and work content can also identify a miner. ztao hides the key, not
  your behaviour.
- **Collusion.** A validator who is also a miner knows its own tag and could
  score it high. Zuma consensus clips that score to what half the set agrees on, so it
  only counts if a majority colludes.

## Use

```bash
m ztao/networks                  # every chain adapter: nets, ring cap, cost unit
m ztao/build                     # prover + every chain's contract; chain=<chain> for one
m ztao/test                      # every chain's native suite; chain=<chain> for one
m ztao/simulate                  # fresh local devnet, deploy, 4+4 members, submit, score (one pumped), settle (Zuma consensus), claim
m ztao/settle epoch=3             # anyone: run Zuma consensus for every unsettled tag of a closed epoch
m ztao/simulate chain=near       # the same epoch on near-sandbox (~75s); eth, solana too
m ztao/gas_sweep chain=near      # cost per ring size on any chain -> data/<chain>_gas.json
m ztao/economics fresh=1         # mainnet USD per action + 1 h epoch limits (live gas+price, 10 min cache)

m ztao/keygen name=alice         # random secret -> data/keys.json (0600); prints the public key
m ztao/deploy network=devnet tempo=50 min_ring=3 emission=1000
m ztao/register name=alice role=miner
m ztao/submit name=alice work="sha of my model"         # -> tag
m ztao/score  name=val1 tag=0x… score=87
m ztao/tally                                            # tags + sums, never identities
m ztao/claim  name=alice epoch=3 recipient=0x…
m ztao/mytag  name=alice epoch=3                        # only the key holder can compute it
```

### Console

`src/server.py` is one stdlib process on **:50122** (pm2 `ztao`). It serves the
console at `/ztao/` and the JSON API at `/ztao/api/<fn>`; the console's own
calls go to `/ztao/_api/<fn>`.

The console is a Next.js app in `src/app/` (static export, no node at runtime):
`src/app/build.sh` builds it and swaps `src/app/dist` atomically. It **adapts to
the adapters**: chain buttons, theme color, nets, ring caps and cost columns all
come from `GET networks`, so a new `src/networks/<chain>/` gets every tab for free.
Tabs: LIVE (run/replay an epoch, ring stage + Zuma consensus bars + event log), SUBNET
(board: params, epoch clock, member sets, recent epochs), COSTS (economics + ring sweep vs chain limit),
CODE, TESTS, PAPER.

- **Stage.** It draws both public sets as rings. Each submit pulses the whole
  miner ring, never one node, and drops an anonymous tag. Each score pulses the
  validator ring and fills that tag's bar. A claim pays the payee, and the
  replayed claim flashes red when the contract rejects it.
- **Run an anonymous epoch** calls `simulate` on the local devnet with
  throwaway `sim-*` keys and streams each on-chain step (`GET sim?since=N`).
  **Replay last epoch** re-animates `data/last_sim.json`.
- **Who can call what.** Reads (`board`, `tally`, `members`, `epoch`,
  `last_sim`, ...), off-chain `verify`, and `simulate` are public. Public
  `simulate` runs one at a time, has a 60s cooldown (`ZTAO_SIM_COOLDOWN`) and
  allows at most 8 members per set. Everything that touches `data/keys.json`
  or spends from an account (keygen, sign, submit, score, claim, register,
  deploy, devnet, ...) is loopback-only: it must come from 127.0.0.1 and
  carry no proxy headers.
- **Reads.** Views use raw `starknet_call` (selector = starknet_keccak).
  `board` takes about 0.3s, where it used to cost one sncast process per read.

```bash
python3 src/server.py 50122      # or: m ztao/serve
python3 -m pytest                # offline: every chain's adapter tests + core + server (~4s)
ZTAO_NATIVE=1 python3 -m pytest -m native   # + snforge / cargo / forge (~1 min)
ZTAO_LIVE=1 python3 -m pytest src/networks/near -m live   # a full epoch on one chain
```

### Deploying to Sepolia or mainnet

```bash
sncast account create --name ztao-owner --url <rpc>   # then fund it with STRK and `sncast account deploy`
m ztao/deploy network=sepolia account=ztao-owner relayer=ztao-relay tempo=360 min_ring=8
```

The default RPCs are publicnode, which you can override with `rpc=` or with
`ZTAO_RPC_SEPOLIA` / `ZTAO_RPC_MAINNET`. Fees are paid in STRK. sncast 0.31
speaks RPC 0.7. The accounts file defaults to
`~/.starknet_accounts/starknet_open_zeppelin_accounts.json`.

## Layout

```
config.json, README.md, WHITEPAPER.md, pytest.ini
data/                  keys.json (secrets, 0600), deployments.json, per-chain local state (gitignored)
src/
  mod.py               the module: keys, dispatch to adapters, simulate, compare, gas_sweep
  server.py            console + JSON API on :50122 (public reads, loopback-only key ops)
  app/                 the console: Next.js static export (basePath /ztao);
                       app/build.sh -> releases/<ts> -> dist symlink, served by server.py
  static/index.html    fallback console while app/dist is unbuilt
  core/
    signer.py          keys + the Cairo prover bridge
    prover/            Cairo program over ztao::lsag (keygen, sign, verify, image, scope, Hp)
    stark.py           curve + Poseidon in Python (hints for Solana, cross-checks)
    util.py            felts, JSON state
  networks/
    base.py            the Network contract every adapter implements
    __init__.py        registry (one package = one chain)
    starknet/          adapter.py, contract/ (Scarb + snforge), tests/
    near/              adapter.py, client.py, contract/ (crate), tools/ (wasmlower, gen_fp), tests/
    eth/               adapter.py, client.py, contract/ (Foundry), tools/ (gen_consts), tests/
    solana/            adapter.py, client.py, contract/ (sBPF crate), tools/ (gen_fixture), tests/
    postquant/         adapter.py, client.py (own node, runs, receipts, state decoder),
                       contract/ (pqc1 crate, reuses near's lsag/fp/poseidon), tests/
    _template/         copy to add a chain
  tests/               server, adapter conformance, core crypto
```

## Scope of v0.2

- Credits are **points** recorded in contract storage (`earned(addr)`). No
  token is transferred. Real payouts are planned on the ztao L1, where
  every wallet is shielded by default (see *Transfers*).
- The console is read-mostly: anything that touches member keys stays on the anchor host.
- Epochs follow block number (`epoch = block / tempo`). A submission and its
  scores must land in the same epoch.
