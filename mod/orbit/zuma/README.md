# zuma: anonymous subnets on Starknet and NEAR

zuma runs a Bittensor-style subnet where **miners and validators don't know
each other.** Each one only knows that the other is in the registered set.

- The **sets are public.** The owner (or anyone, while registration is open)
  registers STARK-curve public keys as `miner` or `validator`.
- Every **action is a ring signature** over a ring of members of the right
  role. The proof says "one of these N miners/validators signed this" and
  nothing more. A Starknet contract verifies it with the native EC builtin.

| action | who | proves | key image is… | enforces |
|---|---|---|---|---|
| `submit(ring, work, sig)` | miner | one of the miners, for `(subnet, epoch)` | the miner's **tag** for this epoch | one submission per miner per epoch |
| `score(ring, tag, score, sig)` | validator | one of the validators, for `(subnet, epoch, tag)` | a **nullifier** | one score per validator per tag |
| `claim(epoch, ring, to, sig)` | miner | same key as the tag (same scope gives the same image) | the tag again | one claim per tag; credits `emission · tag_sum / epoch_total` to `to` |

Validators score tags. They never learn which miner is behind a tag. Tags in
different epochs can't be linked to each other, and they can't be linked to
the miner's key. Miners see their scores but never learn who gave them. When
one validator scores two different tags, the two scores can't be linked to
each other either. Payouts are **public books**: every credit is visible, but
nobody can tell which member it belongs to.

## Two chains, one protocol

| | Starknet | NEAR |
|---|---|---|
| contract | `contracts/src/zuma.cairo` | `near/src/lib.rs` (near-sdk 5) |
| verifier | `lsag.cairo` on the native EC builtin | `lsag.rs` + `fp.rs` + `poseidon.rs`, in wasm |
| local chain | starknet-devnet :51332 | near-sandbox :51333 |
| ring ≤ | 64 | 32 (gas: ~7.5 Tgas/member, 300 Tgas per call) |
| verify, ring 4 | ~15.8k steps, 19 ec_op | ~37 Tgas |
| tests | snforge (10) | cargo test (25, incl. Cairo fixtures + fast-vs-reference) |

One prover (`prover/`, Cairo) signs for both. On NEAR the scope comes from
the contract (`mine_scope` / `score_scope` views; subnet felt =
sha256(account id)), and the signature is identical. Every write fn takes
`network=devnet|sepolia|mainnet|near|near-testnet`; `simulate chain=near`
runs a full epoch on the sandbox. WHITEPAPER.md explains the scheme and how
the NEAR verifier went from 214 to ~37 Tgas.

**NEAR build traps (all handled by `near_chain.build`):** modern rustc's std
carries bulk-memory + overlong call_indirect → `PrepareError:
Deserialization`; `tools/wasmlower` lowers and validates to MVP. Without
`--cfg near`, near-sdk 5.2x panics are a bare `unreachable` trap and sha256
runs in wasm. Sandbox txs must reference the OPTIMISTIC head (after
`sandbox_fast_forward` the final block is outside the validity window).

## The scheme

LSAG (Liu–Wei–Wong) over the STARK curve, with Poseidon challenges:

```
I   = x · Hp(scope)                     key image (Hp: try-and-increment poseidon -> curve x)
L_i = s_i·G + c_i·P_i
R_i = s_i·Hp(scope) + c_i·I
c_{i+1} = poseidon('zuma-chal', poseidon('zuma-msg', scope, msg, I, ring), L_i, R_i)
valid  iff  the ring closes: c_n == c_0
```

`contracts/src/lsag.cairo` is the only implementation. The contract verifies
with it. `prover/` is a Cairo program that links the **same module** to
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
  IP and work content can also identify a miner. zuma hides the key, not
  your behaviour.
- **Collusion.** A validator who is also a miner knows its own tag and could
  score it high. Scores are summed, so an honest majority dominates. A
  Yuma-style clipped median is a future upgrade.

## Use

