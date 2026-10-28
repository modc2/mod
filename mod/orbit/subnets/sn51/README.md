# sn51 — lium.io ת

Agent-first GPU cloud. Rent NVIDIA GPUs via web, CLI, SDK, or API. Providers earn 95% rental revenue + bonuses.

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · [discord](https://discord.gg/lium)

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.110219 | -0.62% | +0.29% | +2.88% | 681,138 | 179,694 | 8,165 |

## Last 24h flow

576 trades by 263 coldkeys · 218 buys (3,827 τ) / 358 sells (3,920 τ) · net -92.77 τ

## News

- 2026-10-19 · commit · [DAH-4195 - \[P2\] Skip could-not-reach rows when the miner reached almo…](https://github.com/Datura-ai/lium-io/commit/46d1c4b88d848daa7ce569bba52e914a119d3ee4) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-4124 - \[P2\] validator: record EXECUTOR_UNREACHABLE_BY_MINER for a…](https://github.com/Datura-ai/lium-io/commit/4ff83f8bb0afa2fa8e730fd742beb234a781e255) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-4547 - \[P0\] Validator: host hardware fingerprint a provider canno…](https://github.com/Datura-ai/lium-io/commit/33671b1e1d630dbae961d9107c9b07d5e17d96ae) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-4551 - \[P0\] Validator: verify each GPU's NVIDIA-signed identity s…](https://github.com/Datura-ai/lium-io/commit/2686e964665f801801321fc4184a3d7d9142c75e) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-4556 - \[P0\] Validator: a node of a banned pool account fails the …](https://github.com/Datura-ai/lium-io/commit/213cdc5429903d49e63a07373e296125a344fb3a) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-2865 - \[P2\] The verified-job reset carries the address its cycle …](https://github.com/Datura-ai/lium-io/commit/e940f770435fcfe621f77c3a24449000d212302b) — Datura-ai/lium-io
- 2026-10-19 · release · [miner-v1.008](https://github.com/Datura-ai/lium-io/releases/tag/miner-v1.008) — Datura-ai/lium-io
- 2026-10-19 · commit · [DAH-4535 - \[P2\] Miner: sign a fresh portal handshake on every reconne…](https://github.com/Datura-ai/lium-io/commit/bddd465655a8a9e2fe941d14c601b655dbe4c35d) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
