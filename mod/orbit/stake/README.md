# stake — BLOC on apps

Stake BlocTime (**BLOC**) on the apps registered in the mod protocol's on-chain
Registry. Every registered app is a staking pool: back an app with BLOC as a
curation signal, unstake any time (never locked), and anyone — typically the
app's owner — can add BLOC rewards that split pro-rata across that app's
stakers at that moment.

## On chain (Base Sepolia, chainId 84532)

| contract | address |
|---|---|
| AppStaking | `0x19DfA8C037E8c7aBBCE64a4C0B96CC00e9949138` |
| BlocTime (BLOC) | `0xF25AAFDd0A842ff50b041595C79210b48d6795bD` |
| Registry | `0xF7a5498369d7ceA13461BcfDC65995B8743baE97` |

`contracts/AppStaking.sol` is dependency-free Solidity (^0.8.20):

- `stake(appId, amount)` — requires the app to exist in the Registry and a
  prior ERC20 `approve`; pulls BLOC into the contract.
- `unstake(appId, amount)` — `amount = 0` unstakes everything. Never gated:
  works even after the app is removed from the Registry.
- `reward(appId, amount)` — adds BLOC to the app's reward pool, split
  pro-rata among current stakers (MasterChef `accRewardPerShare` accounting).
- `claim(appId)` / `claimMany(ids)` — pull accrued rewards.
- Views: `getStakedApps`, `getTotals`, `getAppStakers`, `getPositions`,
  `earned`, `totalStaked`, `totalStakedAll`.

## Console + API (one port, :50840)

`python3 api.py` (pm2 `stake-api`) serves:

- `/` — the console: app cards from the live Registry joined with catalog
  descriptions, staked bars, wallet-signed stake / unstake / claim / reward
  (browser wallet, auto chain-switch to Base Sepolia). Public at
  `modc2.com/stake`.
- `GET /apps`, `/apps/{id}` (with staker book), `/positions?address=`,
  `/contract`, `/info`, `/health`.
- `POST /stake|unstake|reward|claim` — server-signed convenience writes using
  a named mod key (`~/.mod/key/<name>/ecdsa`, default `test`); the console
  never uses these — it signs in the browser.

CLI: `m stake/apps`, `m stake/positions address=0x…`,
`m stake/stake app_id=4 amount=25`, `m stake/unstake app_id=4`,
`m stake/reward app_id=4 amount=5`, `m stake/claim app_id=4`.

## Build / test / deploy

- `python3 scripts/compile.py` — compiles via core/chain's solc bridge
  (:8800/build/compile, solc 0.8.26); artifacts in `artifacts/`.
- `python3 test/test_local.py` — full e2e on the local hardhat node (:8545):
  deploys the real Registry + MockBloc + AppStaking and walks every flow
  (28 checks, incl. reward splits, app-removal fund safety, balance
  conservation).
- `python3 scripts/deploy.py` — deploys to Base Sepolia with the mod server
  key and writes the address into `config.json`.
