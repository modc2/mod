# charitao — proof-of-donation Bittensor subnet

**[Whitepaper](WHITEPAPER.md)** · live dashboard <https://modc2.com/charitao>
(with in-app [whitepaper](https://modc2.com/charitao/whitepaper) and
read-only [code browser](https://modc2.com/charitao/code))

Miners **mine by donating**: they send TAO from their registered coldkey to
whitelisted charity addresses. Validators watch the chain, verify the
transfers, and set weights proportional to credited donations — so subnet
emissions flow to the miners that donate the most, and **emissions always
cover donations at a fixed, predictable profit margin**.

## The profit guarantee

Every credited TAO donated earns exactly `1 / coverage_ratio` TAO of miner
emission. With the default `coverage_ratio = 0.8`, donating 1 TAO returns
1.25 TAO — a guaranteed 25% margin. Three mechanisms make the rate exact
(not just a lower bound):

1. **Budget cap** — per epoch, at most `budget = epoch_emission × 0.8` TAO of
   donations is credited. Weights are `credited / budget`, so
   `payout = credited / 0.8`, always.
2. **Burn weight** — if donations under-fill the budget, the residual weight
   goes to `burn_uid` (default 0) instead of being split among donors.
   Without it, one small donor in a quiet epoch would capture the entire
   emission and the "margin" would be a lottery, not a rate.
3. **Carryover queue** — donations beyond the budget are never diluted; they
   queue FIFO by epoch (pro-rata within an epoch) and settle in later epochs
   at the same rate. Nothing donated is ever lost, just settled later.

```
payout  = donation / coverage_ratio        (guaranteed)
profit  = donation × (1/coverage_ratio−1)  (25% at ρ=0.8)
epochs_to_settle ≈ ceil(donation / budget) (if you have the budget to yourself)
```

## Trust model

- **Charity whitelist** is the consensus surface: only transfers to a
  *verified* charity address are credited. Curation is the subnet owner's
  job; every validator must run the same registry or weights diverge. Demo
  entries (`demo://…`) ship so the local sim works — replace them with real,
  verified cold addresses before going live.
- **Miner binding is trustless on chain**: a donation counts only if its
  sender is the coldkey of a registered uid in the metagraph. No axon, no
  query protocol, no signatures to exchange — the transfer itself is the
  proof of work.
- **Anti-gaming**: funds actually leave to charities, so wash-donating buys
  nothing; txids are deduped (replay-proof); dust below `min_donation` is
  ignored; self-dealing requires getting your own address past registry
  curation.

## Usage

```bash
m charitao                                   # status (epoch, ρ, margin, carryover)
m charitao/charities                         # the whitelist
m charitao/add_charity id=x name=X address=demo://x verified=1
m charitao/simulate n_miners=3 epochs=2      # local end-to-end sim
m charitao/donate uid=0 amount=0.2           # sim miner donates (mock chain)
m charitao/epoch                             # one settlement epoch
m charitao/leaderboard                       # donated / credited / payout per uid
m charitao/projection donation=1             # {payout: 1.25, profit: 0.25, ...}
m charitao/suggest_donation expected_donors=4  # size to settle in one epoch
m charitao/reset                             # wipe local sim state
m charitao/test                              # pytest suite (21 tests)
```

## Layout

```
charitao_subnet/
  registry.py    # whitelisted charities (verified-only credit filter)
  chain.py       # MockChain (local JSON ledger) + SubtensorChain (live, pluggable indexer)
  incentive.py   # coverage-ratio budget, burn weight, carryover queue, projections
  miner.py       # donate() = the mining work; suggest_donation() sizing
  validator.py   # scan → verify → credit → weight (+ set_weights on chain)
mod.py           # anchor: CLI/console surface
tests/           # pytest, 21 tests (python3 -m pytest tests/)
```

State lives off-tree in `~/.mod/charitao/` (registry.json, mockchain.json,
incentive_state.json, scan_state.json, epochs/).

## Going live

1. Replace demo charities with real, verified TAO cold addresses
   (`add_charity … network=finney verified=1`) and distribute the registry
   to all validators.
2. Run the validator with `local=False, netuid=N`; it binds miner coldkeys
   from the metagraph and needs a transfer `indexer` (taostats / subquery /
   archive node) wired into `SubtensorChain` — plain RPC nodes can't
   enumerate historical balance transfers.
3. Miners donate straight from their coldkey wallet:
   `btcli wallet transfer --dest <charity> --amount <x>`.
4. `epoch_emission` is estimated from the subnet's emission rate (miner
   share) with the configured value as fallback; `coverage_ratio` and
   `burn_uid` are validator config — keep them identical across validators.
