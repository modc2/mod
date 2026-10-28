# zman — New York buys its buildings back

Mayor Zohran Mamdani's housing plan, *Block by Block* (May 2026), run as a
program on the [openhouse](../openhouse) framework. Municipal bonds pay for
New York buildings. Each building becomes a city-owned rent-to-own property.
Rent pays down the bonds and builds up the tenants' stake in the building.

> **Testnet bookkeeping only.** No bond is issued, no deed changes hands and
> no money is real. Key checks match addresses, not signatures (same as
> openhouse).

## The flow

```
investors ──buy──▶ bond series ──proceeds──▶ acquire(building)
                                                  │
                                   openhouse store per building
                                   owner = city · civic authority = city · fee 0%
                                                  │
tenant pays rent ──▶ openhouse split ──┬──▶ tenant equity (credit_pct)
                                       └──▶ owner income ──sweep──▶ bond.collected
                                                                        │
                                                   pay_coupon ──▶ holders, pro-rata
```

This is the same recipe as openhouse's "city-owned rent-to-own": the city holds
the bank seat and charters itself as the civic authority. zman adds one thing
openhouse doesn't have, which is the bond desk that pays for the buildings.

## What each piece does

| Piece | Where | Rule |
|---|---|---|
| City seat | `claim_city` | The first key to claim it holds it. Every later write needs that key. |
| Bonds | `issue_bond`, `subscribe` | Sales stop at the authorized principal. Payments are level (annuity). |
| Acquisition | `acquire` | Only money investors have actually paid in can be spent. Borough must be one of the five. |
| Split | openhouse `hybrid` by default | 50% of rent goes to tenant equity, 50% to debt service. Change it with `credit_pct=`. |
| Rent freeze | `set_freeze`, `set_rent` | On by default. While it's on, rent can only go down. |
| Civic pause | `house(id).civic_override` | Uses openhouse's pause as is. While paused, `pay_rent` refuses payments. |
| Coverage | `coverage(series)` | DSCR = scheduled bond share of the rent roll ÷ annual debt service. |

`house(building_id)` gives you the building's full openhouse instance
(`equity`, `rent_ledger`, `bank_reconcile`, ...). zman never copies that logic.

## Use

```python
import mod as m
z = m.mod('zman')()
z.claim_city(key='0xCITY')
z.issue_bond('HDC-2026A', 10_000_000, coupon_pct=4.0, term_years=30, key='0xCITY')
z.subscribe('HDC-2026A', '0xALICE', 10_000_000)
b = z.acquire('1520 Sedgwick Ave', 'bronx', units=100, price=8_000_000,
              monthly_rent=150_000, series='HDC-2026A', key='0xCITY',
              partner='Bronx Community Land Trust')['building']
z.pay_rent(b['id'], '0xTENANT', 1500)      # 750 equity, 750 to the bond
z.pay_coupon('HDC-2026A', key='0xCITY')
z.coverage('HDC-2026A')                    # dscr ≈ 1.56
z.plan()                                   # the published targets and progress so far
```

CLI: `zman <action> key=value ...`. `forward()`'s docstring lists every action.

## Storage

`~/.zman/{program,bonds,buildings}.json`, plus one openhouse store for each
building under `~/.zman/buildings/<id>/`. Pass `Mod(store=dir)` to put all of
it somewhere else; the tests do this.

## Tests

```
cd /root/mod && python3 -m pytest -q mod/orbit/zman/tests
```

## Not built yet

- No API, app or port. The module is Python only for now. When a server is
  added, check `ss -ltn` first, because openhouse had a port collision.
- No on-chain contract. The on-chain version would be one `OpenHouseTrust`
  per building (city = bank + authority) and a bond token in front of them.
- Tenant equity accrues to the building as a whole (toward a limited-equity
  co-op or CLT). openhouse does not yet model per-unit shares.

## Sources

The plan's figures are in `Mod.PLAN`, with links to where they came from:
[NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n) ·
[City & State](https://www.cityandstateny.com/policy/2026/05/5-things-know-about-mamdanis-big-housing-plan/413771/) ·
[ABC7](https://abc7ny.com/post/nyc-mayor-zohran-mamdani-housing-plan-allow-time-rent-hikes-vacant-units-despite-possible-freeze/19173941/)
