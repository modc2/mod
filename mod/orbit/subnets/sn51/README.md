# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.100182 | +0.14% | +0.19% | +4.46% | 609,270 | 170,086 | 6,996 |

## Last 24h flow

634 trades by 336 coldkeys · 319 buys (3,181 τ) / 315 sells (3,398 τ) · net -216.29 τ

## News

- 2026-10-07 · commit · [DAH-3980 - validator: encrypted volume and renter keys in one exec, r…](https://github.com/Datura-ai/lium-io/commit/a870a244898d7cbd67175b45776967df4218598a) — Datura-ai/lium-io
- 2026-10-07 · commit · [NO-TICKET - [P1] validator: one machine earns under one miner hotkey …](https://github.com/Datura-ai/lium-io/commit/ec31b1ecd9b5f4d594d88d7c1c8ecbfa7cdd7228) — Datura-ai/lium-io
- 2026-10-07 · commit · [DAH-3980 - validator: a customer rent removes the node's filler in on…](https://github.com/Datura-ai/lium-io/commit/03973aef02154bf7b745f2ad4fdc5b420c3f7a41) — Datura-ai/lium-io
- 2026-10-07 · commit · [miner: roll the shared session back when an update, delete or portal …](https://github.com/Datura-ai/lium-io/commit/88042587b93e73ffe24c5520be4b77e6b4f9512b) — Datura-ai/lium-io
- 2026-10-07 · commit · [validator: set a pids limit on rental containers (#1531)](https://github.com/Datura-ai/lium-io/commit/07b09f854822c7cc6df6e1360aa0e741d82e0e62) — Datura-ai/lium-io
- 2026-10-07 · commit · [DAH-3980 - validator: a filler create stands down at docker run while…](https://github.com/Datura-ai/lium-io/commit/6b05b1ba16466bcc812c03827e1768b4acd920c9) — Datura-ai/lium-io
- 2026-10-06 · commit · [DAH-3947 - [P2] lium-io drops celium-collateral; miner reclaims with …](https://github.com/Datura-ai/lium-io/commit/19503aa29dbbd7e88d308cda882458cb0f87ab86) — Datura-ai/lium-io
- 2026-10-06 · commit · [DAH-3980 - validator: start the host probes at SSH connect and restor…](https://github.com/Datura-ai/lium-io/commit/6b91718cb814a6149bfac3e0c0e4fb439063931d) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
