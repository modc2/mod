# dollartime

**Own a revenue-generating thing together, in dollars and time.** StakeTime
weighed *stake* by time locked. DollarTime weighs *dollars* by time locked:
put dollars into a treasury that buys one thing (a house, a GPU box), lock
them for 0 to 1460 days, and you earn that day's share of a fixed emission.
The token (DTT) is the deed: revenue the thing earns is split by DTT held,
and the steward's valuation of the thing prices every DTT.

The same rules run three times, pinned to the same numbers by three test
suites: a paper ledger here, Solidity on the staketime EVM stack (Base
Sepolia), and Rust on NEAR.

## The rules

| | |
|---|---|
| **cap** | 21,000,000 DTT, ever. |
| **emission** | at most 7,200 DTT a day. Day `d` from genesis emits 7,200 for `d < 2916`, then 4,800 on day 2916, then nothing: `2916 x 7200 + 4800 = 21,000,000` exactly. |
| **buying** | `contribute(dollars, lock_days)` puts dollars in the treasury. Your weight for that day is `dollars x (365 + lock_days)`: no lock counts as one year, a four-year lock counts five times. The day's emission is split by weight, `tokens = emission x w / W`, claimable once the day closes. |
| **burning** | a day nobody buys is **burned**: its emission is never minted and still counts against the cap. Nothing rolls over. If no one buys anything, nothing is minted. |
| **lock** | DTT from a locked contribution cannot be transferred until the lock ends. It still earns revenue. |
| **revenue** | `revenue(dollars)` is what the thing earned (rent, compute fees). It is split pro-rata across every DTT in existence at that moment (accumulator-per-share, 1e36). Before any DTT exists it pools in the treasury. |
| **treasury** | contributed dollars pool here. The steward spends them on the thing and marks its valuation, so `value(you) = valuation x balance / supply` and `share = balance / supply`. |

Units: dollars are 6-decimal integers (USDC style); DTT has 18 decimals.
All arithmetic is integer and identical on every layer.

## Paper (this module)

Play money. `join(name)` returns a **token** (shown once) and 10,000 paper
dollars; the first name to join is the steward.

```bash
m dollartime/serve                              # console + API on :51410
open http://localhost:51410/dollartime/

m dollartime/join name=alice                    # → {token, dollars: 10000000000, steward: true}
m dollartime/contribute token=$T dollars=100 lock_days=365
m dollartime/stats                              # day, emission today, sold/burned, supply, treasury
m dollartime/claim token=$T                     # after the day closes: mint your share
m dollartime/me token=$T                        # dollars, DTT, locked, owed, share, value
m dollartime/revenue token=$T2 dollars=40 memo=rent
m dollartime/withdraw token=$T
m dollartime/spend token=$T to=seller dollars=150 memo="buy the machine"   # steward
m dollartime/set_asset token=$T name="GPU box" valuation=1000             # steward
m dollartime/days · holders · history · chains
m dollartime/test                               # paper suite; chains=true adds hardhat + cargo
```

Dollar arguments are human dollars (`12.50`); add a `u` suffix for raw
micro-dollars. DTT arguments are whole tokens; `w` suffix for raw wei.

HTTP: reads are `GET /{fn}?args`, writes are `POST /{fn}` with a JSON body —
see `config.json → endpoints`.

## EVM — `evm/` (staketime stack, Base Sepolia)

`DollarTime.sol` is an ERC-20 (DTT) plus the rules. Contributions are a
6-decimal stablecoin (`MockUSD` for tests and local nets). `contribute`,
`claim`/`claimAll`, lock-aware `transfer`, `depositRevenue`,
`withdrawRevenue`, steward `spend`/`setAsset`, views `stats`, `burned`,
`claimable`, `owed`, `shareBps`, `valueOfHolder`.

```bash
m dollartime/evm_test                                     # 10 hardhat tests
cd evm && PRIVATE_KEY=... ./hh run scripts/deploy.js --network base_sepolia
m dollartime/evm_deploy dollar=0x... asset="GPU box"     # same, writes evm/deployments.json
```

`hh` picks a Node ≥ 22 on the box; `node_modules` is a symlink to
`orbit/staketime`'s.

## NEAR — `near/`

`near/src/lib.rs` (near-sdk 5) is a NEP-141 token (DTT) plus the rules.
Dollars arrive as a NEP-141 stablecoin through `ft_transfer_call`:

```
ft_transfer_call(receiver_id=<dollartime>, amount, msg="365")          # contribute, lock 365 days
ft_transfer_call(receiver_id=<dollartime>, amount, msg="revenue:rent") # deposit revenue
```

Then `claim(id)` / `claim_all()`, `withdraw_revenue()` (pays out in the
stablecoin, re-credits on failure), steward `spend` / `set_asset` /
`set_steward`, views `stats`, `me(account_id)`, `positions_of`, `claimable`,
`burned`, `owed`, `share_bps`, `value_of`. The 1e36 accumulator uses a
256-bit `mul_div` so the numbers match the EVM to the wei.

```bash
m dollartime/near_test                     # 22 unit tests
m dollartime/near_build                    # → dollartime.wasm (~430 KB)
m dollartime/near_deploy account=dollartime.<you>.testnet dollar=usdc.fakes.testnet asset="GPU box" create=true
```

Deploys go through `orbit/near`'s keystore (`~/.mod/near/`); testnet by
default, `confirm=true` elsewhere. The deployed account is written to
`config.json → chains.near`.

## Files

| file | what |
|---|---|
| `engine.py` | the paper ledger: the rules, persistence, injectable clock |
| `mod.py` | the module: every fn above, chain helpers |
| `serve.py` | console + API on one port |
| `web/index.html` | the console |
| `tests/test_engine.py` | the rules, pinned (11) |
| `evm/contracts/DollarTime.sol`, `MockUSD.sol`, `evm/test/` | EVM twin (10) |
| `near/src/lib.rs` | NEAR twin, with its tests (22) |

State: `~/.mod/dollartime/ledger.json` (override with `DOLLARTIME_DIR`).
