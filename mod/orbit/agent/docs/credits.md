# Credits, provider funding, and the margin

A guest who has no OpenRouter or Venice key can still run the agent: they
top up with USDT/USDC and spend the credits on the module's own provider
key. This document is how that money moves.

**The loop.** A deposit is the guest pre-funding the provider credits their
own runs will burn. Every run is billed at what it actually cost on the
module's key, plus a margin — 5% by default, owner-settable. The owner
buys OpenRouter/Venice credits out of the deposit float and logs it; the
treasury says how much has to go over.

```
guest deposits USDT/USDC ──▶ credit balance (1 credit = $1)
                                 │
                run  ──▶ metered provider cost × (1 + fee_rate) debited
                                 │
                    ┌────────────┴────────────┐
              provider_cost                  fee
        (owed to OpenRouter/Venice)     (the module keeps it)
                    │
          owner buys API credits, records the top-up
```

## What a run costs

`src/billing.py` prices every model call from the provider's own live
catalog — OpenRouter quotes USD per token under `pricing`, Venice quotes
USD per million tokens under `model_spec.pricing` — so a step on Opus and
a step on a small model are not billed the same.

Token counts are **estimated** from character counts (chars / 4): both
providers stream plain text back and neither surfaces a usage block
through the model module, so there is no exact count to read. Two knobs
handle the gap:

- `cost_multiplier` — a safety factor applied to the estimate (default 1.0).
- the treasury's **drift** readout — OpenRouter reports lifetime usage on
  the key, so `actual` (usage since the first treasury read) against
  `billed` (metered cost booked since then) says whether the estimate runs
  light. Owner runs burn the same key and are never billed, which pushes
  actual above billed — read the ratio as a signal, not a dial.

A run the meter can't price (unknown model, a harness CLI run) falls back
to the flat `price_per_step`, so nothing runs free by accident.

Tallies are per-thread — one Mod instance serves every concurrent run —
and a chain accumulates across its stages, billed once at the end.

## Who pays

| caller | billed |
| --- | --- |
| module owner | never — own key, own money |
| guest, spend toggle ON | metered cost + `fee_rate`, clamped to balance |
| guest, spend toggle OFF | nothing — the run is pinned to free models |

A charge is clamped to the balance and the clamped amount is split on the
same ratio, so the books still say how much of what was collected is owed
to the providers. An account never goes negative, and a zero balance
closes the run gate again (`is_allowed`).

## The books

`GET /credits/treasury` (owner) returns:

| field | meaning |
| --- | --- |
| `deposits` / `grants` | real money in / credits the owner handed out |
| `revenue` | total billed to guests |
| `provider_cost` | the part of that owed to the providers |
| `fees` / `fees_available` | our margin, earned / not yet withdrawn |
| `topups` / `topups_total` | API credits bought at each provider |
| `topup_pending` | bought on a key but not booked yet — confirm it |
| `float` | cash held that isn't deployed or taken |
| `user_credits` | unspent guest credits — the liability |
| `funding_required` | that liability at cost (credits ÷ 1 + fee_rate) |
| `provider_balance` | live balance across the provider keys |
| **`topup_needed`** | `funding_required − provider_balance` — send this now |

## Topping up a provider key

Neither provider sells credits over an API. OpenRouter's Coinbase endpoint
(`POST /api/v1/credits/coinbase`) was removed and answers **410 Gone** —
"use the web credits purchase flow instead" — and Venice never had one. So
the purchase itself is always a trip to the provider's page:

| provider | buy credits at | what the module can read |
| --- | --- | --- |
| openrouter | `openrouter.ai/settings/credits` | `total_credits` — credits ever bought on the key, so a rise in it **is** the purchase, exactly |
| venice | `venice.ai/settings/api` | the USD balance only — a rise above the last mark is a purchase, but spending moves it too |

The console closes that loop instead of asking for a typed amount. The
treasury panel's **Top up a provider key** row opens the provider's page and
then watches the key: every eight seconds it re-reads the meter, and the
moment the money lands it books exactly what arrived
(`ledger` entry, `verified: true`, `ref: purchased 477.0 → 527.0`).

`mark` is the meter as of the last booked top-up. For OpenRouter it only
moves when a top-up is booked. For Venice the mark follows the balance
*down* as the key is spent, so a purchase after a spend is still booked at
full size — and only the rise above the mark counts. A hand-logged top-up
walks the mark past its own amount, so the same money is never booked twice.

```bash
m agent/credit_verify provider=openrouter     # book what landed on the key
```

## Endpoints

```
GET  /credits                 deposit address, pricing, caller's account
POST /credits/deposit         verify a tx hash, credit the on-chain sender
POST /credits/grant           owner: adjust an account (± amount)
GET  /credits/treasury?live=  owner: the books above (live= skips provider calls)
POST /credits/topup           owner: record credits bought {provider, amount, ref}
POST /credits/topup/verify    owner: book what landed on a key {provider}
POST /credits/withdraw        owner: take earned margin out {amount}
POST /credits/config          owner: {fee_rate, price_per_step, cost_multiplier,
                                      deposit_address}
```

The same actions exist on the mod protocol: `agent treasury`,
`agent credit_topup provider=openrouter amount=25`, `agent credit_verify
provider=openrouter`, `agent credit_config fee_rate=0.1`.

## Tailoring the margin

`fee_rate` is a fraction: `0.05` is 5%, `0` runs the key at cost, `0.5`
takes 50%. Set it from the console (TREASURY panel, "% margin") or:

```bash
m agent/credit_config fee_rate=0.08
```

It applies to charges made after the change; charges already booked keep
the split they were billed at.

## Where state lives

`~/.mod/agent/credits.json` — accounts, credited tx hashes, the treasury
book and the pricing config. Private auth state, off-tree, never in the
repo (same rule as the ACL).
