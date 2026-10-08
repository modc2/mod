# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003111 | -0.00% | -0.80% | -4.41% | 16,599 | 5,851 | 89.93 |

## Last 24h flow

22 trades by 18 coldkeys · 8 buys (33.13 τ) / 14 sells (56.11 τ) · net -22.98 τ

## News

- 2026-10-08 · commit · [gitt down waits for the customer; leaving never forfeits pay (#1824)](https://github.com/entrius/gittensor/commit/893123cc46cd8abd24e6bd3db6ddf77531b83c47) — entrius/gittensor
- 2026-10-08 · commit · [Rentals: probe the rent range at admit; a closed range admits idle-on…](https://github.com/entrius/gittensor/commit/43ef5d41d282326532cc032cffa239939d2b5e5f) — entrius/gittensor
- 2026-10-07 · commit · [gitt rent ps: explicit columns, pyright-clean (#1820)](https://github.com/entrius/gittensor/commit/e2d915da454ecd65e22b4a4a9d82ba67f93febc5) — entrius/gittensor
- 2026-10-07 · commit · [Pay: a 48 h holdback; probation is the rental gate (#1818 phase 1) (#…](https://github.com/entrius/gittensor/commit/8a0bd2c13a8bb31f322338eda34273baf543ad29) — entrius/gittensor
- 2026-10-07 · commit · [Validator-only packages become the validator extra (#1822)](https://github.com/entrius/gittensor/commit/a68b6b4da31be5377d10cdc7f1ccae972bc757d9) — entrius/gittensor
- 2026-10-07 · commit · [gitt rent: the customer's CLI for GPU box rentals (#1816)](https://github.com/entrius/gittensor/commit/98b31f6ce008c17201b5737e0ccc51a41f21058a) — entrius/gittensor
- 2026-10-07 · commit · [Rentals: a dark box leaves the market; a customer's bad image is not …](https://github.com/entrius/gittensor/commit/749aa5403f0e3c74d6e252559f2d1ed5cef0c980) — entrius/gittensor
- 2026-10-07 · commit · [Rentals: the order seam poller, and dev overrides for our own test bo…](https://github.com/entrius/gittensor/commit/8f93c1e7c32b2ea5b4c20d2ff0214a4d767d9c55) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
