# Yuma by BlocTime: a subnet where time is the stake

**bloctime.yuma · v0.1 · October 2026**

## In one minute

A subnet is a small market for work. **Miners** do the work: they serve a model, run a GPU or answer queries. **Validators** check that work and give each miner a score. Every so often an **epoch** runs and a fixed amount of new tokens is split between them.

The hard question is whose scores count. Bittensor answers it with money: the more TAO staked behind you, the louder your vote. Here the answer is **BlocTime**. A validator's vote is the BLOC it holds plus the BLOC delegated to it, and BLOC is dollars multiplied by seconds locked. So $100 locked for a month outvotes $5,000 locked for an hour. Patience counts for more than size.

The rest is Yuma consensus, unchanged in spirit. Scores far from what most of the BlocTime agrees on are cut back. Miners are paid by the score that survives. Validators are paid for having backed those miners, and they earn more the earlier they backed them.

## Abstract

We describe **YumaBlocTime**, a Yuma-style consensus mechanism in which validator stake is read from a BlocTime contract (`getVotingPower`) rather than from a balance of a liquid token. Validators submit weight vectors over miners. Each epoch computes a stake-weighted kappa-quantile per miner, clips every weight to it, pays miners by clipped rank and pays validators through exponentially smoothed bonds. Emission is split three ways: an owner cut, a miner pool and a validator pool. The mechanism is implemented twice, as a Solidity contract (`contracts/yuma/YumaBlocTime.sol`) and as a pure-integer Python twin (`yuma/yuma.py`). A shared fixture file pins the two to identical outputs, to the wei. A paper ledger lets anyone run a subnet with no chain and no money, and the same ledger can switch to reading live BLOC from chain.

## 1. Why subnets need a better stake

Proof-of-stake subnets have a quiet flaw. The validator with the most liquid capital sets the scores, and capital can arrive the day before an epoch and leave the day after. Rented stake votes as loudly as committed stake. That gives the network's judgement to whoever can borrow the most for the shortest time.

BlocTime already measures something better. You lock dollars for a length of time and receive BLOC equal to dollars × seconds. Nothing can be borrowed into BLOC at the last minute, because the time cannot be compressed: getting a month of BlocTime takes a month of lock. When BLOC becomes a subnet's stake, the people judging the work are the people who committed to the network longest.

## 2. Roles

- **Subnet owner.** Deploys the contract and sets its parameters: tempo, emission, kappa, bond rate, cuts and set sizes. Takes `ownerCut` of every epoch plus all rounding dust.
- **Validator.** Any address with at least `minValidatorBloc` of BlocTime voting power. It writes one weight per miner. Its stake is read fresh at each epoch.
- **Miner.** Registration is free. A miner does off-chain work, gets scored by validators and earns by incentive.
- **Delegator.** Any BLOC holder. Delegating BLOC to a validator (`BlocTime.delegate`) adds to that validator's voice without moving the BLOC. Delegators keep their own BLOC rewards in the parent protocol.

## 3. Stake is BlocTime

For validator `i`, stake is

```
S_i = BlocTime.getVotingPower(validator_i)      // BLOC held + BLOC delegated to i
```

A validator with zero stake, or below `minValidatorBloc`, or with an all-zero weight row is counted as `S_i = 0` for that epoch. It cannot move consensus and earns no dividends.

Stake is read **when the epoch runs**, not when weights are set. If BLOC is moved from one validator to another just before an epoch, the vote moves with it. The total can be relocated but never counted twice, because BLOC is a single balance on one contract.

The worked case in the fixtures, *long lock beats big wallet*:

- Validator A: $100 locked for 30 days → 100 × 2,592,000 = **259,200,000 BLOC**
- Validator B: $5,000 locked for 1 hour → 5,000 × 3,600 = **18,000,000 BLOC**

A holds 93.5% of the stake with one fiftieth of the money. When the two disagree, A's scores are the consensus.

## 4. The epoch

Each epoch is permissionless: anyone can call `epoch()` once `tempo` seconds have passed since the last one. All maths is integer, scaled by `ONE = 1e18`, and every division floors in the same order in both implementations.

### 4.1 Normalise

```
s_i  = S_i / Σ S                     share of BlocTime
W_ij = w_ij / Σ_j w_ij               each validator's row sums to ONE
```

### 4.2 Consensus: the stake-weighted quantile

For each miner `j`, sort validators by the weight they gave it, highest first. Walk down the list adding stake. The consensus weight `C_j` is the weight at which the running stake first reaches `kappa` (default one half):

