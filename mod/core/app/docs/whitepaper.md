```
+-----------------------------------------------------------+
|                                                           |
|   M O D   P R O T O C O L                                |
|                                                           |
|   TECHNICAL WHITEPAPER                                    |
|   VERSION: 2.1                                            |
|   APRIL 2026                                              |
|                                                           |
+-----------------------------------------------------------+
```

---

## 0. ABSTRACT

MOD Protocol is a decentralized module registry and execution
system with 204 composable modules. It enables secure, verifiable
function calls across distributed modules using IPFS storage,
cryptographic authentication, and AI-native agent workflows.

Revenue flows through on-chain smart contracts on Base. Stakers
earn proportional treasury shares via time-weighted BlocTime
tokens---no inflation, only real marketplace fees. Validators
earn subnet emissions via StakeTime consensus scoring.

---

## 1. PROBLEM

```
CURRENT STATE OF SOFTWARE INFRASTRUCTURE:

  [x] centralized    --> single points of failure
  [x] unverifiable   --> can't confirm what code runs
  [x] non-composable --> services don't interop
  [x] costly         --> complex payment infrastructure
  [x] ai-siloed      --> models trapped in walled gardens
  [x] no dev revenue --> open source creators earn nothing
  [x] no consensus   --> no decentralized validation layer
```

## 2. SOLUTION

```
MOD PROTOCOL PROVIDES:

  [+] decentralized storage   --> ipfs content addressing
  [+] crypto verification     --> every tx signed & verified
  [+] modular architecture    --> 204 composable modules
  [+] built-in economics      --> token-gated execution
  [+] ai-native agents        --> autonomous module composition
  [+] dev monetization        --> earn per function call
  [+] validator consensus     --> staketime yuma scoring + emissions
```

---

## 3. ARCHITECTURE

```
+------------------+
| FRONTEND         |  next.js 14 + typescript + ethers.js v6
| (app)            |  wallet connect, module browser
+--------+---------+
         |
+--------v---------+
| SERVICE LAYER    |  fastapi async endpoints
| (api)            |  module registry, IPFS bridge
+--------+---------+
         |
+--------v---------+
| MODULE LAYER     |  204 python orbit modules
| (orbit)          |  ai agents, defi, storage, consensus, tools
+--------+---------+
         |
+--------v---------+
| CHAIN LAYER      |  bloctime + staketime protocol (solidity 0.8.20)
| (contracts)      |  base sepolia / base mainnet
+--------+---------+
         |
+--------v---------+
| CORE LAYER       |  mod.py framework engine
| (engine)         |  crypto, storage, routing, CLI
+------------------+
```

### DATA FLOW

```
developer --> drop module in orbit/ --> auto-discovered
          --> register on-chain (Registry.sol)
          --> set price in config.json

user --> discover module via app/api
     --> deposit stablecoins (Market.credit)
     --> invoke function (Debit.executeDebit)
     --> 5% fee to treasury, 95% to provider

staker --> stake NativeToken into BlocTime
       --> earn time-weighted BlocTime tokens
       --> claim proportional treasury revenue

validator --> register in StakeTime (multi-key identity)
          --> receive delegated stakes
          --> earn emission rewards via yuma consensus scoring
```

---

## 4. TOKEN ECONOMICS

### 4.1 TOKEN STRUCTURE

```
+------------------+-----------------------------------+
| TOKEN            | PURPOSE                           |
+------------------+-----------------------------------+
| NativeToken      | stakeable ERC20, fixed supply     |
| BlocTime         | time-weighted receipt token        |
| Market Token     | USD-pegged marketplace credit      |
| Subnet Token     | validator reward token (per-subnet)|
| USDC / USDT      | payment tokens                    |
+------------------+-----------------------------------+
```

### 4.2 COST MODEL

```
+------------------+-----------------------------------+
| FEE TYPE         | DESCRIPTION                       |
+------------------+-----------------------------------+
| market credit    | 1% to treasury on deposit          |
| market withdraw  | 0.1% retained in market            |
| debit execution  | 5% to treasury per invocation      |
| staker claim     | proportional to BlocTime balance   |
| validator comm.  | per-validator bps on emissions     |
+------------------+-----------------------------------+
```

### 4.3 REVENUE SPLIT

