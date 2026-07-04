# DeFi

A modular, low-fidelity yield aggregator. Users deposit a whitelisted asset
(USDC/USDT/WETH/…) into one of many registered **strategies**; each strategy is a
pluggable adapter over an external DeFi venue. Harvested profit is routed through the
existing **Market** contract and minted to depositors as the native token, distributed
pro-rata. Principal stays in the adapter, redeemable ~1:1.

## Why this shape

- **Multiple lowfi yield options, modular.** A strategy = one `IYieldAdapter` over one
  asset. The owner can register any number of strategies (e.g. a conservative mock, an
  aggressive mock, Aave) without changing the vault — new venues drop in behind the
  interface.
- **Profit → native token via Market.** On `harvest`, realized profit (the adapter's
  `totalAssets` above tracked principal) is sent to `Market.mint`, which forwards the
  underlying to the Treasury and mints native tokens to the vault. The vault distributes
  those native tokens with a MasterChef-style accumulator. Every vault asset must be
  whitelisted + priced in `TokenGate` (Market requires it), or `harvest` reverts.
- **No share-price inflation.** Shares are minted 1:1 with *measured* principal (no
  price-per-share), so the first-depositor / donation attack does not apply.

## Contracts

| Contract | Purpose |
|---|---|
| `IYieldAdapter` | The modularity seam: `asset / deposit / withdraw / totalAssets` over one venue |
| `YieldVault` | Multi-strategy vault: deposit/withdraw principal, harvest, pro-rata native rewards |
| `MockYieldAdapter` | Self-contained venue for localhost/CI/demos; owner funds yield via `addYield` |
| `AaveV3Adapter` | Real adapter over Aave V3 (`supply`/`withdraw`, aToken balance); Base mainnet |
| `test/MockERC20` | Test-only ERC20 with configurable decimals (e.g. 6-dec USDC) |

## Interface (YieldVault)

| Function | Description |
|---|---|
| `addStrategy(asset, adapter, name)` | Register a new yield strategy (owner) |
| `setStrategyEnabled(id, bool)` | Enable/disable deposits to a strategy (owner) |
| `setAdapter(id, newAdapter)` | Migrate a strategy to a new venue, principal preserved (owner) |
| `deposit(id, amount)` | Deposit the strategy's asset, mint principal shares |
| `withdraw(id, shares)` | Burn shares, receive underlying back (allowed while paused) |
| `harvest(id)` | Realize yield → `Market.mint` → distribute native to depositors |
| `claim(id)` | Claim accrued native reward tokens |
| `pendingReward(id, user)` / `pendingProfit(id)` / `userShares(id, user)` | Views |

## Test

```sh
npx hardhat test src/contracts/defi/test/
```

Covers deposit/withdraw 1:1, harvest routing profit to Treasury + pro-rata native
distribution, multi-user splits, late-depositor reward isolation, zero-shares no-op,
revert when the asset is delisted from the Market gate, pause semantics, and multiple
strategies side by side.

## Deploy

```sh
npx hardhat run scripts/deploy-defi.js --network localhost
```

Deploys `YieldVault` (reward token = the Market token), a `MockYieldAdapter` per stable
asset, registers them as strategies, and writes the addresses into `config.json`. Wire a
real `AaveV3Adapter` on mainnet by deploying it with the Aave Pool + aToken addresses and
calling `addStrategy`.

## Backend / app

- Python: `m chain/yield_strategies`, `yield_deposit`, `yield_withdraw`, `yield_harvest`,
  `yield_claim`, `yield_position` (in `src/mod.py`).
- API: `GET /yield/strategies`, `GET /yield/position`, `POST /yield/{deposit,withdraw,harvest,claim}`.
- UI: the **DeFi** tab in the protocol app (`src/app/.../protocol`).