```
C_j = max c  such that  Σ_{i : W_ij ≥ c} s_i ≥ kappa
```

Put simply, `C_j` is the highest score that at least half the BlocTime is willing to give miner `j`. Ties keep the lower validator uid first, so the sort is deterministic on chain.

### 4.3 Clip, rank, incentive

```
Wc_ij = min(W_ij, C_j)               no one can push a miner above consensus
R_j   = Σ_i s_i · Wc_ij               clipped rank
I_j   = R_j / Σ R                     miner incentive
```

Clipping is what makes collusion expensive. A minority validator that scores its own miner at 100% sees that weight cut to whatever the majority gave the miner, which is usually zero.

### 4.4 Bonds

Each validator holds a bond in each miner. The bond is its share of that miner's clipped rank, smoothed over time:

```
ΔB_ij = s_i · Wc_ij / Σ_k s_k · Wc_kj
B_ij  = alpha · ΔB_ij + (1 − alpha) · B_ij       alpha = bondAlpha, default 0.1
```

A validator that backed a miner before the rest of the network did has already built up bond in it by the time the miner's incentive grows. Bonds are what pay for being early and being right.

### 4.5 Dividends

```
D_i = Σ_j B_ij · I_j ,   normalised so Σ D = ONE
```

### 4.6 Emission split

```
owner      = emission · ownerCutBps / 10,000
minerPool  = (emission − owner) · minerShareBps / 10,000
valPool    = emission − owner − minerPool
miner_j    = minerPool · I_j
validator_i= valPool · D_i
owner     += every unit of rounding dust
```

The defaults are a 10% owner cut and a 50/50 split of the remainder, so each epoch pays 10% to the owner, 45% to miners and 45% to validators. If nobody earned anything, because no weights were set or all stake was zero, nothing is emitted that epoch. Payouts accrue to `pending` and each address pulls its own with `claim()`.

## 5. A worked epoch

The fixture *honest majority, one outlier*: three validators holding 3,000, 1,000 and 500 BLOC score four miners. The first two agree on `10 / 30 / 60 / 0`. The third puts everything on miner 3.

- Stake shares: 66.7%, 22.2%, 11.1%.
- Consensus `C`: 0.1, 0.3, 0.6, 0. Miner 3 gets 0, because only 11% of the BlocTime backs it.
- Incentive `I`: 10%, 30%, 60%, 0%. The outlier's weight on miner 3 is clipped to zero.
- With `alpha = 1` (no bond memory), dividends `D` are 75%, 25%, 0%. The outlier earns nothing.
- From an emission of 100: miners get 4.5 / 13.5 / 27 / 0, validators get 33.75 / 11.25 / 0, and the owner gets 10.

The outlier lost both ways. Its miner was paid nothing, and its own dividend went to zero.

## 6. Registration and replacement

The validator and miner sets are capped (`maxValidators`, up to 64; `maxMiners`, up to 256), so a full subnet has to choose who leaves.

- **Validators.** When the set is full, a newcomer replaces the validator with the **least BlocTime**, and only if the newcomer has strictly more. The seat's bonds, weights and last dividend are wiped.
- **Miners.** When the set is full, a newcomer takes the uid of the miner with the **lowest last incentive** among those that have been through at least one epoch. New miners are immune until they have been scored once. That miner uid's bonds are zeroed in every validator row.
- **Weight masking.** A global sequence number is bumped on every miner registration and every weight write. When an epoch runs, any weight written *before* the current miner took its uid is read as zero, so a recycled uid never inherits the last miner's score.

## 7. Reward token

The contract runs in one of two modes, fixed at deploy time:

- **Mint mode** (the default). The reward token names the Yuma contract as its only minter, and each epoch mints exactly the emission. `scripts/yuma/deploy.js` deploys a StakeTime `Mod` token, "BlocTime Yuma" (YUMA), for this.
- **Funded mode.** Rewards come out of an ERC-20 balance that anyone tops up with `fund()`. An epoch pays out at most what is available, so the subnet can never promise more than it holds.

## 8. Three ways to run the same subnet

The same rules exist in three places, and all three produce the same numbers.

