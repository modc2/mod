# charitao: a proof-of-donation Bittensor subnet

**Version 1.0 · July 2026 · [code](https://github.com/modc2/mod/tree/dev/mod/orbit/charitao)**

## Abstract

charitao is a Bittensor subnet in which the mining work is a verified
charitable donation. Miners send TAO from their registered coldkey to
whitelisted charity addresses; validators observe those transfers on chain,
credit them against a per-epoch budget, and set weights so that subnet
emissions repay every credited donation at an exact, predictable rate:

```
payout = donation / ρ
```

where `ρ` (the *coverage ratio*, default 0.8) is a subnet constant. At
`ρ = 0.8`, donating 1 TAO earns 1.25 TAO of emission — a guaranteed 25%
margin. The guarantee is exact, not a lower bound: a budget cap stops the
rate from deflating, a burn weight stops it from inflating, and a carryover
queue ensures no donation is ever diluted or lost. The result is a subnet
that channels a continuous, incentive-compatible stream of TAO to real-world
charities while paying miners a fixed return for routing it.

## 1. Motivation

Most proof-of-X subnets pay miners for compute, inference, or data. charitao
asks a simpler question: what if the scarce resource a subnet rewards is
*verified altruism*? Bittensor already provides everything needed —

- a **registration mechanism** binding a uid to a coldkey (identity),
- a **public ledger** of balance transfers (verifiable work),
- an **emission schedule** redistributed by validator weights (reward).

A donation from a registered coldkey to a curated charity address is a proof
of work that is trivially verifiable, impossible to fake without real cost,
and socially useful in itself. The only missing piece is an incentive design
that makes the return *predictable* — nobody donates against a lottery. That
design is charitao's contribution.

## 2. Protocol overview

Four roles:

- **Charities** — real-world organizations with published TAO cold
  addresses, curated into a whitelist (the registry).
- **Miners** — registered subnet uids. Mining = transferring TAO from the
  registered coldkey to any whitelisted address. No axon, no query protocol,
  no request/response: the on-chain transfer *is* the submission.
- **Validators** — scan the chain each epoch, verify transfers (sender must
  be a registered coldkey, recipient must be a verified charity), credit
  them through the incentive mechanism, and set weights.
- **Subnet owner** — curates the charity registry and fixes the consensus
  parameters (`ρ`, `burn_uid`, `min_donation`).

An epoch proceeds: scan → verify → credit → weight.

## 3. The incentive mechanism

The invariant is `payout_i = credited_i / ρ` for every miner `i`, every
epoch. Three mechanisms jointly enforce it (`charitao_subnet/incentive.py`):

### 3.1 Budget cap

Each epoch, at most

```
budget = epoch_emission × ρ
```

TAO of donations is credited. Weights are set as `w_i = credited_i / budget`,
so each miner's emission is

```
payout_i = w_i × epoch_emission = credited_i / ρ
```

— exactly the guaranteed rate, by construction. The cap is what makes the
rate a *floor*: without it, an oversubscribed epoch would split a fixed
emission over more donations and dilute everyone.

### 3.2 Burn weight

If donations under-fill the budget, the residual weight `1 − Σ w_i` is
assigned to `burn_uid` (default 0) rather than renormalized across donors.
This makes the rate a *ceiling*: without it, a single small donor in a quiet
epoch would capture the entire emission, and the "margin" would be a lottery
rather than a rate. Burning the residual is what turns "at least 25%" into
"exactly 25%".

### 3.3 Carryover queue

Donations beyond the budget are never discarded or diluted. They enter a
FIFO queue ordered by epoch, pro-rata *within* an epoch: if an epoch's batch
exceeds the remaining budget, every donation in the batch is credited the
same fraction and the remainder stays queued. Queued value settles in later
epochs at the same fixed rate. Nothing donated is ever lost — large
donations simply settle over multiple epochs:

```
epochs_to_settle ≈ ceil(donation / budget)     (at sole use of the budget)
```

### 3.4 Miner economics

For a donation `d` at coverage ratio `ρ`:

| quantity | formula | at ρ = 0.8, d = 1 τ |
|---|---|---|
| payout | `d / ρ` | 1.25 τ |
| profit | `d × (1/ρ − 1)` | +0.25 τ |
| margin | `1/ρ − 1` | 25% |
| settle time | `⌈d / (E·ρ)⌉` epochs | 2 (E = 1 τ, sole donor) |

The margin is deterministic; only settlement *time* varies with congestion.
`suggest_donation(expected_donors)` sizes a donation to settle in a single
epoch given the expected competition for the budget.

## 4. Trust model

- **Miner binding is trustless.** A donation counts only if its sender is
  the coldkey of a registered uid in the metagraph. The chain itself proves
  who donated; there are no signatures to exchange and no server to trust.
- **The charity whitelist is the consensus surface.** Only transfers to a
  *verified* registry entry are credited. Curation is the subnet owner's
  responsibility, and every validator must run the same registry — divergent
  registries mean divergent weights. This is the protocol's deliberate point
  of centralization: verifying that an address truly belongs to a charity is
  an off-chain, human task.
- **Validator config is consensus-critical.** `ρ`, `burn_uid`, and
  `min_donation` must be identical across validators.

### Anti-gaming

- **Wash donations buy nothing** — funds irrevocably leave to charity
  addresses; recovering them requires corrupting an actual charity.
- **Replay-proof** — txids are deduplicated; a transfer credits once, ever.
- **Dust-filtered** — donations below `min_donation` are ignored.
- **Self-dealing** requires getting an attacker-controlled address past
  registry curation, which is exactly the human verification step the
  whitelist exists to perform.

## 5. Honest limitations

- The miner's 25% margin is paid in **subnet emissions** — economically, TAO
  holders' dilution funds both the charity flow and the miner margin. The
  subnet is a mechanism for the network to *direct* emission toward verified
  giving, not a money machine; the margin is denominated in TAO and carries
  TAO's market risk between donation and settlement.
- Registry curation is centralized in the subnet owner (see §4).
- Live deployment needs a transfer **indexer** (taostats / subquery /
  archive node): plain RPC nodes cannot enumerate historical balance
  transfers.

## 6. Architecture

The full implementation is ~1,200 lines of dependency-light Python, with a
zero-dependency web dashboard. Browse it in the **Code** tab of this app, or
on GitHub: <https://github.com/modc2/mod/tree/dev/mod/orbit/charitao>.

```
charitao_subnet/
  registry.py    # charity whitelist — verified-only credit filter
  chain.py       # MockChain (local JSON ledger) + SubtensorChain (live, pluggable indexer)
  incentive.py   # budget cap, burn weight, carryover queue, projections (§3)
  miner.py       # donate() = the mining work; suggest_donation() sizing
  validator.py   # scan → verify → credit → weight (+ set_weights on chain)
mod.py           # module anchor: CLI/console surface
app.py           # this dashboard (zero-dep HTTP + JSON API)
tests/           # pytest suite — 21 tests covering the §3 invariants
```

State lives off-tree in `~/.mod/charitao/` (registry, mock chain ledger,
incentive state, scan cursor, per-epoch results). The local simulation
(`m charitao/simulate`) exercises the identical settlement code path used
live — only the chain backend is swapped.

## 7. Parameters

| parameter | default | role |
|---|---|---|
| `coverage_ratio` (ρ) | 0.8 | payout = donation/ρ; sets the 25% margin |
| `epoch_emission` | 1.0 τ (est. live) | miner-share emission per epoch |
| `burn_uid` | 0 | receives residual weight in under-filled epochs |
| `min_donation` | 0.001 τ | dust filter |
| `tempo` | 300 blocks | epoch length |

## 8. Going live

1. Replace demo registry entries with real, verified TAO cold addresses and
   distribute the registry to all validators.
2. Run validators with `local=False, netuid=N` and a transfer indexer wired
   into `SubtensorChain`.
3. Miners donate straight from their wallet:
   `btcli wallet transfer --dest <charity> --amount <x>`.
4. `epoch_emission` is estimated from the subnet's live emission rate;
   `ρ` and `burn_uid` are fixed validator config.

---

*CLI: `m charitao` · `m charitao/simulate` · `m charitao/projection donation=1` ·
tests: `m charitao/test` (21 passing).*
