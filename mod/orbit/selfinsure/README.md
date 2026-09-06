# selfinsure

**A member-owned insurance mutual as an Ethereum smart contract — with the
provider's profit published on chain, an optional oracle for real data, and a
ready-to-deploy template for a US health mutual.**

The premise is one accounting rule: *a premium becomes pool money and stays pool
money.* There is no house account. What is left after claims is owed back to the
people who paid it in. An operator fee exists because a pool may want to pay for
its own administration — but it defaults to **0%**, is **hard-capped at 10%** in
the contract, can only be **raised after a 7-day on-chain notice**, and every
unit of it is published by `transparency()` as `operatorShareBps`: the
provider's profit, as a share of every premium ever paid, readable by anyone
with no key.

Compare the thing it is a template against: US health insurers are required by
the ACA to spend 80–85% of premium on care (the "medical loss ratio") and file
that number once a year. Here the equivalent is a view function, live, per pool,
and the *maximum* an operator can keep is 10% — with the default being nothing.

```
contracts/
  src/SelfInsure.sol            the mutual (22 KB, one file, no imports)
  src/SelfInsureFactory.sol     anyone opens a pool; openHealth() = the template
  src/oracles/SignedOracle.sol  real data, signed by named reporters
  test/SelfInsure.t.sol         35 forge tests
  script/Demo.s.sol             a whole health mutual on a local anvil
pool.py     the same mutual off-chain (cents, JSON ledger) — for pools that are not on a chain yet
chain.py    the bridge to the eth / solana modules (this module holds no keys)
onchain.py  source · abi · presets · deploy through the eth module · read a live pool back
mcp.py      26 MCP tools (si_*) for agents — adjudicating claims is the point
api.py      one port: REST + POST /mcp + the transparency page
```

## What the contract guarantees, in code

| Guarantee | Where |
|---|---|
| Operator fee ≤ 10% of premium, forever | `MAX_FEE_BPS = 1000` (constant); `_checkTerms`, `proposeFee` |
| A fee **raise** is announced on chain and applies 7 days later; a **cut** is immediate | `proposeFee` / `applyFee`, `FEE_NOTICE = 7 days` |
| `setTerms` cannot change the fee at all | `BadTerms("use proposeFee")` |
| Every premium lands in the pot; only the fee leaves | `_credit` |
| The provider's take is public: accrued, withdrawn, and as bps of gross premium | `transparency().feesAccrued / feesWithdrawn / operatorShareBps`, `FeesWithdrawn` event |
| Surplus goes back to members pro rata to net contribution, pulled by each member | `distribute`, `claimRebate`, per-stake accumulator |
| No surplus leaves while any accepted claim is unpaid | `distribute` → `ClaimsOwed` |
| Open claims and a reserve floor are held back from distribution | `distributable()` |
| A claim the pool cannot fund is recorded as **owed**, not reduced, and paid oldest-first from the next money in | `_pay`, `_payBacklog`, `unfundedQueue()` |
| A claim is judged under the terms it was filed under | `Frozen` per claim |
| Adjudicators cannot vote twice, without a reason, or on their own claim | `vote` |
| Every ballot, with its reason and whether the judge is AI or human, is public | `ballots(id)`, `agents(addr)` |
| The books reconcile with what the contract holds | `transparency().reconciles` |
| The oracle is optional, and its mode is frozen per claim | `OracleMode`, `oracleView(id)` |

## The oracle (optional — for real data)

`ISelfInsureOracle` is one view: `attestation(pool, claimId) → (attested, ok,
verifiedAmount, dataHash, at)`. A pool can run in four modes:

| mode | what it does |
|---|---|
| `none` | adjudicators alone |
| `advisory` | the attestation is recorded beside the votes and emitted at settlement; it does not gate or cap |
| `required` | a claim cannot settle until attested; `ok=false` rejects it regardless of votes; the payout is capped at `verifiedAmount` |
| `automatic` | the attestation alone settles the claim — no votes needed (parametric cover) |

`SignedOracle` is the shipped implementation. Its owner names **reporters** —
a hospital's billing system, a claims auditor, a Chainlink Functions consumer
contract — and a reporter signs `(chainId, oracle, pool, claimId, ok,
verifiedAmount, dataHash, expiry)`. **Anyone** may relay that signature with
`submit()`; a contract reporter calls `report()` directly. `dataHash` is the
keccak256 of the underlying record (the itemised bill, the EOB) so the data
behind every payout can be published and checked against the chain later.
Everything written to the oracle is public and permanent: who attested, when,
to what amount, under which key.

## The health template

```solidity
factory.openHealth("Travis County health mutual", "", USDC, 1e6, oracle, OracleMode.Required);
```

| term | value | why |
|---|---|---|
| premium | $400 / 30 days | roughly a single adult's marketplace premium |
| coverage | $50,000 per claim | one hospitalisation, most surgeries |
| deductible | $250 | low, because the point is to be used |
| annual cap | $250,000 per member per policy year | |
| waiting period | 30 days | stops joining on the way to the ER |
| reserve floor | $25,000 | never distributed — the pool's own backstop |
| **operator fee** | **0%** | the operator keeps nothing; the contract caps it at 10% |
| quorum / threshold | 2 votes, 66% | two adjudicators, both must agree |
| adjudicators | approved only | the pool admits who judges its claims |

Every number is a starting point the owner tunes with `setTerms`. The same
preset is available off-chain as `si_preset preset=health decimals=6`.

How a community, employer, union or county uses it:

1. Deploy `SignedOracle`; name the billing systems or auditors you trust as reporters.
2. `openHealth(...)` on a USD stablecoin with the oracle in `required` mode.
3. Members `join(400e6)` (after `approve`). Coverage starts in 30 days.
4. Register adjudicators — humans, AI agents, or both; the pool admits them.
5. A member files the provider's itemised bill as a claim. Adjudicators vote
   with reasons. The reporter attests the bill. The claim settles and pays in
   that call; if the pot is short, the rest is owed and paid from the next premiums.
6. At the end of a period the owner calls `distribute(0)`; members `claimRebate()`.
7. Anyone, at any time: `transparency()`.

## Run it

```bash
cd contracts && forge test                                   # 35 tests
forge script script/Demo.s.sol --tc Demo --rpc-url http://127.0.0.1:8545 --broadcast
python3 -m pytest test/                                      # the off-chain engine
python3 api.py --port 50850                                  # REST + MCP + page
python3 mcp.py                                               # MCP over stdio
```

Deploying through the module signs with the **eth module's keystore** —
selfinsure never holds a key:

```
si_deploy account=<eth keystore account> network=base-sepolia asset=0x... oracle=0x... oracle_mode=required
si_onchain address=0x...          # money, provider block, solvency, terms — no key
si_onchain_claim address=0x... claim=1
```

The contract is also installed in the eth module's template catalog as
`mutual`, so `POST /deploy {"template": "mutual", ...}` there works too.

## Off-chain pools

`pool.py` is the same mutual without a chain — integer minor units, a JSON
ledger under `~/.mod/selfinsure/`, keys shown once and stored hashed. It is
where a pool starts before it has an asset and an address, and it is what the
first 21 `si_*` tools drive. Its `fee_bps` cap, unfunded queue, frozen terms
and pro-rata distribution mirror the contract line for line.
