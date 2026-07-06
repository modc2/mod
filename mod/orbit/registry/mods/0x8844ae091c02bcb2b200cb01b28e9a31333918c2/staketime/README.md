# StakeTime

Programmable inflationary token distribution via time-weighted validator staking on EVM (Base Sepolia).

## Core Concepts

- **Mod** — ERC20 token deployed with zero supply. All tokens are minted by the consensus mechanism.
- **StakeTime (STT)** — Users stake Mod tokens on validators, receive STT proportional to amount x lock duration.
- **Consensus** — Pluggable module that mints new Mod tokens as emissions each epoch. Three implementations: Yuma, Linear, Staked.
- **Inflation Curves** — Six pluggable emission schedules: Flat, Halving, LinearDecay, Sigmoid, TAO, BTC.
- **Registry** — Competitive 420-slot directory with bonding-curve boost pools.

## Structure

```
staketime/
├── src/
│   ├── contracts/
│   │   ├── Mod.sol                      # ERC20 token (zero supply, consensus-minted)
│   │   ├── Registry.sol                 # Subnet registry + bonding curve pools
│   │   ├── staking/
│   │   │   ├── Staking.sol              # Abstract validator framework
│   │   │   └── StakeTime.sol            # STT: lock-time weighted staking
│   │   ├── consensus/
│   │   │   ├── Consensus.sol            # Abstract consensus base
│   │   │   ├── yuma/ConsensusYuma.sol   # Blocktime scoring + exp decay
│   │   │   ├── linear/ConsensusLinear.sol # Simple checkin counting
│   │   │   ├── staked/ConsensusStaked.sol # Pure stake-weighted
│   │   │   └── priv/ConsensusPriv.sol   # Privacy-preserving (Merkle proofs)
│   │   └── inflation/
│   │       ├── IInflationCurve.sol      # Interface
│   │       ├── InflationFlat.sol        # Constant emission
│   │       ├── InflationHalving.sol     # Bitcoin-style halving
│   │       ├── InflationLinearDecay.sol # Linear decrease to floor
│   │       ├── InflationSigmoid.sol     # S-curve ramp-up/decay
│   │       ├── InflationTAO.sol         # Supply-cap asymptotic
│   │       └── InflationBTC.sol         # Hard cap + halving
│   ├── mod.py                           # Python CLI module
│   ├── api/api.py                       # FastAPI backend
│   └── app/                             # Next.js dashboard
├── scripts/
│   ├── deploy.js                        # Full stack deploy
│   └── deploy_subnet.js                 # Single mod deploy + register
├── test/
│   ├── Mod.test.js                      # Token tests
│   ├── StakeTime.test.js                # Staking tests
│   ├── ConsensusYuma.test.js            # Yuma consensus tests
│   ├── ConsensusLinear.test.js          # Linear consensus tests
│   ├── ConsensusStaked.test.js          # Staked consensus tests
│   ├── Registry.test.js                 # Registry + bonding curve tests
│   ├── Inflation.test.js               # All 6 inflation curve simulations
│   └── ConsensusInflation.test.js       # Consensus × inflation integration
└── hardhat.config.js
```

## Consensus Mechanisms

| Type | Scoring | Selection | Best For |
|------|---------|-----------|----------|
| **Yuma** | Blocktime + exp decay | Weighted random by score | Networks needing consistent uptime |
| **Linear** | +1 per checkin, reset each epoch | Weighted random by count | Simple, equal-opportunity networks |
| **Staked** | STT staked on validator | Weighted random by stake | Capital-aligned networks |
| **Privacy** | Anonymous Merkle-proof checkins | Random by nullifier | Privacy-preserving participation |

## Inflation Curves

| Curve | Shape | Parameters | Use Case |
|-------|-------|------------|----------|
| **Flat** | Constant | `rate` | Predictable, steady emission |
| **Halving** | Step decrease | `initialRate, interval, floor` | Bitcoin-like scarcity schedule |
| **LinearDecay** | Linear ramp-down | `initialRate, floor, decayEpochs` | Gradual transition to steady-state |
| **Sigmoid** | S-curve up then down | `peak, floor, totalEpochs` | Bootstrap phase then wind-down |
| **TAO** | Asymptotic to cap | `initialRate, supplyCap, floor` | Bittensor-style diminishing returns |
| **BTC** | Halving + hard cap | `initialReward, halvingInterval, supplyCap` | Fixed-supply with halving schedule |

## Usage

```bash
m staketime status
m staketime deploy
m staketime serve
m staketime register key=val1 key_type=1 commission_bps=1000
m staketime stake_on validator_key=val1 amount=1000 lock_blocks=43200
m staketime checkin key=val1
m staketime produce_block
m staketime claim_staker_rewards
```

## Tests

```bash
npx hardhat test                              # all 147 tests
npx hardhat test test/Inflation.test.js       # inflation curve simulations
npx hardhat test test/ConsensusInflation.test.js  # consensus × inflation integration
```
