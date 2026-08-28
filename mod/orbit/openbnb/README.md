# openbnb

An open short-stay marketplace whose rules are **data, not code**. Hosts list
places, guests book nights — and the module owner rewrites how the market
behaves while it is running: fees, minimums, who may list, what a stay costs,
who gets auto-approved. No redeploy, no code edit, no restart.

- **App** → `https://modc2.com/openbnb` (local `:50371`)
- **API** → `/openbnb/api` (local `:50370`, OpenAPI at `:50370/docs`)
- **State** → `~/.mod/openbnb/` — listings, bookings, policy, rules, hooks, owner key

## The three programmable layers

| Layer | What it is | Owner calls |
|---|---|---|
| **Policy** | A typed document of every knob the market runs on. Each one is validated; unknown keys are refused so a typo can't create a setting nothing reads. | `policy()`, `set_policy()`, `reset_policy()`, `policy_schema()` |
| **Rules** | An ordered list of `when → then` rules evaluated on every quote and booking. | `rules()`, `add_rule()`, `update_rule()`, `move_rule()`, `delete_rule()`, `test_rule()`, `sandbox()` |
| **Hooks** | Outbound webhooks per event, so the market can drive anything else you run. | `hooks()`, `add_hook()`, `delete_hook()`, `hook_deliveries()` |

Plus live-data overrides (`owner_delete_listing`, `owner_set_booking_status`, …)
and whole-market `export_state()` / `import_state()`.

### Policy

```bash
m openbnb/policy                          # read the live document
curl -s $A/policy                         # …it's public: guests can read the house rules
m openbnb/set_policy owner_key=$K fee_bps=300 fee_wallet=0x89bc…
```

Knobs: `market_name`, `tagline`, `currency`, `chain`, `fee_bps`, `fee_wallet`,
`open_listings`, `listing_cap_per_host`, `require_listing_review`,
`instant_book_default`, `min_nights`, `max_nights`, `booking_horizon_days`,
`max_guests`, `cities`, `kinds`, `amenities`, `cleaning_fee_cap`,
`cancellation`, `blocked_words`, `guest_review_required`.

Guard rails are enforced, not suggested: `fee_bps` is capped at 2000 and refused
without a `fee_wallet`; `min_nights` can't exceed `max_nights`.

### Rules

A rule is `when` (an expression) + `then` (effects). Rules run top to bottom;
effects stack; the first `deny` wins.

```bash
curl -X POST $A/owner/rules -H "X-Owner-Key: $K" -H 'Content-Type: application/json' -d '{
  "name": "Weekly stay 20% off",
  "when": "nights >= 7",
  "then": {"pct": -20, "tag": "weekly"}
}'
```

**Effects** — `deny` (message), `review` (force host approval), `pct` (percent of
subtotal), `flat` (absolute), `min_nights` (raise the floor), `tag` (label).

**Facts** — `nights`, `guests`, `lead_days`, `checkin_dow`, `checkout_dow`,
`checkin_month`, `weekend`, `price`, `cleaning_fee`, `subtotal`, `city`, `kind`,
`amenities`, `instant_book`, `listing_id`, `host`, `guest`, `guest_stays`,
`listing_stays`, `repeat_guest`. `GET /owner/sandbox` documents all of it.

**The sandbox is the point.** `when` is parsed with `ast` and walked against a
node whitelist at *write* time — no attribute access, no imports, no
comprehensions, and only ten numeric/length builtins. An unknown name is a
write-time error naming the valid facts, so a rule can't silently never fire:

```
$ m openbnb/add_rule owner_key=$K when="__import__('os')" ...
only these calls are allowed: abs, all, any, bool, float, int, len, max, min, round
$ m openbnb/add_rule owner_key=$K when="nite > 2" ...
unknown fact 'nite' — available: amenities, checkin_dow, …
```

Every quote carries the trace of which rules matched and what each one did, so a
price is always explainable — the guest sees it too, under "why this price".

### Hooks

```bash
m openbnb/add_hook owner_key=$K url=https://example.com/openbnb
```

