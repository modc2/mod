# StakeTime: Modular Primitives for Programmable Consensus and Token Distribution

**Version 3.0 | April 2026**

**Abstract.** StakeTime is a protocol composed from independent on-chain primitives—Staking, Consensus, Inflation, Subnet, and Registry—each deployable and usable in isolation. The Staking primitive introduces time-weighted delegated staking: users lock tokens on validators and receive StakeTime (STT), a synthetic token minted via a piecewise-linear multiplier over lock duration, which determines their share of emissions. Consensus is an abstract scoring layer with four implementations (Yuma decay, linear, stake-weighted, privacy-preserving). Inflation is a pluggable curve interface with four implementations (halving, flat, linear decay, sigmoid). The Registry is a competitive 420-slot directory that replaces the weakest subnet when full. Each primitive operates independently—Staking can function without Consensus, Consensus without a Registry—but they compose into a full validator network when deployed together as StakeTime. All contracts are on Base (EVM L2) with multi-key validator identity supporting ECDSA, Ed25519, and Sr25519 for cross-chain interoperability.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Primitive Architecture](#2-primitive-architecture)
3. [Staking Primitive](#3-staking-primitive)
4. [Consensus Primitive](#4-consensus-primitive)
5. [Inflation Primitive](#5-inflation-primitive)
6. [Subnet Primitive](#6-subnet-primitive)
7. [Registry Primitive](#7-registry-primitive)
8. [StakeTime: Composed Protocol](#8-staketime-composed-protocol)
9. [Economic Model](#9-economic-model)
10. [Privacy-Preserving Consensus](#10-privacy-preserving-consensus)
11. [Security Model](#11-security-model)
12. [Governance & Decentralization](#12-governance--decentralization)
13. [Deployment](#13-deployment)
14. [Conclusion](#14-conclusion)

---

## 1. Introduction

### 1.1 Problem Statement

Proof-of-Stake networks face a trilemma of distribution fairness:

1. **Airdrops** reward early adopters but not sustained participation. Tokens are dumped immediately, concentrating supply among speculators.
2. **Mining** distributes tokens to hardware operators, creating a capital barrier that excludes most participants and centralizes block production.
3. **Governance staking** rewards passive holding. Validators with the most capital dominate rewards regardless of actual network contribution, producing a rich-get-richer dynamic.

None of these mechanisms simultaneously reward (a) active participation, (b) long-term commitment, and (c) genuine network contribution while remaining (d) permissionless and (e) programmable by deployers.

Existing staking protocols compound this problem by being monolithic. Staking, consensus, emission, and registration are entangled in a single codebase. Deployers cannot swap consensus logic without forking. They cannot reuse staking mechanics in a different context. Every protocol reinvents the same primitives.

### 1.2 Solution

StakeTime decomposes the staking protocol into five independent primitives:

| Primitive | Responsibility | Standalone Use Case |
|-----------|---------------|-------------------|
| **Staking** | Time-weighted delegated staking, multiplier curve, STT minting, slashing | Any system needing lock-weighted token positions (governance, vaults, LP boosting) |
| **Consensus** | Validator scoring, checkin routing, emission distribution | Any system needing scored participation (oracles, task networks, reputation) |
| **Inflation** | Epoch-based emission schedule | Any token with programmatic supply expansion |
| **Subnet** | Minter-controlled ERC-20 token | Any system needing a token with restricted minting authority |
| **Registry** | Competitive 420-slot directory with score-based replacement | Any system needing curated, capacity-limited registration |

Each primitive is a standalone contract with no hard dependencies on the others. They compose into a full validator network when deployed together, but each can be used independently in entirely different contexts.

### 1.3 Design Principles

- **Primitives over monoliths**: Each contract does one thing. Composition happens at deployment, not in code.
- **Time over capital**: Lock duration multiplies stake weight, rewarding commitment over size.
- **Competitive curation**: Fixed registry capacity creates Darwinian pressure—weak entries are replaced.
- **Multi-chain identity**: Validators register with ECDSA, Ed25519, or Sr25519 keys, enabling cross-chain validator sets.
- **Privacy as an option**: A privacy-preserving consensus module enables anonymous participation via Merkle proofs.

---

## 2. Primitive Architecture

### 2.1 Composition Model

Each primitive is an independent contract. Composition is achieved through address references set at deployment:

```
┌─────────────────────────────────────────────────────────┐
│                    REGISTRY                             │
│  Competitive 420-slot directory                         │
│  Scores subnets by: lockedStake + totalSTT              │
│  References: Staking, Subnet, Consensus per entry       │
└──────────┬──────────────────────────────────────────────┘
           │ registers triplets of:
           ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
│   SUBNET     │   │   STAKING    │   │   CONSENSUS      │
│  ERC-20 token│   │  lock + STT  │   │  score + emit    │
│  minter ctrl │   │  multiplier  │   │  checkin routing  │
│              │   │  slashing    │   │                  │
│  standalone  │   │  standalone  │   │  references:     │
│              │   │              │   │  Staking, Subnet │
└──────────────┘   └──────────────┘   │  Inflation       │
                                      └────────┬─────────┘
                                               │ uses
                                      ┌────────┴─────────┐
                                      │   INFLATION      │
                                      │  emission curve  │
                                      │  standalone      │
                                      └──────────────────┘
```

### 2.2 Dependency Graph

```
Subnet     → (none)
Inflation  → (none)
Staking    → Subnet (token to stake)
Consensus  → Staking (reads STT balances) + Subnet (mints emissions) + Inflation (emission amount)
Registry   → Staking (reads scores) + Subnet + Consensus (per-entry references)
```

Arrows point from consumer to dependency. Subnet and Inflation have zero dependencies—they are leaf primitives. Staking depends only on a token. Consensus orchestrates the others. Registry indexes them.

### 2.3 Standalone vs. Composed

| Deployment Mode | Contracts | Use Case |
|----------------|-----------|----------|
| **Staking only** | Subnet + Staking | Governance vaults, LP boost, lock-weighted voting |
| **Staking + Consensus** | Subnet + Staking + Consensus | Validator network without registry |
| **Full StakeTime** | All five primitives | Multi-subnet competitive validator ecosystem |

---

## 3. Staking Primitive

The Staking primitive is the core building block of StakeTime. It manages validators, delegated stake positions, the time-weighted multiplier curve, STT minting, and slashing. It is designed to be used independently of consensus or emission logic.

### 3.1 Validator Registration

Validators register with a string key and a key type:

```solidity
enum KeyType { Ecdsa, Ed25519, Sr25519 }

struct Validator {
    address owner;
    string  key;
    KeyType keyType;
    uint256 commissionBps;   // 0–5000 (0–50%)
    bool    active;
    uint256 slashCount;
}
```

- **ECDSA validators** can self-register and self-checkin (`msg.sender == address derived from key`).
- **Ed25519/Sr25519 validators** are relayed by the owner. Keys are stored as strings and hashed with `keccak256` for mapping lookups.

This multi-key design enables validators from Substrate (Sr25519), Solana (Ed25519), and EVM (ECDSA) chains to participate in the same staking pool.

### 3.2 Time-Weighted Multiplier Curve

The multiplier function $M: \mathbb{N} \to \mathbb{R}^+$ maps lock duration (in blocks) to a multiplier (in basis points, where 10000 = 1.0x). It is defined as a piecewise-linear interpolation over a set of control points $\{(b_i, m_i)\}_{i=0}^{n-1}$:

$$M(x) = \begin{cases}
m_0 & \text{if } x \leq b_0 \\
m_i + \frac{(m_{i+1} - m_i)(x - b_i)}{b_{i+1} - b_i} & \text{if } b_i < x \leq b_{i+1} \\
m_{n-1} & \text{if } x > b_{n-1}
\end{cases}$$

**Monotonicity is enforced on-chain**: $b_i < b_{i+1}$ and $m_i \leq m_{i+1}$ for all $i$. This prevents arbitrage between lock durations.

**Example curve:**

| Lock Duration | Blocks | Multiplier |
|---------------|--------|------------|
| None | 0 | 1.0x |
| ~1 day | 43,200 | 1.5x |
| ~1 week | 302,400 | 2.5x |
| ~1 month | 1,296,000 | 4.0x |

The curve is configurable by the owner via `setPoints(Point[])` and can be locked permanently via ownership renunciation.

### 3.3 Staking Mechanics

When a user stakes on a validator:

```
stakeOn(validatorKey, amount, lockBlocks):
    1. Transfer `amount` of Subnet token from user to contract
    2. Compute multiplier: mult = M(lockBlocks)
    3. Mint STT: stt = amount × mult / 10000
    4. Record position: {amount, lockBlocks, startBlock, stt, validatorKeyHash}
    5. Update validator's totalSTT += stt
```

**StakeTime (STT)** is a synthetic ERC-20 token representing time-weighted stake. It is:
- Minted on stake, proportional to `amount × multiplier`
- Burned on unstake
- Non-transferable in the economic sense (tied to a position)
- The unit of account for emission distribution

The Staking primitive does not itself distribute emissions—it only tracks positions and STT balances. Distribution is the Consensus primitive's responsibility, reading STT state from Staking.

### 3.4 Unstaking

```
unstakeFrom(stakeId):
    require(block.number >= position.startBlock + position.lockBlocks)
    Burn position.stt from user
    Return position.amount of Subnet token to user
    Update validator's totalSTT -= position.stt
```

Unstaking is only possible after the lock period expires. There is no early exit mechanism—the multiplier premium is compensation for illiquidity.

### 3.5 Slashing

The owner can slash validators for misbehavior:

```
slashValidator(key):
    For each stake position on this validator:
        penalty = stakeAmount × slashBps / 10000
        sttPenalty = sttBalance × slashBps / 10000
        Reduce stake and STT by penalty amounts
        Burn STT, transfer token penalty to treasury

    validator.slashCount++
    If slashCount >= maxSlashCount:
        Deactivate validator
```

Slashing affects all delegators on a slashed validator, creating incentive for delegators to choose reliable validators. Auto-deactivation after repeated slashing removes persistently bad actors.

### 3.6 Standalone Use Cases

The Staking primitive is useful beyond validator networks:

- **Governance voting**: STT balance determines voting power. Longer lock = more influence.
- **LP boost**: Liquidity providers lock LP tokens and receive boosted rewards proportional to STT.
- **Access tiers**: dApps gate features by STT balance thresholds.
- **Vesting**: Lock tokens with a multiplier curve that increases over the vesting schedule.

In all cases, the Staking primitive provides the core lock-and-weight mechanics without requiring consensus or emission infrastructure.

### 3.7 Interface

```solidity
// Staking.sol — core interface
registerValidator(string key, KeyType keyType)
registerValidatorAdmin(string key, KeyType keyType, uint256 commissionBps)
setValidatorCommission(string key, uint256 bps)
deactivateValidator(string key)

stakeOn(string validatorKey, uint256 amount, uint256 lockBlocks)
unstakeFrom(uint256 stakeId)

setPoints(Point[] points)
getMultiplier(uint256 blockCount) → uint256

slashValidator(string key)

// View functions
getValidator(string key) → Validator
getStakePosition(uint256 stakeId) → StakePosition
getUserStakes(address user) → StakePosition[]
getValidatorStakes(string key) → StakePosition[]
getValidatorTotalSTT(string key) → uint256
```

---

## 4. Consensus Primitive

The Consensus primitive provides validator scoring and emission distribution. It reads state from the Staking primitive (STT balances, validator lists) and mints tokens from the Subnet primitive. It does not manage stake positions—that is Staking's responsibility.

### 4.1 Abstract Base

All consensus modules extend `Consensus.sol`, which provides:
- Checkin routing (ECDSA self-call or owner relay)
- Block production and epoch advancement
- Emission calculation (flat rate or via Inflation primitive)
- A shared `_distributeValidatorShare()` helper for commission splits
- `getLeaderboard(limit)` for top validators by score

The base defines four virtual methods that each module implements:

```solidity
function _applyCheckin(bytes32 keyHash) internal virtual;
function _selectProposer() internal virtual returns (bytes32);
function _distribute() internal virtual;
function _recalcTotal() internal virtual;
```

### 4.2 ConsensusYuma — Blocktime with Exponential Decay

The default module. Inspired by Bittensor's Yuma consensus, it rewards consistent validator activity with exponential decay of inactive participants.

**Scoring:**

On each checkin for validator $v$:

$$\delta_v = \min(\text{block.number} - \text{lastSeen}_v, \text{epochLength})$$

$$\text{score}_v = \text{score}_v \cdot \frac{10000 - \text{decayBps}}{10000} + \delta_v$$

**Properties:**
- Active validators accumulate score proportional to time between checkins (capped at one epoch)
- Inactive validators decay exponentially at rate `decayBps` per checkin cycle
- A validator that stops checking in sees its score approach zero
- Proposer selection: weighted random by score

**Parameters:**
- `decayBps`: Decay rate per cycle (default 500 = 5%)
- `epochLength`: Blocks per epoch (default 43,200 ≈ 1 day on Base)

### 4.3 ConsensusLinear — Simple Counter

Each checkin increments score by 1. Scores reset to zero at each distribution.

$$\text{score}_v = \text{score}_v + 1 \quad \text{(on checkin)}, \qquad \text{score}_v = 0 \quad \text{(after distribution)}$$

**Properties:**
- All checkins carry equal weight
- No decay or history
- Suitable for networks where presence matters more than duration

### 4.4 ConsensusStaked — Pure Stake-Weighted

Score equals the total STT delegated to a validator. Validators must check in each epoch to be eligible, but score is read from the Staking primitive rather than accumulated.

$$\text{score}_v = \text{totalSTT}_v \quad \text{(if checked in this epoch, else 0)}$$

**Properties:**
- More delegated stake = more emissions
- Checkin is a liveness requirement, not a scoring input
- Produces pure proof-of-stake distribution

### 4.5 ConsensusPriv — Privacy-Preserving

See [Section 10](#10-privacy-preserving-consensus) for full specification.

### 4.6 Emission Distribution

Each epoch, the Consensus primitive:

1. Reads total emission from the Inflation primitive (or uses flat rate)
2. Reads validator scores from its internal state
3. Reads STT balances from the Staking primitive
4. Mints Subnet tokens via the Subnet primitive
5. Distributes to validators and delegators

$$E_v = E_{\text{total}} \cdot \frac{\text{score}_v}{\sum_j \text{score}_j}$$

Commission split per validator:

$$\text{commission}_v = E_v \cdot \frac{c_v}{10000}, \qquad \text{delegatorPool}_v = E_v - \text{commission}_v$$

Per-delegator reward:

$$\text{reward}_{u,v} = \text{delegatorPool}_v \cdot \frac{\text{STT}_{u,v}}{\text{totalSTT}_v}$$

### 4.7 Interface

```solidity
// Consensus.sol — core interface
checkin(string key)
batchCheckin(string[] keys)
produceBlock()
distribute()
claimStakerRewards()
claimValidatorRewards(string key, address to)

getLeaderboard(uint256 limit) → ValidatorScore[]
getEffectiveEmission() → uint256
```

---

## 5. Inflation Primitive

The Inflation primitive determines how many tokens are emitted per epoch. It is a pure function contract with no state dependencies—given an epoch number, it returns an emission amount.

### 5.1 Interface

```solidity
interface IInflationCurve {
    function getEmission(uint64 epoch) external view returns (uint256);
}
```

If no Inflation contract is set, the Consensus primitive falls back to a flat `emissionRate` parameter.

### 5.2 InflationHalving — Bitcoin-Style

Emission halves every `interval` epochs until reaching a `floor`:

$$E(\text{epoch}) = \max\left(\text{floor}, \frac{\text{initialRate}}{2^{\lfloor \text{epoch} / \text{interval} \rfloor}}\right)$$

**Use case:** Deflationary pressure over time. Early participants earn the most. Supply converges to a finite limit.

### 5.3 InflationFlat — Constant Rate

$$E(\text{epoch}) = \text{rate} \quad \forall \text{ epoch}$$

**Use case:** Predictable, steady emission. Simple to reason about. Inflationary in perpetuity.

### 5.4 InflationLinearDecay — Linear Ramp-Down

$$E(\text{epoch}) = \begin{cases}
\text{initialRate} - \frac{(\text{initialRate} - \text{floor}) \cdot \text{epoch}}{\text{decayEpochs}} & \text{if epoch} < \text{decayEpochs} \\
\text{floor} & \text{otherwise}
\end{cases}$$

**Use case:** Gradual reduction. Smoother than halving. Reaches floor in predictable time.

### 5.5 InflationSigmoid — S-Curve

A piecewise quadratic approximation of a sigmoid, ramping up to a peak then decaying:

$$E(\text{epoch}) = \begin{cases}
\text{floor} + (\text{peak} - \text{floor}) \cdot \left(\frac{\text{epoch}}{\text{mid}}\right)^2 & \text{if epoch} \leq \text{mid} \\
\text{floor} + (\text{peak} - \text{floor}) \cdot \left(\frac{\text{totalEpochs} - \text{epoch}}{\text{mid}}\right)^2 & \text{if epoch} > \text{mid}
\end{cases}$$

Where $\text{mid} = \text{totalEpochs} / 2$.

**Use case:** Low initial emission (bootstrapping), peak emission (growth), graceful decline (maturity).

### 5.6 Inflation Curve Comparison

```
Emission
  ▲
  │  ██
  │  ████                          Halving
  │  ██████
  │  ████████████
  │  ████████████████████████████
  │──────────────────────────────► Epoch

  ▲
  │  ████████████████████████████  Flat
  │──────────────────────────────► Epoch

  ▲
  │  ██
  │  ████                          Linear Decay
  │  ██████
  │  ████████
  │  ██████████
  │  ████████████████████████████
  │──────────────────────────────► Epoch

  ▲
  │          ████████
  │        ████████████            Sigmoid
  │      ████████████████
  │    ████████████████████
  │  ████████████████████████
  │──────────────────────────────► Epoch
```

### 5.7 Standalone Use Cases

The Inflation primitive is useful wherever programmatic token emission is needed:

- **Liquidity mining**: Emission curve determines reward schedule for LP stakers
- **Vesting contracts**: Sigmoid curve models accelerating-then-decelerating vesting
- **Game economies**: Halving curve creates scarcity pressure on in-game currency

---

## 6. Subnet Primitive

### 6.1 Design

The Subnet primitive is a standard ERC-20 token with a single additional feature: **minter control**.

```solidity
contract Subnet is ERC20, Ownable {
    address public minter;

    function setMinter(address _minter) external onlyOwner;
    function mint(address to, uint256 amount) external {
        require(msg.sender == minter, "not minter");
        _mint(to, amount);
    }
}
```

The minter is set to the Consensus contract after deployment. Only the designated minter can create new tokens, ensuring all inflation flows through authorized distribution logic.

### 6.2 Initial Supply

The deployer receives an initial supply at deployment. This serves as bootstrap liquidity for staking—validators and early delegators stake from this pool before emissions begin.

### 6.3 Standalone Use Cases

Any system needing an ERC-20 with restricted minting authority:

- **DAO treasuries**: Only the governance contract can mint
- **Bridge tokens**: Only the bridge contract can mint wrapped tokens
- **Reward tokens**: Only the rewards distributor can mint

---

## 7. Registry Primitive

### 7.1 Design

The Registry is a competitive directory capped at 420 active slots. When the registry is full and a new entry registers, the weakest non-immune entry is automatically deregistered and replaced.

```solidity
struct SubnetInfo {
    uint256 id;
    address owner;
    string  name;
    address subnet;      // Subnet ERC-20 token
    address stakeTime;   // Staking contract
    address consensus;   // Consensus module
    uint256 registeredBlock;
    bool    active;
}
```

### 7.2 Scoring

Entry strength is measured by combined economic activity:

$$\text{score}(\text{entry}) = \text{lockedStake} + \text{totalSTTSupply}$$

Where `lockedStake` is the total tokens locked in the Staking contract and `totalSTTSupply` is the total minted STT. This dual metric rewards both raw capital commitment and time-weighted commitment.

### 7.3 Immunity Period

Newly registered entries are immune from replacement for `immunityPeriod` blocks (default: 43,200 ≈ 1 day). This prevents front-running attacks where an attacker registers and immediately replaces an entry before it can attract stake.

```solidity
function isImmune(uint256 id) public view returns (bool) {
    return block.number < entries[id].registeredBlock + immunityPeriod;
}
```

### 7.4 Registration Cost

Registering requires locking `registrationCost` governance tokens (default: 1,000 tokens). Tokens are returned on voluntary deregistration but forfeited on competitive replacement.

### 7.5 Competitive Dynamics

The 420-slot cap creates emergent properties:

1. **Quality pressure**: Entries must attract real stakers or risk replacement.
2. **Capital efficiency**: Locked registration costs create opportunity cost for frivolous entries.
3. **Organic curation**: The registry self-organizes by economic activity without governance votes.
4. **Scarcity premium**: As demand exceeds 420, the minimum viable stake to survive increases.

### 7.6 Standalone Use Cases

Any system needing curated, capacity-limited registration:

- **Oracle networks**: Top N oracle providers by stake
- **Task markets**: Top N compute providers by reputation score
- **Content curation**: Top N creators by engagement-weighted stake

### 7.7 Interface

```solidity
// Registry.sol — core interface
registerSubnet(string name, address subnet, address stakeTime, address consensus)
deregisterSubnet(uint256 id)
getStakeScore(uint256 id) → uint256
getWeakestSubnet() → uint256
isImmune(uint256 id) → bool
getSubnet(uint256 id) → SubnetInfo
getAllSubnets() → SubnetInfo[]
```

---

## 8. StakeTime: Composed Protocol

### 8.1 Composition

StakeTime is the full composition of all five primitives into a multi-subnet validator ecosystem:

```
StakeTime = Subnet + Staking + Consensus + Inflation + Registry
```

When deployed together:

1. **Subnet** provides the stakeable and emittable token
2. **Staking** manages delegated positions with time-weighted STT
3. **Consensus** scores validators and distributes minted Subnet tokens using STT balances from Staking and emission amounts from Inflation
4. **Inflation** determines per-epoch emission amounts
5. **Registry** indexes multiple {Subnet, Staking, Consensus} triplets in a competitive directory

### 8.2 Deployment Flow

```python
from mod import Mod
m = Mod()
st = m.mod('staketime')()

# Deploy all primitives
st.deploy(network='base_sepolia')

# Register composed triplet in global registry
st.register_subnet(
    name='my-subnet',
    subnet_addr='0x...',
    stake_time='0x...',
    consensus_addr='0x...'
)
```

Or via CLI:

```bash
m staketime/deploy network=base_sepolia
m staketime/register_subnet name=my-subnet
```

The frontend supports LLM-assisted subnet creation: describe desired tokenomics in natural language, and the system generates deployment parameters for all primitives.

### 8.3 Cross-Primitive Interactions

| Action | Primitives Involved | Flow |
|--------|-------------------|------|
| Stake tokens | Staking ← Subnet | Transfer Subnet tokens into Staking, mint STT |
| Validator checkin | Consensus → Staking | Consensus routes checkin, may read STT from Staking |
| Epoch emission | Consensus → Inflation → Subnet → Staking | Read emission amount, mint tokens, distribute by STT weight |
| Claim rewards | Consensus → Staking | Read STT balances, transfer accumulated rewards |
| Subnet registration | Registry ← {Subnet, Staking, Consensus} | Index the triplet, read scores from Staking |
| Competitive replacement | Registry → Staking | Compare scores, replace weakest |

---

## 9. Economic Model

### 9.1 Lock Duration Economics

The multiplier curve creates a convex relationship between lock duration and earning power:

| Lock Duration | Multiplier | STT per 1000 Tokens | Relative Earning Power |
|---------------|------------|---------------------|----------------------|
| 0 blocks | 1.0x | 1,000 STT | 1.0x |
| 43,200 (~1 day) | 1.5x | 1,500 STT | 1.5x |
| 302,400 (~1 week) | 2.5x | 2,500 STT | 2.5x |
| 1,296,000 (~1 month) | 4.0x | 4,000 STT | 4.0x |

A user who locks for 1 month earns 4x the emissions of one who locks for 0 blocks, per token staked. This compensates for illiquidity risk and aligns long-term holders with network health.

### 9.2 Commission Market

Validators set their commission rate between 0% and 50% (0–5000 bps). This creates a competitive market:

- **High-score validators** can charge higher commission because delegators earn more overall.
- **New validators** attract delegators by offering low commission.
- **Commission changes** take effect at the next epoch, preventing mid-epoch manipulation.

### 9.3 Staker Strategy

Rational stakers optimize across three dimensions:

1. **Validator selection**: Higher-scoring validators earn more emissions. But popular validators have more total STT, diluting per-staker rewards. The optimal strategy balances validator performance against delegation concentration.

2. **Lock duration**: Longer locks yield higher multipliers. But locked tokens cannot be restaked on a better-performing validator. The optimal lock duration depends on confidence in validator selection.

3. **Commission tolerance**: A validator charging 10% commission but scoring 2x the average is more profitable than one charging 0% at average score. Stakers evaluate net yield, not just commission rate.

### 9.4 Validator Strategy

Rational validators optimize across:

1. **Uptime**: In Yuma consensus, longer gaps between checkins reduce score due to decay. Consistent checkins maximize score accumulation.

2. **Commission pricing**: Lower commission attracts more delegators (more total STT), which attracts more delegators (network effect). But margin per STT decreases. Validators find equilibrium between volume and margin.

3. **Reputation**: Slashing destroys delegator trust permanently. The cost of a single slash (lost delegations) far exceeds any short-term gain from misbehavior.

### 9.5 Yield Derivation

Let:
- $S_u$ = user's staked token amount
- $L_u$ = user's chosen lock duration (blocks)
- $M(L_u)$ = multiplier at lock duration $L_u$ (basis points)
- $\text{STT}_u = S_u \cdot M(L_u) / 10000$
- $\text{STT}_v$ = total STT on validator $v$
- $E_v$ = validator $v$'s emission share
- $c_v$ = validator commission (basis points)

**User's epoch reward on validator $v$:**

$$R_u = E_v \cdot \frac{10000 - c_v}{10000} \cdot \frac{\text{STT}_u}{\text{STT}_v}$$

---

## 10. Privacy-Preserving Consensus

### 10.1 Motivation

Standard consensus modules reveal validator identities on every checkin, enabling surveillance, targeted attacks, and validator coercion. ConsensusPriv enables anonymous validator participation using commitment schemes and Merkle proofs.

### 10.2 Commitment Tree

A 20-level incremental Merkle tree supports up to $2^{20}$ (≈1M) commitments. The tree stores the last 30 roots to accommodate concurrent insertions:

```solidity
contract CommitmentTree {
    uint8  constant LEVELS = 20;
    uint32 constant ROOT_HISTORY_SIZE = 30;

    mapping(uint8 => uint256) filledSubtrees;
    mapping(uint256 => uint256) roots;
    uint32 currentRootIndex;
    uint32 nextIndex;
}
```

### 10.3 Protocol Flow

**Registration:**

1. User generates a secret $s$.
2. User computes commitment $C = \text{keccak256}(s, \text{address})$.
3. User calls `registerCommitment(C)`, inserting $C$ into the Merkle tree.
4. User receives the leaf index and current root.

**Anonymous Checkin (each epoch):**

1. User computes nullifier $N = \text{keccak256}(s, \text{epoch})$—unique per user per epoch.
2. User generates Merkle proof $\pi$ proving $C$ is in the tree against known root $R$.
3. User calls `anonCheckin(N, C, \pi, R)`.
4. Contract verifies:
   - $R$ is a known root (within last 30)
   - $N$ has not been used this epoch (prevents double-checkin)
   - Merkle proof $\pi$ is valid for commitment $C$ against root $R$
5. If valid: epoch score incremented, nullifier marked as used.

**Reward Claim:**

```
claimPrivRewards(secret, epochs[]):
    For each epoch:
        Verify nullifier(secret, epoch) was used
        Transfer epoch's share of emissions to msg.sender
```

### 10.4 Privacy Guarantees

- **Unlinkability**: Checkins cannot be linked to specific registrations (Merkle proof reveals set membership, not identity).
- **Non-double-spending**: Each nullifier is unique per secret per epoch.
- **Forward privacy**: Revealing the secret for claiming does not retroactively link past checkins.
- **Set anonymity**: Privacy set size equals the number of registered commitments.

### 10.5 Limitations

The current implementation uses on-chain Merkle proofs without zero-knowledge proofs. Gas costs scale with tree depth (20 hashes per verification). A future upgrade path exists to integrate zk-SNARKs for constant-cost verification.

---

## 11. Security Model

### 11.1 Smart Contract Security

| Measure | Implementation |
|---------|---------------|
| Reentrancy protection | OpenZeppelin `ReentrancyGuard` on all state-changing functions |
| Safe token transfers | OpenZeppelin `SafeERC20` for all ERC-20 operations |
| Overflow protection | Solidity 0.8.20 built-in checked arithmetic |
| Access control | `Ownable` with per-function modifiers |
| Input validation | `require` guards on all external functions |
| Monotonicity enforcement | Multiplier curve points must be strictly increasing |
| Commission cap | Maximum 50% (5000 bps) prevents predatory validators |
| Staker limits | `maxStakersPerValidator` prevents unbounded gas costs |

### 11.2 Economic Security

1. **No flash-loan attacks on STT**: STT is minted on staking with a lock period. Cannot be acquired and returned in a single transaction.

2. **Monotonic multiplier curve**: Enforced on-chain. One long lock always produces ≥ STT than equivalent short locks.

3. **Block-based lock enforcement**: `block.number` check prevents early unstaking. No governance override.

4. **Slashing deterrence**: Validators lose both their own stake and delegator trust.

5. **Registry sybil resistance**: Registration cost makes creating dummy entries expensive. Competitive replacement removes entries that fail to attract real stake.

### 11.3 Multi-Key Security

Supporting Ed25519 and Sr25519 key types introduces a trust assumption: the owner address must relay checkins for non-ECDSA validators. This is mitigated by:

- ECDSA validators can self-register for trustless operation
- Non-ECDSA validators accept the relay trust model (suitable for cross-chain sets)
- Key hashing via `keccak256` prevents collision attacks

### 11.4 Primitive Isolation

Each primitive's security is independent:

- A vulnerability in Consensus does not affect Staking positions (tokens remain locked in Staking)
- A vulnerability in Registry does not affect token balances (Subnet tokens are unrelated to Registry state)
- A compromised Inflation curve only affects future emissions, not existing positions

This isolation is a direct consequence of the primitive architecture—unlike monolithic contracts where a single vulnerability can cascade.

---

## 12. Governance & Decentralization

### 12.1 Ownership Model

All contracts use OpenZeppelin `Ownable`. The owner can:

- Set multiplier curve points (Staking)
- Configure emission parameters (Consensus)
- Slash validators (Staking)
- Update primitive references (Consensus)
- Set registration costs and immunity periods (Registry)

### 12.2 Decentralization Path

```
Phase 1: DEPLOYER CONTROL
  Owner = deployer EOA
  Rapid iteration, parameter tuning, emergency response

Phase 2: MULTISIG
  Transfer ownership to Gnosis Safe (N-of-M)
  Timelock on parameter changes
  Community signers

Phase 3: DAO GOVERNANCE
  On-chain proposals weighted by STT holdings
  Time-locked execution
  Community-driven parameters

Phase 4: RENUNCIATION
  renounceOwnership() on all contracts
  Permanent, irreversible
  Protocol operates autonomously with fixed parameters
```

### 12.3 Upgradability

The protocol is intentionally non-upgradable (no proxy pattern). Contract parameters are configurable by the owner, but logic is immutable after deployment.

New consensus modules or inflation curves are deployed as separate contracts and adopted by new subnets. Existing subnets continue under their original logic. This is a natural consequence of the primitive model—swapping a consensus module means deploying a new Consensus contract and creating a new Registry entry, not upgrading existing state.

---

## 13. Deployment

### 13.1 Live Contracts (Base Sepolia)

| Contract | Primitive | Address |
|----------|-----------|---------|
| Subnet Token | Subnet | `0x774e448F4B212eCE4a9b2270B840f327ab1FAada` |
| StakeTime | Staking | `0x153D2009F415330B3c860B9d511bF1ba8cBa2d56` |
| ConsensusYuma | Consensus | `0x57eCc51d2a26fB95521A02b6579ECDbB591A9ec2` |
| Governance Token | Subnet | `0x21e8E4be4AF613F31EE385F50bC9aB3b16756352` |
| Registry | Registry | `0x075cA02b1AFAab3eCAC54AcFb705bAF348D59784` |

**Chain**: Base Sepolia (Chain ID 84532)
**Compiler**: Solidity 0.8.20
**Framework**: Hardhat
**Libraries**: OpenZeppelin Contracts v5.x

### 13.2 Default Parameters

| Parameter | Value | Primitive | Description |
|-----------|-------|-----------|-------------|
| `emissionRate` | 100 tokens/epoch | Consensus | Base emission per epoch |
| `decayBps` | 500 (5%) | Consensus | Yuma score decay rate |
| `epochLength` | 43,200 blocks | Consensus | ~1 day on Base |
| `immunityPeriod` | 43,200 blocks | Registry | New entry protection |
| `registrationCost` | 1,000 tokens | Registry | Governance token lock |
| `maxLockBlocks` | 100,000 blocks | Staking | Maximum lock duration |
| `maxStakersPerValidator` | 100 | Staking | Per-validator staker cap |
| `defaultCommissionBps` | 1,000 (10%) | Staking | Default validator commission |
| `maxSubnets` | 420 | Registry | Directory capacity |

---

## 14. Conclusion

StakeTime decomposes the staking protocol into five independent primitives, each useful in isolation and powerful in composition:

1. **Staking primitive.** Time-weighted delegated staking with a piecewise-linear multiplier curve. Converts lock commitment into synthetic STT tokens. Manages validators, positions, and slashing. Usable standalone for governance, vaults, LP boosting, or any system needing lock-weighted token positions.

2. **Consensus primitive.** Pluggable validator scoring with four implementations (Yuma, linear, stake-weighted, privacy-preserving). Reads STT state from Staking, emission amounts from Inflation, and mints tokens via Subnet. Usable standalone for any scored participation system.

3. **Inflation primitive.** Pure function contracts returning emission amounts per epoch. Four curves (halving, flat, linear decay, sigmoid). Usable standalone for any programmatic token supply schedule.

4. **Subnet primitive.** Minter-controlled ERC-20. Usable standalone wherever restricted minting authority is needed.

5. **Registry primitive.** Competitive 420-slot directory with score-based replacement. Usable standalone for any capacity-limited, quality-curated registration system.

Composed together, these primitives form a multi-subnet validator ecosystem where deployers have full control over tokenomics, validators compete on uptime and performance, delegators are rewarded for genuine commitment, and weak subnets are organically replaced by stronger ones.

The protocol is deployed on Base Sepolia with all primitives operational, and is architecturally ready for mainnet deployment.

---

*StakeTime is part of the MOD Protocol. All smart contracts are verified and auditable on-chain.*

*Version 3.0 — April 2026*
