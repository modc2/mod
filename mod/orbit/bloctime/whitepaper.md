# BlocTime

Time as the unit of commitment. v0.1, September 2026.

## In one minute

- **What it is**: a staking contract where you lock tokens for a length of time you pick, and get a token called **BLOC** for doing it.
- **How much BLOC**: dollars locked times seconds locked. $1 for one day is 86,400 BLOC. Bigger and longer locks earn more.
- **What BLOC does**: it is your voting power, and it decides your share of a reward pot that pays out once a week, every Friday at 17:00 UTC.
- **The catch**: locks are final until they expire. The official instance runs on a testnet, so the tokens have no real value yet.
- **Who runs it**: anyone. The contracts, the API and the console are one open module you can deploy or fork on any EVM chain.

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

## 8. Private staking (ShadowBloc)

On the main contract BLOC is minted to your wallet address, so anyone can read who staked, how much, for how long and what slice of the pot they take. **ShadowBloc** is the same system written in Cairo for Starknet, with the owner removed from the record.

- **Notes, not balances.** A stake is a note owned by a one-time Stark key that your browser makes. The contract stores the public half of that key and never stores a wallet address. No event names an account either.
- **Signed actions, relayed.** Claiming, unstaking and handing a note to someone else are signatures from the note's key. Starknet checks them natively with ECDSA, and any account can post them. A relayer posts them, so your own account never touches the contract. Each signature covers the payout address and a per-note nonce, so a relayer can't redirect your funds and nobody can replay a signature.
- **Fixed menus.** Stakes come in four sizes (10 / 100 / 1,000 / 10,000) and six locks (1 day to 8 years). Your note looks the same as every other note in its bucket, and the console shows how many notes share it.
- **Same economics.** BLOC = dollars × seconds, and the whole pot is paid out once a week after Friday 17:00 UTC, pro-rata by BLOC. Payouts are pulled per note, to whatever address the key signs for.

**What stays public, on purpose:** totals, the pot, each note's bucket and BLOC, and every token transfer, including the address a payout lands on. You can follow a note from stake to payout. ShadowBloc hides *who owns* a position, not *what moved*: it is not a mixer. The relayer sees your IP address and a note's public key, but never its secret. Your keys live in your browser and in the backup you download. Lose both and you lose the note.

The contract is `cairo/src/shadowbloc.cairo`, covered by a snforge suite in `cairo/tests/`. It currently runs on a local Starknet devnet with paper NAT.

## 9. Yuma: rewards by BlocTime

BLOC already measures commitment, so it can also be used to pay people for work. **bloctime.yuma** is Bittensor-style Yuma consensus, except that a validator's stake is its BlocTime: the BLOC it holds plus the BLOC delegated to it.

- **Validators score miners.** Each validator sets a weight for every miner. Its vote counts in proportion to its BlocTime. Because BLOC is dollars × seconds, $10 locked for a month (25.9M BLOC) outvotes $1,000 locked for a minute (60K BLOC).
- **Consensus clips outliers.** For each miner, the protocol finds the highest score that validators holding at least half of the BlocTime (kappa) agree on. Any score above that is cut down to it. A small validator cannot push its friend's miner up by voting alone.
- **Miners earn by clipped rank.** A miner's incentive is the BlocTime-weighted sum of the clipped scores it received, as a share of all miners.
- **Validators earn by bonds.** Every epoch, each validator gains a bond in the miners it scored, sized by its share of that miner's clipped score. Bonds are an exponential moving average, so validators who backed good miners early and consistently keep earning. A validator's dividend is its bonds weighted by each miner's incentive.
- **The split.** Each epoch's emission pays the subnet owner a fixed cut (10% by default). Half of what remains goes to miners and half to validators. Rounding dust goes to the owner, so every token is accounted for.

The rules run in two places with the same integer maths: `contracts/yuma/YumaBlocTime.sol`, which reads `getVotingPower` from any BlocTime and in mint mode mints a staketime `Mod` token, and `yuma/yuma.py`, a paper subnet that can read live BLOC from chain. A shared fixture file (`yuma/fixtures/cases.json`) pins both implementations to identical outputs, down to the wei. Stake is read when the epoch runs, so BLOC moved between validators just before an epoch moves its vote too. It can be moved, but never counted twice.

The full mechanism, with a worked epoch and its attack model, is in the Yuma whitepaper (`yuma/whitepaper.md`, also under **Read the whitepaper** on the YUMA tab).

This is the part of **StakeTime** that has been folded into BlocTime. The rest of StakeTime is its full subnet stack: Mod tokens, validators, pluggable consensus (Yuma by uptime, Linear, Staked, Private), six inflation curves and a 420-slot registry. It now lives in `contracts/staketime` and `bloctime.staketime`.

## 10. Governance and risk

- **Owner powers**: set the price, the multiplier curve, the maximum lock and the inflation schedule; emergency-withdraw stray tokens. A deployment can renounce ownership once it is configured.
- **Price is an input, not an oracle**: a dishonest owner can misprice the native token. The treasury model (1 token = $1 reserve) is the remedy.
- **Locks are final**: there is no early exit. Choose a lock you can live with.
- **Testnet**: the official instance runs on Base Sepolia. Nothing here is investment advice.

## 11. Summary

BlocTime measures commitment in the one currency everybody spends at the same rate: time. Lock dollars for seconds, receive BLOC, vote with it, and share a pot that pays once a week. Fork it, deploy it anywhere, and run it yourself.
