# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.099988 | +0.48% | +6.80% | +5.31% | 607,276 | 169,822 | 13,816 |

## Last 24h flow

540 trades by 266 coldkeys · 230 buys (9,301 τ) / 310 sells (4,142 τ) · net 5,159 τ

## News

- 2026-10-07 · commit · [DAH-3980 - validator: a filler create stands down at docker run while…](https://github.com/Datura-ai/lium-io/commit/6b05b1ba16466bcc812c03827e1768b4acd920c9) — Datura-ai/lium-io
- 2026-10-06 · commit · [DAH-3947 - [P2] lium-io drops celium-collateral; miner reclaims with …](https://github.com/Datura-ai/lium-io/commit/19503aa29dbbd7e88d308cda882458cb0f87ab86) — Datura-ai/lium-io
- 2026-10-06 · commit · [DAH-3980 - validator: start the host probes at SSH connect and restor…](https://github.com/Datura-ai/lium-io/commit/6b91718cb814a6149bfac3e0c0e4fb439063931d) — Datura-ai/lium-io
- 2026-10-06 · commit · [DAH-3775 - [P2] validator: pod killed between docker run and bootstra…](https://github.com/Datura-ai/lium-io/commit/dff7b6152fb9ebd982bfdfbaef58cd06e4f48282) — Datura-ai/lium-io
- 2026-10-05 · release · [executor-v1.137](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.137) — Datura-ai/lium-io
- 2026-10-05 · commit · [NO-TICKET - [P1] verifyx: vendor libverifyx.so from celium-gpu-verifi…](https://github.com/Datura-ai/lium-io/commit/2b4dc97bf014e9a3944420b72c2a4a8b5c3c0d49) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3980 - validator: connector reads the chain off its event loop an…](https://github.com/Datura-ai/lium-io/commit/2535a5af297a53ab038af4a46c52b162367f8a44) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3980 - validator: port mapping works on a copy of the preferred p…](https://github.com/Datura-ai/lium-io/commit/c2b652889cb202e3d00b13ba5367a05c51b9abf2) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
