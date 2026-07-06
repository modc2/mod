# StakeTime Protocol

**Programmable Inflationary Token Distribution via Time-Weighted Validator Staking**

## Abstract

StakeTime is a protocol for fair distribution of inflationary tokens through time-weighted staking on validators. Each deployment creates a **Mod token** — an ERC20 that starts at zero supply. All tokens enter existence through consensus-elected minting: no pre-mine, no initial allocation. The consensus mechanism decides who earns new tokens and at what rate.

The system separates three layers:
1. **Token** — Mod (zero-supply ERC20, consensus-minted)
2. **Staking** — StakeTime (lock Mod tokens on validators, earn STT)
3. **Incentive** — Pluggable consensus + pluggable inflation curve

Three consensus mechanisms (Yuma, Linear, Staked) can be combined with six inflation curves (Flat, Halving, LinearDecay, Sigmoid, TAO, BTC) to create 18 distinct economic configurations.

## 1. Problem

Inflationary token distribution is a fundamental problem in decentralized systems:

- **Pre-mines and ICOs** concentrate supply in founders' hands before the network has value
- **Proof of Stake** concentrates rewards to large capital holders
- **Airdrops** are gamed by sybil attacks and provide no ongoing incentive alignment
- **Mining** requires specialized hardware and wastes energy
- **Fixed vesting** distributes regardless of contribution

What's missing is a **programmable, fair distribution mechanism** where:
1. The token starts at zero — no one has an advantage
2. All supply comes from **consensus-elected minting** — you earn by participating
3. Rewards flow to **active participants** (validators) and their **supporters** (stakers)
4. The emission curve is **configurable** — deployers choose their incentive shape
5. The consensus mechanism is **pluggable** — different scoring strategies for different use cases

## 2. Solution: Mod Token

### 2.1 Zero-Supply Genesis

Every Mod token deploys with **totalSupply = 0**. No tokens exist until the consensus mechanism mints them as emissions. This means:

- No pre-mine, no founder allocation, no ICO
- All tokens are earned through participation
- The minter role is assigned to the consensus contract
- Only the consensus contract can create new tokens

```solidity
contract Mod is ERC20, Ownable {
    address public minter;

    constructor(string memory _name, string memory _symbol)
        ERC20(_name, _symbol) {}

    function setMinter(address _minter) external onlyOwner {
        minter = _minter;
    }

    function mint(address to, uint256 amount) external {
        require(msg.sender == minter, "not minter");
        _mint(to, amount);
    }
}
```

### 2.2 Consensus-Elected Minting

The minter is set to the consensus contract after deployment. This creates a clean separation:

```
Deploy Mod (supply = 0) → Deploy Staking → Deploy Consensus
    → setMinter(consensus) → Consensus mints emissions each epoch
```

No human can mint tokens. Only the consensus algorithm, executing its deterministic rules, creates new supply. The owner sets the minter once at deployment, then the consensus contract alone controls token creation.

## 3. Staking Layer: StakeTime

### 3.1 Time-Weighted Staking

Users deposit Mod tokens into the StakeTime contract, locking them on a validator for a chosen duration. In return they receive **STT** (StakeTime tokens):

```
STT = amount × M(lockBlocks) / 10000
```

Where `M(lockBlocks)` is a piecewise-linear multiplier curve defined by the deployer:

```
Point[] = [
  { blocks: 0,      multiplier: 10000 },   // 1.0x at 0 lock
  { blocks: 43200,  multiplier: 15000 },   // 1.5x at 1 day
  { blocks: 302400, multiplier: 30000 },   // 3.0x at 1 week
]
```

Longer lockups produce more STT, giving long-term stakers a proportionally larger share of emissions.

### 3.2 Unstaking

After the lock period expires, users unstake:
1. STT tokens are **burned**
2. Mod tokens are **returned** to the staker
3. The position is deleted from on-chain state

### 3.3 Multi-Key Validators

Validators can register with any key type:
- **ECDSA** (Ethereum) — self-register, self-checkin
- **ed25519** (Solana, Polkadot) — owner-relayed
- **sr25519** (Substrate) — owner-relayed

Keys are stored as plain strings and hashed with keccak256 for indexing.

## 4. Consensus Mechanisms

The consensus layer is **pluggable**. All implementations share the same abstract base:

```
┌───────────────────────────────────────────────────────┐
│                  Consensus (abstract)                  │
│  ├─ Validator checkin routing                         │
│  ├─ Block production loop                             │
│  ├─ Epoch-based emission triggers                     │
│  ├─ Reward claims (staker + validator)                │
│  └─ Commission/staker distribution helper             │
├───────────────────────────────────────────────────────┤
│  Virtual functions (implemented per consensus type):   │
│  ├─ _applyCheckin()    — how checkins update scoring  │
│  ├─ _selectProposer()  — how block proposer is chosen │
│  ├─ _distribute()      — how emissions are split      │
│  └─ _recalcTotal()     — how total score is tracked   │
└───────────────────────────────────────────────────────┘
```

