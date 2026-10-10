# NearTensor

**Subnets that pay for verifiable truth about NEAR, with consensus you can swap.**

## 1. The problem

Apps, bridges and agents that read NEAR state trust a single RPC provider. If
that provider lags, lies or goes down, everything built on it does too. Paying
one more provider doesn't fix that. What's missing is a market where many
independent parties answer the same question, and answers are checked before
anyone gets paid.

## 2. The idea in one paragraph

A **subnet** is a small market with one job. **Miners** answer questions about
NEAR chain state. **Validators** ask questions, check every answer themselves
at the exact block the miner pointed to, score it, and publish weights. A
**consensus rule** turns all validators' weights into one incentive number per
miner. The miners that get paid are the ones that are right, fresh and fast.
Anyone can create a new subnet and choose its tasks and its consensus rule.

## 3. Subnets

A subnet is one record:

| field | meaning |
|---|---|
| `netuid` | its number (0 is the genesis subnet, `neartensor`) |
| `name` | a human label, unique |
| `tasks` | which questions its validators ask |
| `consensus` | which rule turns weights into incentive |
| `neurons` | registered miners and validators (uid, hotkey, stake, endpoint) |

Create one with `m neartensor sn_create name=fast-headers tasks=block_header consensus=winner`
or the **Create subnet** form in the app. By default the box's own miner and
validator join right away, so the next epoch scores it.

Locally each subnet is one JSON file under `data/subnets/`. On real Bittensor
(`subnet.network = test | finney`) a subnet is created with `btcli subnet create`
and its netuid goes in `config.json`; the same miner and validator code then
runs against subtensor.

## 4. Tasks: answers you can check

Every answer has to name a concrete NEAR `block_hash`. That one rule is what
makes checking possible: the validator asks its **own** RPC for the same block
and expects the same bytes back.

| task | the miner returns |
|---|---|
| `block_header` | height, hash, prev hash, epoch id, timestamp of the final block |
| `gas_price` | gas price at that block |
| `account_state` | balance, locked, storage of an account the validator picks |

The miner signs `sha256(task, nonce, answer)` with its hotkey. The nonce comes
from the validator, so an old answer can't be replayed.

## 5. Scoring

```
score = correct × (0.7 × freshness + 0.3 × latency)
```

- **correct** is 0 or 1 and gates everything. A wrong, unsigned or
  wrong-hotkey answer scores 0 however fast it was.
- **freshness** drops linearly from 1 to 0 as the anchored block falls up to
  30 blocks behind the validator's final block.
- **latency** is 1 within 2 seconds and falls with the square of the overshoot.
- If the validator's own RPC is down, the sample is skipped rather than
  counted against the miner.

Scores are smoothed per subnet with an EMA (alpha 0.3), so one bad epoch
doesn't wipe a miner out, then normalised into weights.

## 6. Modular consensus

Validators can disagree, and some may be dishonest. The consensus rule decides
how their weights become incentive. Each rule is one small file in
`src/ntsubnet/consensus/` with a `NAME`, an `ABOUT` line and a single function:

```python
def run(weights, stakes) -> {uid: incentive}
```

The rules that ship:

| rule | how it works | good for |
|---|---|---|
| `yuma` (default) | clip each validator's weight at the stake-weighted median, then average by stake | open subnets where a lone validator might try to pump a friend |
| `mean` | plain stake-weighted average | small, trusted validator sets |
| `winner` | run yuma, then give 100% to the top miner | races where only the best answer matters |

To add a rule, drop a new file in that folder. It is picked up automatically
and any subnet can switch to it with `sn_set_consensus netuid=N consensus=<name>`.
The switch takes effect from the next epoch. On real subtensor the chain runs
its own Yuma, so the local rule doesn't apply there.

## 7. Epochs

1. The validator reads the subnet's metagraph and finds miners that are serving.
2. For each miner it sends `sample_size` tasks drawn from the subnet's task list.
3. It checks each answer: signature, hotkey, the right account, then the bytes at the anchored block.
4. It updates the EMA scores and normalises them into weights.
5. `set_weights` stores the weights and runs the subnet's consensus rule to get each miner's incentive.

A single validator process loops over **every** subnet, and a single miner
serves them all, since it can answer any task.

## 8. Security notes

- **Lying** fails the byte-equal check, so it scores 0.
- **Replaying** fails because the nonce is in the signed digest.
- **Impersonation** fails because the answer must be signed by the registered hotkey.
- **A pumping validator** is held to the median under `yuma`. Under `mean` it isn't, so pick `mean` only when you trust your validators.
- **Spam subnets**: local creation is capped (`subnet.max_subnets`, default 32).
  On NEAR, subnet registration costs a time-locked stake (BlocTime, below).

## 9. The NEAR side

The module also ships NEAR contracts (`src/contracts/`): a **registry** where
registering a subnet means locking NEAR for a while. The lock earns
stake × seconds ("BlocTime") and has to clear a minimum, 1 NEAR for 30 days by
default. A **subnet** contract handles staking, check-ins and on-chain
consensus variants. A **governance** token completes the set. So both halves
point the same way: Bittensor-style subnets that check NEAR, and NEAR contracts
that host Bittensor-style subnets.

## 10. Keeping it simple

- One folder, `src/`, holds everything: `mod.py`, `ntsubnet/`, `api/`, `app/`, `contracts/`.
- Local-first by default, with no wallet, no tokens and no outside service
  beyond public NEAR RPC (which rotates across several providers).
- Each concern is one file: protocol, reward, chain, consensus rules, miner, validator.
