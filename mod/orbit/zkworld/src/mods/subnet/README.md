# subnet

Yuma consensus with sealed weights: a Bittensor-style subnet where validators
can't copy each other's weights.

A Cairo contract mod (submod of `zkworld`), addressed `zkworld.subnet`.

## Round

1. **commit**: each validator posts `seal = poseidon('yuma', contract, epoch, who, salt, w_0..w_n)`.
   Weights are not stored on chain yet. The seal is bound to the contract, epoch and validator,
   so another validator can't copy it and open it later, and it can't be replayed in a later epoch.
2. **reveal**: opens when every validator has sealed or the commit window ends. Each validator
   opens their seal. Weights are fixed at commit time, so seeing an earlier reveal doesn't help.
3. **finalize**: anyone can call it once every sealer has revealed or the reveal window ends.
   It runs Yuma and rolls the epoch. A validator who seals but never reveals drops out of that epoch.

## Yuma (fixed point, ONE = 1e9)

- consensus `c_j` = stake-weighted median of normalized weights (kappa = 1/2)
- clip `min(w_ij, c_j)`; incentive `I_j` = stake-weighted clipped rank, normalized
- bonds: EMA (alpha 0.1) toward the column-normalized clipped stake, kept across epochs
- dividends `D_i = sum_j B_ij I_j`, normalized; emission is 50/50 miners/validators

## Privacy scope

Weights stay hidden for as long as they could be copied, then become public. That's the same
guarantee as Bittensor commit-reveal. Who-voted-what stays visible. For anonymous validators
(ring signatures) see `orbit/zuma`. Stake is paper (set by the owner) and no token moves.

```sh
m zkworld.subnet/build
m zkworld.subnet/test      # 12 tests
```
