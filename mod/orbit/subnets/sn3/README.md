# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025663 | +0.01% | -0.13% | +0.62% | 160,058 | 76,107 | 711.12 |

## Last 24h flow

80 trades by 49 coldkeys · 39 buys (238.28 τ) / 41 sells (374.90 τ) · net -136.62 τ

## News

- 2026-10-08 · commit · [Add competition dataset panel and update evaluation history layout](https://github.com/unarbos/teutonic/commit/25c64ca4857015c01f10257eac609d2fc8389fe5) — unarbos/teutonic
- 2026-10-08 · commit · [Fix evaluator replica race in Transformers module cache](https://github.com/unarbos/teutonic/commit/803af9c332f70a0259921a6e45a3767abc0abc6b) — unarbos/teutonic
- 2026-10-07 · commit · [Refresh stale split baselines after coronation](https://github.com/unarbos/teutonic/commit/3475ddab7411ee695770b6d1af1c9195494fd86b) — unarbos/teutonic
- 2026-10-07 · commit · [Add per-competition error visibility toggles](https://github.com/unarbos/teutonic/commit/7c8b80eeeaeaa7477c9f3f08555f2d6916e42024) — unarbos/teutonic
- 2026-10-07 · commit · [Update delta_threshold values for MATH, CODE, and TEXT splits to 0.002](https://github.com/unarbos/teutonic/commit/f5bfc3f1fba26599b314e7458b36993a2d8a777c) — unarbos/teutonic
- 2026-10-07 · commit · [Add automatic upload credential renewal and latest-generation discovery](https://github.com/unarbos/teutonic/commit/b7b3618c0f3733b1443b968926e538a45f07429e) — unarbos/teutonic
- 2026-10-06 · commit · [Adjust weights for MATH split](https://github.com/unarbos/teutonic/commit/8a804269aecd638acc414b9919b5d9efdb445bf4) — unarbos/teutonic
- 2026-10-06 · commit · [Enforce original-coldkey checkpoint ownership and enable promotions a…](https://github.com/unarbos/teutonic/commit/188ea802426f4394ec93362b1ee554ff7cad3dd6) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