```bash
m zuma/build                     # contract (Sierra+CASM) + prover
m zuma/test                      # snforge (10) + cargo test (25); chain=starknet|near for one
m zuma/simulate                  # fresh local devnet, deploy, 4+4 members, submit, score, claim (~20s)
m zuma/simulate chain=near       # the same epoch on near-sandbox (~75s)
m zuma/gas_sweep                 # NEAR gas per ring size -> data/near_gas.json

m zuma/keygen name=alice         # random secret -> data/keys.json (0600); prints the public key
m zuma/deploy network=devnet tempo=50 min_ring=3 emission=1000
m zuma/register name=alice role=miner
m zuma/submit name=alice work="sha of my model"         # -> tag
m zuma/score  name=val1 tag=0x… score=87
m zuma/tally                                            # tags + sums, never identities
m zuma/claim  name=alice epoch=3 recipient=0x…
m zuma/mytag  name=alice epoch=3                        # only the key holder can compute it
```

### Console

`server.py` is one stdlib process on **:50121** (pm2 `zuma`). It serves the
console at `/zuma/` and the JSON API at `/zuma/api/<fn>`; the console's own
calls go to `/zuma/_api/<fn>`.

- **Stage.** It draws both public sets as rings. Each submit pulses the whole
  miner ring, never one node, and drops an anonymous tag. Each score pulses the
  validator ring and fills that tag's bar. A claim pays the payee, and the
  replayed claim flashes red when the contract rejects it.
- **Run an anonymous epoch** calls `simulate` on the local devnet with
  throwaway `sim-*` keys and streams each on-chain step (`GET sim?since=N`).
  **Replay last epoch** re-animates `data/last_sim.json`.
- **Who can call what.** Reads (`board`, `tally`, `members`, `epoch`,
  `last_sim`, ...), off-chain `verify`, and `simulate` are public. Public
  `simulate` runs one at a time, has a 60s cooldown (`ZUMA_SIM_COOLDOWN`) and
  allows at most 8 members per set. Everything that touches `data/keys.json`
  or spends from an account (keygen, sign, submit, score, claim, register,
  deploy, devnet, ...) is loopback-only: it must come from 127.0.0.1 and
  carry no proxy headers.
- **Reads.** Views use raw `starknet_call` (selector = starknet_keccak).
  `board` takes about 0.3s, where it used to cost one sncast process per read.

```bash
python3 server.py 50121          # or: m zuma/serve
python3 -m pytest tests -q       # server routing + gates (needs the devnet deployment for board)
```

### Deploying to Sepolia or mainnet

```bash
sncast account create --name zuma-owner --url <rpc>   # then fund it with STRK and `sncast account deploy`
m zuma/deploy network=sepolia account=zuma-owner relayer=zuma-relay tempo=360 min_ring=8
```

The default RPCs are publicnode, which you can override with `rpc=` or with
`ZUMA_RPC_SEPOLIA` / `ZUMA_RPC_MAINNET`. Fees are paid in STRK. sncast 0.31
speaks RPC 0.7. The accounts file defaults to
`~/.starknet_accounts/starknet_open_zeppelin_accounts.json`.

## Layout

```
contracts/   Scarb pkg `zuma`: src/lsag.cairo (scheme), src/zuma.cairo (contract), tests/
near/        crate `zuma_near`: lsag.rs (port), lib.rs (contract), fp.rs + poseidon.rs (fast wasm math), tests.rs, bench.rs
near_chain.py  NEAR driver: borsh+ed25519 txs, near-sandbox :51333, build -> wasmlower -> deploy
tools/       wasmlower (wasm -> MVP for nearcore), gen_fp.py (generates fp.rs core + poseidon.rs)
WHITEPAPER.md  the paper (also the console's WHITEPAPER tab)
prover/      Cairo program over zuma::lsag; ops: keygen, sign, verify, image, scope
mod.py       anchor: keys, prover bridge, devnet on :51332, sncast deploy/invoke, RPC views
server.py    console + JSON API on :50121 (public reads, loopback-only key ops)
static/      index.html, the console (no build step)
tests/       server tests
data/        keys.json (secrets), deployments.json, devnet/  (gitignored)
```

## Scope of v0.2

- Credits are **points** recorded in contract storage (`earned(addr)`). No
  token is transferred. A real emission token can pay out against `earned`
  later.
- The console is read-mostly: anything that touches member keys stays on the anchor host.
- Epochs follow block number (`epoch = block / tempo`). A submission and its
  scores must land in the same epoch.