```
  MODULE INVOCATION FEE
  |
  |-- 95% --> module provider
  '-- 5%  --> protocol treasury
              |
              |-- distributed to BlocTime stakers
              |   (proportional to time-weighted stake)
              |
              '-- subnet emissions to validators
                  (proportional to yuma consensus score)
```

### 4.4 STAKING MULTIPLIER

```
  lock longer = earn more

  0 blocks      --> 1.0x multiplier
  10,000 blocks --> 1.5x multiplier
  50,000 blocks --> 2.0x multiplier
  100,000 blocks -> 3.0x multiplier

  BlocTime minted = amount * multiplier
  treasury share  = your BlocTime / total BlocTime
```

---

## 5. MODULE SYSTEM

### 5.1 204 ORBIT MODULES

```
+------------------+-----------------------------------+
| CATEGORY         | EXAMPLES                          |
+------------------+-----------------------------------+
| ai & agents      | agent, claude, claudecode, arena, |
|                  | skill, ag0, model                  |
| defi & trading   | uniswap, hyperliquid, goldfi,     |
|                  | prefi, raydium, copyquant, defi    |
| blockchain       | bridge, safe, eth, near, solana,  |
|                  | base, cardano, zcash               |
| storage          | ipfs, filecoin, arweave, cache    |
| dev tools        | git, gitagent, pytest, docker,    |
|                  | codex, claudegit                   |
| consensus        | staketime, bloctime, quests        |
| applications     | bloctime app, embedcode, openhouse |
| infrastructure   | web, mcp, modal, compute, caddy,  |
|                  | proton, ssh, config                |
+------------------+-----------------------------------+
```

### 5.2 MODULE LIFECYCLE

```
  1. DROP    --> put module in mod/orbit/mymod/
  2. DISCOVER --> framework auto-finds anchor file
  3. LOAD    --> m.mod('mymod')() -- lazy, O(1)
  4. SERVE   --> m serve mymod -- auto-generates API
  5. REGISTER --> on-chain via Registry.sol
  6. MONETIZE --> users pay per invocation
```

### 5.3 CREATING A MODULE

```python
# mod/orbit/mymod/mymod/mod.py

class Mod:
    description = "What this module does"

    def forward(self, **kwargs):
        return {"status": "ok"}

# that's it. now run:
#   m mymod/forward
#   m serve mymod
#   m info mymod
```

### 5.4 FULL-STACK APPLICATIONS

```
  bloctime app  --> next.js + fastapi staking dashboard
                    ports 8851 (api) / 8852 (frontend)
                    stake, view positions, claim rewards

  embedcode     --> code embedding & execution platform
                    next.js + tailwind + fastapi backend
                    code processing and deployment
```

---

## 6. STAKETIME CONSENSUS

```
VALIDATOR CONSENSUS LAYER:

  register validator (ECDSA + Ed25519 + Sr25519 keys)
     |
     v
  receive delegated stakes (with lock multiplier)
     |
     v
  yuma consensus scoring (exponential decay)
     |
     v
  per-epoch emission distribution
     |
     v
  commission split to delegators
```

```
CONSENSUS STRATEGIES:

  [+] ConsensusLinear  --> equal weight for all validators
  [+] ConsensusStaked  --> weighted by delegated stake
  [+] ConsensusYuma    --> yuma-inspired exponential decay

SLASHING:

  [+] misbehavior penalized with stake reduction
  [+] auto-deactivation after max slash count
  [+] validator score reset on slash
```

```
SUBNET ARCHITECTURE:

  each subnet has:
    - own emission token
    - own consensus parameters
    - own validator set
    - pluggable consensus strategy
```

---

## 7. AI-NATIVE ARCHITECTURE

```
AGENT EXECUTION LOOP:

  1. init memory (goal, tools, context)
  2. send context to LLM (claude / openrouter)
  3. parse structured plan
  4. execute tool calls
  5. append results to context
  6. repeat until done
```

```
WHAT AGENTS CAN DO:

  [+] discover and compose modules autonomously
  [+] persistent memory across sessions
  [+] vector embeddings for semantic retrieval
  [+] background job execution (rust server)
  [+] build, deploy, and monetize new modules
```

---

## 8. SMART CONTRACTS (BASE)

