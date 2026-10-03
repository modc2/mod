# BlocTime

Time as the unit of commitment. v0.1, September 2026.

## Abstract

BlocTime is a staking protocol that turns time into a token. You lock a native token for a length of wall-clock time you choose, and the contract mints **BLOC** in proportion to the dollars you locked multiplied by the seconds you committed: `BLOC = USD value x seconds locked`. BLOC is voting power and a claim on a weekly reward pot. Every piece of the system (contracts, API, console, registry) runs from one self-contained module that anyone can fork and redeploy on any EVM chain without asking permission.

## 1. The problem

Most staking systems reward the balance you hold right now. A whale who arrives the day before a vote counts the same as someone who committed for years. Rewards trickle out continuously, which favours bots that farm and leave. Nothing in the system measures conviction, the one thing a community actually wants to pay for.

## 2. The idea

Price commitment directly. A stake has two dimensions: how much and how long. BlocTime multiplies them.

- 100 dollars locked for one day mints 100 x 86,400 = 8,640,000 BLOC.
- 100 dollars locked for eight years mints about 2,920 times more.
- The BLOC is minted up front, at stake time. There is no per-second accrual to game: time enters through the lock you commit to, not through how long you happen to sit there.

Because BLOC is a normal ERC-20, it can be held, delegated and transferred, and it is what the weekly pot pays out against.

## 3. Mechanics

### 3.1 Stake

`stake(amount, lockSeconds)` pulls `amount` of the native token into the contract and mints

```
usdValue = amount x priceUsdMicro / 1e6
BLOC     = usdValue x lockSeconds x multiplier(lockSeconds) / 10000
```

Locks are measured in seconds against `block.timestamp`. The console can show them as blocks (`seconds = blocks x secondsPerBlock`, 2 on Base) but the contract only ever sees seconds. The default maximum lock is eight years (252,288,000 seconds); the owner can change it with `setParams`.

### 3.2 Price

`priceUsdMicro` is the dollar price of one native token, in millionths of a dollar. It defaults to 1,000,000 ($1.00) and is owner-set with `setPriceUsd`. On a treasury-backed deployment the native token is minted 1:1 against a dollar reserve, so $1.00 is the honest price.

### 3.3 Multiplier curve

An optional piecewise-linear curve of `Point{lockSeconds, multiplier}` lets a deployment boost long locks on top of the linear model. The contract enforces that multipliers never drop below 1x, never fall as locks lengthen, and never exceed the maximum lock. The default is a single flat 1x point: pure dollars x seconds.

### 3.4 Unstake

After the lock expires, `unstake(stakeId)` returns the principal and burns the BLOC that stake minted. Voting power and pot share leave with the capital.

### 3.5 Delegation

`delegate(to)` moves your BLOC's voting power to another address without moving the tokens. `getVotingPower` counts your own balance plus everything delegated to you, and never counts a delegated balance twice.

## 4. The weekly pot

Rewards do not trickle. They collect in a single pot and pay out once a week.

- **Inflation**: each epoch (one day by default) mints a reward into the pot. The reward halves every `halvingInterval` epochs and never falls below `minRewardPerEpoch`, so issuance is front-loaded and then flat.
- **Anyone can fund it**: `fundPot(amount)` adds BLOC to this week's pot. Projects and treasuries can pay stakers directly.
- **Payout**: `distributeRewards()` opens every **Friday at 12:00 EST (17:00 UTC)**. Anyone can call it. It sweeps the whole pot to BLOC holders pro-rata by balance. Rounding dust carries into next week.
- **Fairness**: eligible supply is snapshotted before the week's inflation lands, and every BLOC transfer checkpoints both sides, so a recipient never inherits rewards that were earned before they held the token.

One payout a week makes the reward schedule legible: you can see the pot, the countdown and your share before the moment it pays.

## 5. The treasury

Fresh deployments ship a `Treasury` alongside the native token. Deposit a dollar stablecoin (USDC by default) and it mints one native token per dollar; redeem burns the token and returns the dollar. The treasury owns the token's mint keys, so supply is backed by the reserve it holds.

One trust assumption is stated plainly rather than hidden: the treasury owner **can** withdraw the reserve (`ownerWithdraw`). Choose deployments whose owner you trust, or deploy your own.

## 6. Anyone can run one

BlocTime is a template, not a platform.

- **Deploy**: the console ships the ABI and bytecode, so anyone deploys NativeToken, Treasury and BlocTime from their own wallet on any EVM chain, including a custom RPC. No server key is involved.
- **Market**: deployments register in a local registry. Registration is verified on-chain (the contract must answer like a BlocTime and report its owner), and removal requires a signature from that owner.
- **Use**: switching the console onto any registered instance reads through that instance's own RPC and writes through your wallet.
- **Fork**: `m bloctime/fork name=x` copies the whole module (contracts, API, console) into a new module with its own ports and route.

## 7. Local-first by design

Every moving part runs on hardware you control.

- The API is a small FastAPI process; the console is a Next.js app; state lives in plain JSON under `~/.mod/bloctime/`.
- Contracts compile with a local Hardhat; no hosted build service is required.
- Reads go to whichever RPC you point at, including a node you run yourself.
- No analytics, no accounts, no third-party identity: your wallet is your login.

If the host that served you this page disappears, the contracts keep working and any fork of the module can serve them again.

## 8. Governance and risk

- **Owner powers**: set the price, the multiplier curve, the maximum lock and the inflation schedule; emergency-withdraw stray tokens. A deployment can renounce ownership once it is configured.
- **Price is an input, not an oracle**: a dishonest owner can misprice the native token. The treasury model (1 token = $1 reserve) is the remedy.
- **Locks are final**: there is no early exit. Choose a lock you can live with.
- **Testnet**: the official instance runs on Base Sepolia. Nothing here is investment advice.

## 9. Summary

BlocTime measures commitment in the one currency everybody spends at the same rate: time. Lock dollars for seconds, receive BLOC, vote with it, and share a pot that pays once a week. Fork it, deploy it anywhere, and run it yourself.
