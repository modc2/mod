# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.100881 | -0.16% | +0.70% | +8.10% | 614,349 | 170,779 | 5,753 |

## Last 24h flow

539 trades by 243 coldkeys · 277 buys (2,802 τ) / 262 sells (2,566 τ) · net 235.25 τ

## News

- 2026-10-08 · commit · [DAH-3769 - Validator: dropped SSH transport becomes a typed error wit…](https://github.com/Datura-ai/lium-io/commit/25ebd36537d2786ff5023e0d451a6c70a83488d6) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-3583 - Pre-pull follow-up: cancel the sweep however the loop ends…](https://github.com/Datura-ai/lium-io/commit/979159ac7b637df7c02a5ed46ddbc47bc10402ae) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-4001 - validator: shadow never delays the live weights, settlemen…](https://github.com/Datura-ai/lium-io/commit/1b82cdd5bd64e6b610758173e456b4380bdd58e4) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-4001 - validator: settled weight submission (#1527)](https://github.com/Datura-ai/lium-io/commit/4b07dcade43ef71bd46ee0612ca1f16488dc3686) — Datura-ai/lium-io
- 2026-10-08 · commit · [validator: remove the failed container before the stale-mount retry (…](https://github.com/Datura-ai/lium-io/commit/cb148c67ca2fef8aa9ea2658df2dfbc7a45fdb82) — Datura-ai/lium-io
- 2026-10-08 · release · [executor-v1.138](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.138) — Datura-ai/lium-io
- 2026-10-08 · commit · [executor: reserve CPU/RAM and OOM-protect it so a renter's full load …](https://github.com/Datura-ai/lium-io/commit/173e11d2b8c0c0245e4b6f9f2bb5c96ca1ace657) — Datura-ai/lium-io
- 2026-10-08 · commit · [DAH-3980 - validator: a customer rent starts the filler's removal as …](https://github.com/Datura-ai/lium-io/commit/1f37562e7b9cb645cfcd8392d45761abb51af967) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