- **Paper.** `yuma/yuma.py` `Ledger`, a JSON file at `~/.mod/bloctime/yuma/ledger.json`. Stakes are paper BLOC set by hand, or, with `source mode=live`, read from the parent BlocTime contract's `getVotingPower` (cached for 30 seconds). No gas and no keys are needed. This is what the console's YUMA tab drives.
- **Chain.** `contracts/yuma/YumaBlocTime.sol`, compiled in bloctime's hardhat project. Deploy with `m bloctime.yuma/deploy network=base_sepolia`.
- **Simulate.** `m bloctime.yuma/simulate stakes=3000,500 weights='1,3;0,1'` is stateless: give it stakes and weights and get back consensus, incentive, dividends and pay.

`yuma/fixtures/cases.json` (regenerate with `python3 yuma/fixtures/gen.py`) lists multi-epoch cases with exact expected integers. `yuma/tests/test_yuma.py` checks Python against them, and `test/yuma/YumaBlocTime.test.js` replays them on the EVM. Both must match to the last wei, so a change to either implementation that drifts by one unit fails the build. `m bloctime.yuma/test` runs both.

## 9. Relation to StakeTime and Bittensor

**Bittensor.** The maths is Yuma consensus as Bittensor runs it: kappa-quantile clipping, clipped rank, EMA bonds and dividends through bonds. The change is the stake. Bittensor reads liquid TAO (or alpha) stake. This reads BlocTime, which cannot be borrowed in at the last minute. The structure here is also simpler: one subnet per contract, no root network and no per-block emission. Emission is per tempo and fixed by the owner.

**StakeTime.** StakeTime is the larger subnet stack that now lives inside this module as `bloctime.staketime` (`contracts/staketime`). It has Mod tokens, pluggable consensus (Yuma by uptime, Linear, Staked, Private), six inflation curves and a 420-slot registry in which the weakest subnet is replaced when it fills. YumaBlocTime takes one piece of that stack, its Mod token as the reward, and replaces uptime-based scoring with validator-weighted Yuma whose stake is BlocTime. The two compose: a StakeTime registry can list YumaBlocTime subnets, and each one mints its own Mod.

## 10. Attacks and limits

- **Last-minute stake.** Fresh capital has almost no BlocTime, because BLOC grows with seconds locked. Buying a large vote requires buying a long lock, and a long lock cannot be undone before the epoch.
- **Stake shuffling.** BLOC can be moved between validators right before an epoch, by transfer or by re-delegation, and the vote moves with it. This relocates voice without creating any. A validator that receives stake also has to produce a weight row that agrees with consensus, or the extra stake earns it nothing.
- **Collusion below kappa.** A cartel with less than `kappa` of the BlocTime cannot lift a miner above what the rest agree on. Above `kappa` it controls consensus, as in any Yuma. That is the security assumption: more than half of the committed time is honest.
- **Weight copying.** A lazy validator can copy the majority's public weights and collect dividends. Bonds blunt this, because a copier always arrives later than the validators it copies and earns less bond per epoch. As in Bittensor, this is mitigated, not solved.
- **Owner powers.** The owner can change every parameter, including emission and cuts, through `setParams`. It cannot shrink the sets below their current size, and it cannot take anything beyond its configured cut and dust. Treat the owner as trusted, or hand ownership to a timelock or multisig.
- **Gas.** An epoch is O(V² · M) because of the per-miner sort, which is why the sets are hard-capped at 64 × 256. Large subnets should keep tempo long.
- **Not deployed yet.** As of this version the contract is tested (8 hardhat cases + 7 pytest, inside a 217-test suite) but not deployed on a public chain. The paper ledger is the live subnet.

## 11. Parameters

- `tempo`: seconds between epochs. Default 3,600.
- `emissionPerEpoch`: reward tokens per epoch. Default 100.
- `kappa`: consensus quantile. Default 0.5.
- `bondAlpha`: bond EMA rate. Default 0.1. A value of 1 means no memory.
- `ownerCutBps`: owner's cut, off the top. Default 1,000 (10%).
- `minerShareBps`: miners' share of the remainder. Default 5,000 (50%).
- `minValidatorBloc`: BlocTime needed to validate. Default 0, meaning any non-zero amount.
- `maxValidators` / `maxMiners`: set sizes. Defaults 16 / 64; hard caps 64 / 256.

## 12. Summary

A subnet is only as honest as the people whose scores count. YumaBlocTime keeps Yuma's machinery (quantile clipping, clipped rank, bonds) and changes who those people are: the holders of the most committed time, not the most capital. Because one fixture file pins the same integer maths in Solidity and Python, anyone can run a subnet on paper today and get exactly the numbers the chain would give tomorrow.