Events: `listing.created|updated|removed`, `booking.quoted|created|confirmed|declined|cancelled`,
`policy.updated`, `rule.updated`. Delivery is threaded and best-effort — a dead
endpoint never blocks a booking — with the last 200 attempts in
`hook_deliveries()`.

## Owner key

One secret, off-tree, never in committed config: `$OPENBNB_OWNER_KEY` if set,
otherwise minted once into `~/.mod/openbnb/owner.json` (chmod 600). Send it as
the `X-Owner-Key` header or an `owner_key` body field — so everything the console
does is also a one-line curl. It doubles as a master `host_key`, giving the owner
organizer rights on every listing.

## Who can do what

- **Guest** — a handle. Browse, quote, book, cancel. No wallet needed until a
  market charges for something.
- **Host** — creating a listing returns a one-time `host_key`, kept in the
  creator's browser. It gates edit/pause/block-dates and approve/decline on that
  listing's bookings.
- **Owner** — the module secret. Everything above, plus the three layers.

## Booking flow

`quote()` is the whole engine in one call: policy checks (horizon, guest count,
night bounds) → availability (bookings + host-blocked nights) → the owner's rules
→ line items. `book()` re-quotes and confirms instantly, unless the listing isn't
instant-book, or a rule said `review`, or policy says `guest_review_required`;
then it lands as `pending` for the host. Cancellation refunds follow
`policy.cancellation` (full / partial / none by days out).

Payment is quoted, not custodied: `payment_requirements(booking_id)` returns x402
requirements (asset, atomic amount, host receiver, fee receiver, facilitator) for
the configured token on Base.

## Usage

```python
import mod as m
ob = m.mod("openbnb")()
ob.serve()                                   # api :50370 + app :50371, registers the gateway
key = ob._owner_secret()                     # or read ~/.mod/openbnb/owner.json

ob.seed_demo(key)                            # opt-in demo listings; wipe_demo() undoes it
lid = ob.listings()[0]["id"]
ob.quote(lid, "2026-08-17", "2026-08-24", guests=2, guest="sam", explain=True)
ob.add_rule(key, "Long stays", "nights >= 7", {"pct": -20, "tag": "weekly"})
ob.set_policy(key, fee_bps=300, fee_wallet="0x…")
ob.book(lid, "sam", "2026-08-17", "2026-08-24", guests=2)
```

```bash
m openbnb/status
m openbnb/listings city=toronto
m openbnb/quote listing_id=l_… checkin=2026-08-17 checkout=2026-08-24 guests=2
m openbnb/serve            # dev; serve dev=False for the production build
```

## API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/listings` `/listing/{id}` `/listing/{id}/calendar` | public browse + availability |
| GET | `/policy` `/rules` `/owner/sandbox` `/owner/policy_schema` | the house rules, in the open |
| POST | `/quote` | prices a stay; `explain:true` returns the rule trace |
| POST | `/book` | confirms or goes pending, per rules + policy |
| POST | `/listings` | create; returns a one-time `host_key` |
| POST | `/listing/{id}/edit` `/status` `/block` | host-only, `{host_key, …}` |
| POST | `/booking/{id}/approve` `/decline` `/cancel` | host key, or guest handle to cancel |
| GET | `/booking/{id}/payment` | x402 requirements |
| POST | `/owner/state` `/policy` `/rules` `/hooks` `/export` `/import` … | owner-gated (`X-Owner-Key`) |
| POST | `/forward` | any mod function by name |

## Notes

- No seeded data. A fresh install is empty; `seed_demo` is owner-only, labels its
  hosts `demo_*`, and `wipe_demo` removes exactly those.
- The app is a Next build behind the gateway (basePath `/openbnb`, API proxied at
  `/openbnb/api`). `serve_app(dev=False)` runs the production build under pm2 as
  `openbnb-api` / `openbnb-app`.
- Next is pinned to 14.2.33 to match the rest of the fleet; npm flags the whole
  14.x line for an advisory whose fix ships in 15.x.
