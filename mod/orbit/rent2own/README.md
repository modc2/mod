# rent2own

Rent-to-own agreements you can check with a pencil.

A rent-to-own deal is four numbers and a clock: the **price** agreed today, the
monthly **rent**, the **credit** share of each payment that counts toward buying,
and the **months** until the option to buy is exercised. `rent2own` turns those
into a month-by-month schedule — equity built, strike price, balance left to
finance — and keeps saved agreements with their payment logs in one local JSON
file.

Stdlib only. No listing API, no lender, no chain — nothing here calls out.

## Use

```
m rent2own                                            # info
m rent2own/quote price=300000 rent=2200 credit=0.25 months=36
m rent2own/quote price=300000 rent=2200 appreciation=0.03 option_fee=6000 full=true
m rent2own/save name="12 Elm St" price=300000 rent=2200 months=36 renter=alice
m rent2own/agreements
m rent2own/agreement id=<id>
m rent2own/pay id=<id>                                # defaults to the agreed rent
m rent2own/remove id=<id>
m rent2own/test
```

## The math

For month `m` of `N`:

| field       | formula                                             |
|-------------|-----------------------------------------------------|
| `equity`    | `option_fee + rent * credit * m`                    |
| `strike`    | `price * (1 + appreciation) ** (m / 12)`            |
| `balance`   | `max(strike - equity, 0)` — what's left to finance  |
| `owned_pct` | `equity / strike`, capped at 100                    |

`quote` also returns `at_exercise`: total paid, how much of it was credited,
and how much was plain rent.

## Terms

| term           | default | meaning                                     |
|----------------|---------|---------------------------------------------|
| `price`        | —       | purchase price agreed today (required)      |
| `rent`         | —       | monthly payment (required)                  |
| `credit`       | `0.25`  | fraction of rent credited, `0..1`           |
| `months`       | `36`    | term, `1..600`                              |
| `option_fee`   | `0`     | up-front fee, credited toward purchase      |
| `appreciation` | `0`     | yearly; `0` locks the price today           |

## Data

`~/.mod/rent2own/agreements.json` (override with `RENT2OWN_DATA`). Written
atomically (temp file + rename). Agreement ids are the first 10 hex of a
SHA-256 over terms and parties, so saving the same deal twice is a no-op.

Only reads (`info`, `health`, `readme`, `quote`, `agreements`, `agreement`)
are in `api_fns`; `save`/`pay`/`remove` are CLI-only until the module gets an
auth story.

## Related

`openhouse` is the on-chain rent-to-own protocol (fee-capped contract,
renter equity as shares). `rent2own` is the offline calculator and ledger —
use it to reason about a deal before anything touches a chain.
