# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.108071 | +0.03% | +0.87% | +8.08% | 662,557 | 177,290 | 5,975 |

## Last 24h flow

509 trades by 274 coldkeys · 250 buys (2,985 τ) / 259 sells (2,576 τ) · net 408.13 τ

## News

- 2026-10-14 · commit · [DAH-4035 - \[P0\] Validator: connect to a hostname node only at a check…](https://github.com/Datura-ai/lium-io/commit/30e9a2db1dc5790a4fb63435838cd16be7859437) — Datura-ai/lium-io
- 2026-10-13 · commit · [A create that fails on the node itself keeps the node at score 0 unti…](https://github.com/Datura-ai/lium-io/commit/2c871eafd9f47bf25dabd7cd484dd05bff09eb0e) — Datura-ai/lium-io
- 2026-10-13 · commit · [DAH-4114 - validator: run sleep infinity when a renter image's defaul…](https://github.com/Datura-ai/lium-io/commit/7a2bc654b39e3675ceda6e4136323b1e98aa670c) — Datura-ai/lium-io
- 2026-10-13 · release · [executor-v1.140](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.140) — Datura-ai/lium-io
- 2026-10-13 · commit · [DAH-3980 - Executor: fix Jupyter bootstrap on bare images (#1562)](https://github.com/Datura-ai/lium-io/commit/eb2bd119163228d5163afe636f9990966477101e) — Datura-ai/lium-io
- 2026-10-13 · commit · [DAH-3980 - Validator: install sshd without recommended packages (#1547)](https://github.com/Datura-ai/lium-io/commit/9b9a7a4efea7fa4e5b432d6432da5d4b25436d80) — Datura-ai/lium-io
- 2026-10-12 · commit · [DAH-4001 - validator: report each live weight submission with the cyc…](https://github.com/Datura-ai/lium-io/commit/b2623a86260b264d35bbacae0d5576d300f3610e) — Datura-ai/lium-io
- 2026-10-12 · commit · [NO-TICKET - \[P1\] validator: the express lane re-runs a new node's fai…](https://github.com/Datura-ai/lium-io/commit/613f09a953ae75abd8bf15b56d3e4d5e968390d9) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