### 4.1 Yuma Consensus (Default)

Blocktime-based scoring with exponential decay. Rewards consistent, active validators.

**Scoring:**
```
score = decay(oldScore) + delta
decay(score) = score × (10000 - decayBps) / 10000
delta = min(blocksSinceLastCheckin, epochLength)
```

**Properties:**
- Active validators accumulate score by checking in frequently
- Stale validators decay exponentially toward zero
- Decay rate is configurable (500 bps = 5% per checkin)
- No capital requirement for validators
- Weighted random block proposer selection (score / totalScore)

**Distribution:**
```
For each active validator V:
  validatorShare = emission × V.blocktimeScore / totalBlocktime
  commission = validatorShare × V.commissionBps / 10000
  stakerPool = validatorShare - commission
  For each staker S on V:
    S.reward += stakerPool × S.sttBalance / V.totalSTT
```

After distribution, scores decay and the cycle repeats.

### 4.2 Linear Consensus

Simple, equal-opportunity scoring. Each checkin adds +1. Scores reset to zero after each epoch.

**Scoring:** `score += 1` per checkin
**Selection:** Weighted random by checkin count
**Distribution:** Proportional to checkin count

Best for: networks where uptime matters but complexity is unwanted. A validator that checks in 10 times gets 10x the share of one that checks in once.

### 4.3 Staked Consensus

Pure capital-weighted consensus. Validator score equals the total STT staked on them. Checkins are required for liveness (must check in during the epoch to be eligible) but don't affect the score.

**Scoring:** `score = totalSTT staked on validator`
**Selection:** Weighted random by STT
**Distribution:** Proportional to STT staked

Best for: networks that want capital alignment — validators with more delegated stake earn more. Similar to traditional DPoS but with time-weighted lockup multipliers.

### 4.4 Privacy Consensus

Anonymous participation via Merkle membership proofs. Validators prove they belong to the token-holder set without revealing which holder they are.

**Flow:**
1. Register commitment: `keccak256(secret, msg.sender)` inserted into Merkle tree
2. Anonymous checkin: provide `nullifier = keccak256(secret, epoch)` + Merkle proof
3. Distribution: emissions split evenly across all epoch nullifiers
4. Claim: reveal `secret` to compute nullifiers and withdraw rewards

Nullifiers prevent double-checkin per epoch. Validators see valid membership proofs but cannot link nullifiers to addresses.

## 5. Inflation Curves

Each consensus module can reference an `IInflationCurve` to determine per-epoch emission. If no curve is set, the flat `emissionRate` is used.

### 5.1 Flat

Constant emission every epoch.

```
emission(epoch) = rate
```

Total supply grows linearly: `supply = rate × epoch`

### 5.2 Halving

Bitcoin-style step halving at fixed intervals. Emission halves every `interval` epochs, with a configurable floor.

```
emission(epoch) = max(initialRate >> (epoch / interval), floor)
```

Supply schedule:
```
Epoch 0-9:   1000 tokens/epoch
Epoch 10-19:  500 tokens/epoch
Epoch 20-29:  250 tokens/epoch
...
Epoch 70+:    10 tokens/epoch (floor)
```

### 5.3 Linear Decay

Linear decrease from `initialRate` to `floor` over `decayEpochs`, then constant at `floor`.

```
emission(epoch) = initialRate - (initialRate - floor) × epoch / decayEpochs
emission(epoch >= decayEpochs) = floor
```

At the midpoint: `emission = (initialRate + floor) / 2`

### 5.4 Sigmoid (S-Curve)

Quadratic approximation of a sigmoid. Ramps up from `floor` to `peak` over the first half, then decays symmetrically back to `floor`.

```
midpoint = totalEpochs / 2

phase 1 (epoch < midpoint):
  emission = floor + (peak - floor) × (epoch / midpoint)²

phase 2 (epoch >= midpoint):
  emission = floor + (peak - floor) × ((totalEpochs - epoch) / midpoint)²
```

This creates a bootstrap phase (low initial emissions encourage early builders) followed by peak distribution, then graceful wind-down.

### 5.5 TAO (Supply-Capped Asymptotic)

Bittensor-style inflation: emission decreases proportionally to the remaining unminted supply. Creates an asymptotic approach to the cap.

```
emission = initialRate × (supplyCap - totalMinted) / supplyCap
```

Early epochs are highly rewarding. As `totalMinted` approaches `supplyCap`, emission approaches zero. A floor parameter prevents dust amounts.

### 5.6 BTC (Hard Cap + Halving)

Bitcoin-modeled inflation with a fixed supply cap and deterministic halving. Tracks cumulative minted supply on-chain.

```
emission(epoch) = min(initialReward >> (epoch / halvingInterval), supplyCap - totalMinted)
```

Emission stops permanently once `supplyCap` is reached.

## 6. Consensus × Inflation Matrix

Any consensus mechanism can be combined with any inflation curve:

