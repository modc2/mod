# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.101795 | +0.00% | +0.90% | +9.36% | 620,752 | 171,653 | 3,179 |

## Last 24h flow

488 trades by 250 coldkeys · 276 buys (1,598 τ) / 212 sells (1,191 τ) · net 406.94 τ

## News

- 2026-10-09 · commit · [DAH-3980 - Validator: one Docker SDK SSH channel per host, not per UR…](https://github.com/Datura-ai/lium-io/commit/4f9f65ec1779930ab1343204dc3826c0f43cc5e7) — Datura-ai/lium-io
- 2026-10-09 · commit · [DAH-4001 - validator: no settled window keeps the weights in force, i…](https://github.com/Datura-ai/lium-io/commit/579b244a7203ca7d744fa1dc33bd97ef02f21935) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-3769 - Validator: dropped SSH transport becomes a typed error wit…](https://github.com/Datura-ai/lium-io/commit/25ebd36537d2786ff5023e0d451a6c70a83488d6) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-3583 - Pre-pull follow-up: cancel the sweep however the loop ends…](https://github.com/Datura-ai/lium-io/commit/979159ac7b637df7c02a5ed46ddbc47bc10402ae) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-4001 - validator: shadow never delays the live weights, settlemen…](https://github.com/Datura-ai/lium-io/commit/1b82cdd5bd64e6b610758173e456b4380bdd58e4) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-4001 - validator: settled weight submission (#1527)](https://github.com/Datura-ai/lium-io/commit/4b07dcade43ef71bd46ee0612ca1f16488dc3686) — Datura-ai/lium-io
- 2026-10-08 · commit · [validator: remove the failed container before the stale-mount retry (…](https://github.com/Datura-ai/lium-io/commit/cb148c67ca2fef8aa9ea2658df2dfbc7a45fdb82) — Datura-ai/lium-io
- 2026-10-08 · release · [executor-v1.138](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.138) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
