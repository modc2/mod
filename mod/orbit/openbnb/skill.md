---
name: openbnb
description: Open short-stay marketplace whose rules are data, not code. Hosts list places, guests book nights; the module owner rewrites policy (fees, minimums, who may list), pricing/eligibility rules, and webhooks at runtime — no redeploy. Sandboxed when→then rule engine over booking facts, with an explainable trace on every quote.
type: orbit-module
---

# openbnb

A short-stay market you can reprogram while it runs. Everything that decides
*how* the market behaves lives in owner-editable state, not in the source.

- **Ports**: API `50370`, app `50371`, gateway-routed at `modc2.com/openbnb`.
- **State**: `~/.mod/openbnb/` — `listings.json`, `bookings.json`, `policy.json`,
  `rules.json`, `hooks.json`, `owner.json` (0600).
- **Identity**: handles. Creating a listing returns a one-time `host_key`;
  the owner secret is a master key over everything.

## The programmable surface

**Policy** — one typed document, ~20 knobs (`fee_bps`, `min_nights`,
`max_nights`, `booking_horizon_days`, `cities`, `kinds`, `open_listings`,
`require_listing_review`, `guest_review_required`, `cancellation`, …). Every code
path reads policy; nothing is a constant. `set_policy()` validates against a
schema and refuses unknown keys, so a typo fails loudly.

**Rules** — an ordered `when → then` list evaluated on every quote/booking.
`when` is a sandboxed expression over booking facts; `then` is any mix of
`deny` / `review` / `pct` / `flat` / `min_nights` / `tag`. Effects stack in list
order; the first `deny` wins. Each rule tracks its hit count, and every quote
returns the per-rule trace — the guest sees which rule moved the price.

The sandbox is enforced at *write* time by walking the `ast` against a node
whitelist: no attribute access, no imports, no comprehensions, ten numeric
builtins only, and names must be known facts. Bad rules can't be saved.

**Hooks** — outbound webhooks on `listing.*`, `booking.*`, `policy.updated`,
`rule.updated`. Threaded, best-effort, last 200 deliveries logged; a dead
endpoint never blocks a booking.

Facts: `nights`, `guests`, `lead_days`, `checkin_dow`, `checkout_dow`,
`checkin_month`, `weekend`, `price`, `cleaning_fee`, `subtotal`, `city`, `kind`,
`amenities`, `instant_book`, `listing_id`, `host`, `guest`, `guest_stays`,
`listing_stays`, `repeat_guest`. `GET /owner/sandbox` is the live reference.

## Usage

### Python
```python
import mod as m
ob = m.mod("openbnb")()
ob.serve(dev=False)                          # api 50370 + app 50371 + gateway
key = ob._owner_secret()                     # or ~/.mod/openbnb/owner.json

ob.add_rule(key, "Weekly stay", "nights >= 7", {"pct": -20, "tag": "weekly"})
ob.add_rule(key, "No 1-nighters", "nights == 1 and weekend",
            {"deny": "Weekend stays are two nights minimum."})
ob.set_policy(key, fee_bps=300, fee_wallet="0x…")
ob.test_rule(key, "guests >= 3", facts={"guests": 4})   # dry-run before saving

l = ob.create_listing(host="maya", title="Loft", city="toronto", price=145)
ob.quote(l["id"], "2026-08-17", "2026-08-24", guests=2, guest="sam", explain=True)
ob.book(l["id"], "sam", "2026-08-17", "2026-08-24", guests=2)
```

### CLI
```bash
m openbnb/status
m openbnb/listings city=toronto
m openbnb/policy
m openbnb/set_policy owner_key=$K fee_bps=300 fee_wallet=0x…
m openbnb/quote listing_id=l_… checkin=2026-08-17 checkout=2026-08-24 guests=2
```

### HTTP (owner key as a header — the console has no private API)
```bash
K=$(python3 -c "import json;print(json.load(open('$HOME/.mod/openbnb/owner.json'))['secret'])")
curl -s -X POST localhost:50370/owner/rules -H "X-Owner-Key: $K" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Big groups","when":"guests >= 3","then":{"review":true}}'
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/listings` `/listing/{id}` `/listing/{id}/calendar` | public browse + taken nights |
| GET | `/policy` `/rules` `/owner/sandbox` `/owner/policy_schema` | rules are public by design |
| POST | `/quote` | policy → availability → rules → line items; `explain` adds the trace |
| POST | `/book` | instant or pending, per listing + rules + policy |
| POST | `/listings` | create, returns one-time `host_key` |
| POST | `/listing/{id}/edit` `/status` `/block` | host-only |
| POST | `/booking/{id}/approve` `/decline` `/cancel` | host key; guests cancel by handle |
| GET | `/booking/{id}/payment` | x402 requirements (USDC/USDT on Base) |
| POST | `/owner/*` | policy, rules, hooks, data overrides, export/import |
| POST | `/forward` | `{"action": "<any mod fn>", …}` |

## Gotchas

- A fresh install is **empty** — no seeded listings. `seed_demo` is owner-only and
  labels its hosts `demo_*` so `wipe_demo` can remove exactly those.
- `fee_bps > 0` is refused without a `fee_wallet`; the cap is 2000 (20%).
- Nights are half-open: check-out day is not a night. `blocked` (host) and
  `confirmed|pending` bookings both make a night unavailable.
- Rule order is precedence. `move_rule` reorders; disabled rules are skipped but
  keep their hit count.
- The app is a Next build behind the gateway (basePath `/openbnb`). Rebuild
  (`npm run build`) before `pm2 restart openbnb-app` when editing under
  `serve_app(dev=False)`.