### 8.1 DEPLOYED (BASE SEPOLIA)

```
+------------------+--------------------------------------------+
| CONTRACT         | ADDRESS                                    |
+------------------+--------------------------------------------+
| NativeToken      | 0xB9b6...b51                               |
| BlocTime         | 0xF25A...bD                                |
| Treasury         | 0xe9a9...85                                |
| Market           | 0x2F0B...87                                |
| Registry         | 0x4f9e...43                                |
| Debit            | 0x6F94...42                                |
| TokenGate        | 0x97c7...8b                                |
| Oracle           | 0x40C3...F1                                |
+------------------+--------------------------------------------+
```

### 8.2 SECURITY

```
[x] reentrancy guard    --> all state-changing functions
[x] safe erc20          --> openzeppelin SafeERC20
[x] checked arithmetic  --> solidity 0.8.20 built-in
[x] access control      --> ownable + modifiers
[x] emergency pause     --> pausable on market
[x] anti-flash-loan     --> BlocTime requires lock period
[x] eip-712 signatures  --> structured data signing
[x] validator slashing  --> misbehavior penalized
```

### 8.3 IRREVERSIBLE DECENTRALIZATION

```
setOwnerless() --> one-way function
  - permanently renounces all admin control
  - cannot be reversed
  - protocol runs autonomously forever

  Phase 1: managed (deployer EOA)
  Phase 2: multisig (gnosis safe)
  Phase 3: dao (BlocTime-weighted voting)
  Phase 4: ownerless (permanent autonomy)
```

---

## 9. SECURITY MODEL

```
CRYPTOGRAPHIC:
  [x] ecdsa / sr25519 / ed25519 key generation
  [x] aes-256-gcm encryption at rest
  [x] eip-712 structured signatures
  [x] client-side key generation (keys never leave device)
  [x] gnosis safe multisig (v+4 for eth_sign)

INFRASTRUCTURE:
  [x] https enforced (tls 1.3)
  [x] rate limiting (redis-backed)
  [x] token gating (erc-20/erc-721)
  [x] input sanitization
  [x] environment secrets isolated from vcs
```

---

## 10. ROADMAP

```
PHASE 1 - FOUNDATION [DONE]
  [x] core framework & 204 modules
  [x] bloctime protocol (base sepolia)
  [x] staketime consensus (validators + subnets)
  [x] next.js frontend + wallet integration
  [x] cli tooling (m command)
  [x] ai agent framework
  [x] full-stack apps (bloctime, embedcode)

PHASE 2 - GROWTH [IN PROGRESS]
  [ ] base mainnet deployment
  [ ] module marketplace UI
  [ ] developer revenue dashboard
  [ ] cross-chain registry sync
  [ ] mcp & x402 integrations

PHASE 3 - GOVERNANCE
  [ ] dao governance (BlocTime-weighted voting)
  [ ] on-chain proposals & execution
  [ ] module versioning (semver)
  [ ] privacy features (module encryption)

PHASE 4 - SCALE
  [ ] enterprise features & SLAs
  [ ] global CDN for module execution
  [ ] institutional partnerships
  [ ] setOwnerless() on all contracts
```

---

## 11. CONCLUSION

MOD Protocol combines composable modules, on-chain economics,
AI-native agents, and validator consensus into a unified
development framework.

```
KEY INNOVATIONS:
  1. time-weighted staking    (BlocTime multiplier curve)
  2. validator consensus      (StakeTime yuma scoring + slashing)
  3. non-inflationary rewards (real marketplace fees only)
  4. 204 composable modules   (zero-config, auto-discovered)
  5. ai-native agents         (autonomous module composition)
  6. full-stack apps          (bloctime dashboard, embedcode)
  7. irreversible decentral.  (setOwnerless() one-way lock)
  8. eip-712 debit protocol   (multi-auth, daily limits)
```

```
+-----------------------------------------------------------+
|                                                           |
|   code is capital.                                        |
|   build modules. register on-chain. get paid.             |
|                                                           |
|   "simplicity is the ultimate sophistication"             |
|                                        - da vinci         |
|                                                           |
+-----------------------------------------------------------+
```

---

*MOD Protocol v2.1 --- April 2026*
*Open-source. On-chain. Unstoppable.*