| | Flat | Halving | LinearDecay | Sigmoid | TAO | BTC |
|---|---|---|---|---|---|---|
| **Yuma** | Steady rewards for uptime | Decreasing rewards, uptime matters more over time | Gradual transition | Bootstrap + wind-down | Asymptotic scarcity | Hard-capped uptime rewards |
| **Linear** | Equal opportunity, constant pie | Shrinking pie, early participants favored | Smooth reduction | Participation peaks mid-life | Diminishing returns | Fixed-supply equal shares |
| **Staked** | Capital-weighted steady flow | DPoS with halving | Capital alignment with decay | Capital bootstrap phase | Stake-weighted scarcity | DPoS with hard cap |

## 7. Registry

The Registry is a competitive 420-slot directory for mods. It uses a bonding-curve pool to determine priority.

### 7.1 Registration

Anyone can register a mod by locking governance tokens. If all 420 slots are full, the weakest non-immune mod is replaced.

### 7.2 Bonding Curve Boost

Users deposit STT (from any registered mod's Staking contract) to boost a mod's priority score. Shares are priced on a linear bonding curve:

```
price(share) = slope × totalShares / 1e18

Buy cost for n shares at supply s:
  cost = slope × (2sn + n²) / (2 × 1e18)

Sell return for n shares at supply s:
  return = slope × (2sn - n²) / (2 × 1e18)
```

Early boosters get more shares per STT deposited. The pool creates a market price for bloctime relative to the mod token.

### 7.3 Priority Score

```
score = totalBloctime deposited + lockedGovernanceTokens
```

Weakest non-immune mod is replaced when a new registration occurs at capacity. Immunity period protects new mods from immediate replacement.

## 8. Tokenomics

### 8.1 Two-Token Model

| Token | Type | Purpose |
|-------|------|---------|
| **Mod** | ERC20 (zero-supply, consensus-minted) | Staked by users, distributed as rewards |
| **StakeTime (STT)** | ERC20 (minted/burned) | Proof of stake-duration, determines reward share |

### 8.2 Commission

Validators set their own commission rate (capped at 50%):
```
commission = validatorShare × commissionBps / 10000
stakerPool = validatorShare - commission
```

Competitive dynamics: new validators offer low commission to attract stakers, established validators charge more.

### 8.3 Slashing

Optional slashing mechanism:
- Owner-triggered per validator
- Configurable slash basis points (max 50%)
- Slashed amount sent to treasury
- Max slash count triggers automatic deactivation

## 9. Security

- **ReentrancyGuard** on all state-changing functions
- **SafeERC20** for all token transfers
- **Monotonicity validation** on multiplier curves
- **Max stakers per validator per epoch** prevents sybil flooding
- **Lock period enforcement** — cannot unstake before lock expires
- **Commission cap** (50%) prevents predatory behavior
- **Zero-supply genesis** — no pre-mine attack surface

## 10. Architecture

### On-Chain (Solidity ^0.8.20)

```
Mod.sol           — ERC20 token (zero supply, minter role)
Staking.sol       — Abstract validator framework + epoch management
StakeTime.sol     — Lock-time weighted STT minting/burning
Consensus.sol     — Abstract consensus base (checkins, blocks, claims)
ConsensusYuma.sol — Blocktime scoring + exponential decay
ConsensusLinear.sol — Simple checkin counting
ConsensusStaked.sol — Pure stake-weighted
ConsensusPriv.sol — Privacy-preserving Merkle proofs
IInflationCurve.sol — Interface for pluggable emission schedules
Inflation*.sol    — Six curve implementations
Registry.sol      — 420-slot directory + bonding curve pools
```

### Off-Chain

- **mod.py** — Python CLI for all contract interactions
- **FastAPI** — REST API backend
- **Next.js** — Web dashboard (staking, validators, leaderboards, mod creation)

## 11. Deployment

```
1. Deploy Mod("TokenName", "SYM")         → supply = 0
2. Deploy StakeTime(mod, maxLock, ...)     → staking framework
3. Deploy Consensus(mod, staking, ...)     → scoring + distribution
4. mod.setMinter(consensus)               → consensus controls minting
5. [Optional] Deploy InflationCurve(...)
6. [Optional] consensus.setInflationCurve(curve)
7. Register validators → Users stake → Consensus mints emissions
```

## 12. Summary

StakeTime separates:

- **What gets distributed** — Mod tokens (zero-supply, consensus-minted)
- **How it's earned** — staking on validators with time-weighted lockups
- **Who gets it** — proportional to consensus score × commission split
- **The scoring strategy** — pluggable consensus (Yuma, Linear, Staked, Privacy)
- **The emission shape** — pluggable inflation curves (Flat, Halving, LinearDecay, Sigmoid, TAO, BTC)

Every token starts at zero. All value is created by the consensus mechanism. The protocol gives any community the tools to deploy their own economy with the exact incentive structure they need.

---

*StakeTime Protocol — Base Sepolia*
*147 tests | 3 consensus mechanisms | 6 inflation curves | 18 configurations*
