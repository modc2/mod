# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003129 | -0.00% | +0.60% | -2.80% | 16,716 | 5,868 | 56.31 |

## Last 24h flow

43 trades by 22 coldkeys · 28 buys (36.88 τ) / 15 sells (19.09 τ) · net 17.79 τ

## News

- 2026-10-08 · commit · [Compute: the AMD avenue in the controller (vault 31 steps 3 and 4) (#…](https://github.com/entrius/gittensor/commit/e0b7331eee6416450abe926fab41f0133288b655) — entrius/gittensor
- 2026-10-08 · commit · [Compute: the GPU vendor switch scaffold (vault 30 step 0) (#1825)](https://github.com/entrius/gittensor/commit/941f1688fa376c5677c69af15d587d6ad11bf907) — entrius/gittensor
- 2026-10-08 · commit · [Catalog: RTX3090 and RTX4090 as listed entry cards (#1826)](https://github.com/entrius/gittensor/commit/cbc5faad8ca1cf32ccd5d20009486c1f0c5f1259) — entrius/gittensor
- 2026-10-08 · commit · [gitt down waits for the customer; leaving never forfeits pay (#1824)](https://github.com/entrius/gittensor/commit/893123cc46cd8abd24e6bd3db6ddf77531b83c47) — entrius/gittensor
- 2026-10-08 · commit · [Rentals: probe the rent range at admit; a closed range admits idle-on…](https://github.com/entrius/gittensor/commit/43ef5d41d282326532cc032cffa239939d2b5e5f) — entrius/gittensor
- 2026-10-07 · commit · [gitt rent ps: explicit columns, pyright-clean (#1820)](https://github.com/entrius/gittensor/commit/e2d915da454ecd65e22b4a4a9d82ba67f93febc5) — entrius/gittensor
- 2026-10-07 · commit · [Pay: a 48 h holdback; probation is the rental gate (#1818 phase 1) (#…](https://github.com/entrius/gittensor/commit/8a0bd2c13a8bb31f322338eda34273baf543ad29) — entrius/gittensor
- 2026-10-07 · commit · [Validator-only packages become the validator extra (#1822)](https://github.com/entrius/gittensor/commit/a68b6b4da31be5377d10cdc7f1ccae972bc757d9) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
