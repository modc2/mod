# sn3 — Teutonic γ

Coordinated Learning

Bittensor subnet **3** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/unarbos/teutonic) · [url](https://www.teutonic.ai/) · discord `@unarbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025695 | +0.01% | +0.15% | -7.08% | 160,051 | 76,130 | 2,892 |

## Last 24h flow

145 trades by 69 coldkeys · 75 buys (1,377 τ) / 70 sells (1,410 τ) · net -32.59 τ

## News

- 2026-10-07 · commit · [Refresh stale split baselines after coronation](https://github.com/unarbos/teutonic/commit/3475ddab7411ee695770b6d1af1c9195494fd86b) — unarbos/teutonic
- 2026-10-07 · commit · [Add per-competition error visibility toggles](https://github.com/unarbos/teutonic/commit/7c8b80eeeaeaa7477c9f3f08555f2d6916e42024) — unarbos/teutonic
- 2026-10-07 · commit · [Update delta_threshold values for MATH, CODE, and TEXT splits to 0.002](https://github.com/unarbos/teutonic/commit/f5bfc3f1fba26599b314e7458b36993a2d8a777c) — unarbos/teutonic
- 2026-10-07 · commit · [Add automatic upload credential renewal and latest-generation discovery](https://github.com/unarbos/teutonic/commit/b7b3618c0f3733b1443b968926e538a45f07429e) — unarbos/teutonic
- 2026-10-06 · commit · [Adjust weights for MATH split](https://github.com/unarbos/teutonic/commit/8a804269aecd638acc414b9919b5d9efdb445bf4) — unarbos/teutonic
- 2026-10-06 · commit · [Enforce original-coldkey checkpoint ownership and enable promotions a…](https://github.com/unarbos/teutonic/commit/188ea802426f4394ec93362b1ee554ff7cad3dd6) — unarbos/teutonic
- 2026-10-06 · commit · [Add math, code, and text competitions with gradual reward transition](https://github.com/unarbos/teutonic/commit/de86d5884344bce6b1968d42e5760cd50a38bde4) — unarbos/teutonic
- 2026-10-05 · commit · [Switch evaluator to single-GPU replicas and adjust batch size](https://github.com/unarbos/teutonic/commit/6aa0783a6650451dcf1eaa59bbde1278da98d45a) — unarbos/teutonic

## Use

```bash
m subnets.sn3/info        # live identity + market (snapshot if bt is down)
m subnets.sn3/news        # scraped news
m subnets.sn3/trades      # 24h alpha tape
m subnets.sn3/daily       # daily candles
python3 orbit/subnets/sn3/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
