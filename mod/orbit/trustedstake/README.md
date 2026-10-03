# trustedstake

**Non-custodial Bittensor index staking — the manager desk, in the shell.**

[TrustedStake](https://trustedstake.ai) turns TAO staking into index funds.
Instead of picking a validator and a subnet by hand, a delegator signs **one**
extrinsic — `addProxy(<index proxy account>, Staking)` — and from then on an
index manager rebalances that stake across a basket of subnets. The Substrate
proxy pallet is the whole trust story: the proxy can stake and restake, it
**cannot move the principal**, and the delegator leaves by revoking it. The TAO
never leaves their coldkey.

This module wraps the **manager** side of that platform: the Manager API at
`https://api.app.trustedstake.ai/api/v1/manager-api`. If you run an index, this
is how you run it from a terminal or an agent instead of the dashboard.

```
m trustedstake                            # what this is, and whether a key is set
m trustedstake/strategies                 # your strategies
m trustedstake/strategy <id>              # one, in full
m trustedstake/weights 8:70,120:30        # validate a basket — no network, no key
m trustedstake/update <id> weights=8:70,120:30
m trustedstake/rebalance <id>             # queue one now
m trustedstake/activity <id>              # what the engine actually did
m trustedstake/delegators <id>            # who is staked behind you
m trustedstake/pause <id>                 # stop rebalancing, leave the stake
m trustedstake/resume <id>
m trustedstake/delete <id> confirm=1      # permanent — asks first
m trustedstake/raw GET /strategies        # any Manager API route
```

## BYOK

This module holds no house key. Mint one in the platform dashboard
(**Manager → Settings → API Keys**, wallet signature required — it is shown
once), then:

```
m trustedstake/set_key ts_mk_…            # → ~/.mod/trustedstake/key.json (0600, off-tree)
```

Resolution order: `key=` argument → `TRUSTEDSTAKE_API_KEY` →
`TRUSTEDSTAKE_MANAGER_KEY` → the keystore. The key is never returned by any fn;
`info` and `set_key` show it masked.

Keys are **scoped**, so a key can be read-only by construction:

| scope | unlocks |
|---|---|
| `strategies:read` | `strategies`, `strategy`, `delegators` |
| `strategies:write` | `create`, `update`, `set_weights`, `pause`, `resume`, `delete` |
| `rebalances:trigger` | `rebalance` |
| `operations:read` | `activity` |

A call that needs a scope you did not grant comes back **403 before anything
happens** — mint a `strategies:read` key for monitoring and keep the write key
somewhere else.

## Weights are checked here first

The API requires `targetConstituents.subnetWeights` to sum to exactly 100. This
module parses what a shell makes easy (`8:70,120:30`, `sn8=50;120=50`, JSON, or
a dict) and validates it **locally**, so a malformed basket costs no call and no
rate-limit budget:

```
$ m trustedstake/weights 8:70,120:20
weights sum to 90, not 100 — add 10
```

`m trustedstake/weights <basket>` also prints the exact request body and the
published minimum delegator balance for a basket that size.

## Keyless

`info`, `health`, `weights`, `fees`, `limits`, `scopes`, `docs`, `test` and
`readme` need no key and reach at most the public `/health` route.
`m trustedstake/test` is a pure offline self-check (18 assertions over the
weight parser, the balance tiers and the error unwrapping).

## Published numbers, verified 2026-09-10

- **Fee: 9% take rate** on validator dividends, quoted as an effective
  **~6.62%** after the Kraken Institutional partnership (docs, as of
  2026-05-07). Charged on **yield only, never on principal**; no management or
  performance fee is documented.
- **Rebalancing:** automatic, hourly, when profitability thresholds are met —
  TWAP/DCA sizing with anti-MEV handling. Manual rebalances are immediate and
  capped per strategy per day; some cadences are plan-gated
  (`REBALANCE_FREQUENCY_GATED`).
- **Limits:** 3 active strategies and 5 active API keys per wallet; 130
  requests/minute per key (`429` + `RATE_LIMIT_EXCEEDED` above it); key
  lifetime 90 days by default, 365 maximum.
- **Minimum delegator balance** by basket size: 1–3 constituents → 0.5 TAO,
  4–6 → 1 TAO, 7–12 → 2 TAO. The published table stops at 12, and so does
  this module — above that, the API enforces the real floor.

These are read off the vendor's own docs and kept in `fees()` / `limits()` with
that date attached. The API is always the authority.

## What this does not do

- **It signs no extrinsic.** Delegating is `addProxy` from the delegator's own
  wallet, in their own wallet extension. Nothing here holds a seed or a coldkey.
- **It is not the delegator side.** There is no public read API for browsing
  indexes or checking a position; this wraps the documented manager surface
  only. For on-chain TAO, subnets, prices and stake, use the `bt` module.
- **It cannot touch anyone else's stake.** The key scopes it to the strategies
  its own wallet owns, and a strategy's delegators keep custody throughout.
- **`delete` asks.** It is permanent and visible to everyone delegated to the
  strategy, so it refuses without `confirm=1`. `pause` is the reversible move.

## Layout

| file | what |
|---|---|
| `mod.py` | the anchor — Manager API client, weight parser, every fn, offline tests |
| `config.json` | the manifest: fns, endpoints, BYOK, limits, custody model |
| `README.md` | this |

Python stdlib only (`urllib`). No port, no server, no dependencies.

## Upstream

- App: <https://app.trustedstake.ai> · Site: <https://trustedstake.ai>
- Docs: <https://trustedstake.gitbook.io/trustedstake>
- Manager API: <https://trustedstake.gitbook.io/trustedstake/strategies/manager-api>
