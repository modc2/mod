# zuma: anonymous subnets on Starknet

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
m zuma/test                      # snforge: 10 tests, soundness + full epoch + every rejection
m zuma/simulate                  # fresh local devnet, deploy, 4+4 members, submit, score, claim (~45s)

m zuma/keygen name=alice         # random secret -> data/keys.json (0600); prints the public key
m zuma/deploy network=devnet tempo=50 min_ring=3 emission=1000
m zuma/register name=alice role=miner
m zuma/submit name=alice work="sha of my model"         # -> tag
m zuma/score  name=val1 tag=0x… score=87
m zuma/tally                                            # tags + sums, never identities
m zuma/claim  name=alice epoch=3 recipient=0x…
m zuma/mytag  name=alice epoch=3                        # only the key holder can compute it
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
prover/      Cairo program over zuma::lsag; ops: keygen, sign, verify, image, scope
mod.py       anchor: keys, prover bridge, devnet on :51332, sncast deploy/invoke/call
data/        keys.json (secrets), deployments.json, devnet/  (gitignored)
```

## Scope of v0.1

- Credits are **points** recorded in contract storage (`earned(addr)`). No
  token is transferred. A real emission token can pay out against `earned`
  later.
- There is no web console yet. Everything runs through the anchor (`route:false`).
- Epochs follow block number (`epoch = block / tempo`). A submission and its
  scores must land in the same epoch.
