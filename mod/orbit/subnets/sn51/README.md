# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.107135 | +0.01% | +1.85% | +14.43% | 655,945 | 176,414 | 6,480 |

## Last 24h flow

724 trades by 316 coldkeys · 346 buys (3,658 τ) / 378 sells (2,403 τ) · net 1,255 τ

## News

- 2026-10-12 · commit · [DAH-4001 - validator: report each live weight submission with the cyc…](https://github.com/Datura-ai/lium-io/commit/b2623a86260b264d35bbacae0d5576d300f3610e) — Datura-ai/lium-io
- 2026-10-12 · commit · [NO-TICKET - \[P1\] validator: the express lane re-runs a new node's fai…](https://github.com/Datura-ai/lium-io/commit/613f09a953ae75abd8bf15b56d3e4d5e968390d9) — Datura-ai/lium-io
- 2026-10-12 · commit · [validator: strip nvidia-smi underline escapes before parsing the topo…](https://github.com/Datura-ai/lium-io/commit/2fe4856367095ed8aceeb1443c4f0e5213ec5338) — Datura-ai/lium-io
- 2026-10-12 · commit · [DAH-3998 - \[P2\] Signed node heartbeat; validator forwards only a veri…](https://github.com/Datura-ai/lium-io/commit/017e32f01234fb81c1ad2f4557923e58703223b8) — Datura-ai/lium-io
- 2026-10-12 · commit · [NO-TICKET - \[P2\] validator: check the host after a failed pod reboot,…](https://github.com/Datura-ai/lium-io/commit/bf627242561e8eb180c46412b8196364c0bd2a89) — Datura-ai/lium-io
- 2026-10-12 · commit · [validator: add RENTAL_PROBE_ENFORCEMENT_ENABLED shadow mode for the r…](https://github.com/Datura-ai/lium-io/commit/9fe3d7bc973d4764fc15b45d7e48a40586cab5b2) — Datura-ai/lium-io
- 2026-10-12 · commit · [DAH-3980 - Protect customer pods and volumes from filler cleanup (#1540)](https://github.com/Datura-ai/lium-io/commit/b5eb831d7bf062b5f2aa94a4e7d964f5caf584b1) — Datura-ai/lium-io
- 2026-10-12 · release · [executor-v1.139](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.139) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
