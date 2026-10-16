# sn4 — Targon δ

Incentivized Compute Marketplace powered by the Targon Virtual Machine (TVM).

Bittensor subnet **4** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/manifold-inc/targon) · [url](https://targon.com)

Fleet mods for this subnet: `targon`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.051640 | -0.03% | +0.51% | -0.21% | 333,912 | 133,891 | 5,634 |

## Last 24h flow

288 trades by 130 coldkeys · 144 buys (2,781 τ) / 144 sells (2,638 τ) · net 142.36 τ

## News

- 2026-10-16 · commit · [update docs; add amd profile](https://github.com/manifold-inc/targon/commit/c846ea117a395b650e7bb7767eac86251684cf94) — manifold-inc/targon
- 2026-10-01 · commit · [pin mongo version](https://github.com/manifold-inc/targon/commit/4ffeee53baeba1464a6b9ceee1da701a8faf3c39) — manifold-inc/targon
- 2026-09-25 · commit · [update docs](https://github.com/manifold-inc/targon/commit/56a0769547474c56d14272d137ade43e58958a3c) — manifold-inc/targon
- 2026-09-23 · commit · [add hardware profiles](https://github.com/manifold-inc/targon/commit/ee52c6b4171c6cfd952572f9db962de059472076) — manifold-inc/targon
- 2026-09-23 · commit · [restart chain subscription on stale connection](https://github.com/manifold-inc/targon/commit/7b49a678da2a9f318372b70e1e44d42c4c4db7c3) — manifold-inc/targon
- 2026-09-17 · commit · [flip attestation ports prio](https://github.com/manifold-inc/targon/commit/511128aabc6416ae11179306e57c144a906c9894) — manifold-inc/targon
- 2026-09-17 · release · [v2.0.1](https://github.com/manifold-inc/targon/releases/tag/v2.0.1) — manifold-inc/targon

## Use

```bash
m subnets.sn4/info        # live identity + market (snapshot if bt is down)
m subnets.sn4/news        # scraped news
m subnets.sn4/trades      # 24h alpha tape
m subnets.sn4/daily       # daily candles
python3 orbit/subnets/sn4/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
