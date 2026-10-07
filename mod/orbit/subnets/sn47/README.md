# sn47 — GPUForge צ

GPUForge is a Bittensor subnet for verifiable GPU training. Miners execute signed, immutable training workloads on eligible NVIDIA H100 GPUs, while validators verify correctness, freshness, attestation, and useful training throughput before scoring work.

Bittensor subnet **47** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/forgenet47/gpuforge) · [url](https://gpuforge-gpuforge.static.hf.space/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001717 | -0.00% | -0.12% | -15.37% | 3,682 | 989.3230 | 0.63 |

## Last 24h flow

5 trades by 4 coldkeys · 1 buys (0.01 τ) / 4 sells (0.09 τ) · net -0.08 τ

## News

- 2026-09-29 · commit · [Merge pull request #10 from forgenet47/codex/attestation-and-sandbox](https://github.com/forgenet47/gpuforge/commit/7f9e0a7de8157f9147f2b28529e091a5ded51815) — forgenet47/gpuforge
- 2026-09-29 · commit · [feat: bind execution evidence and validate H100 work](https://github.com/forgenet47/gpuforge/commit/114b9b86235c8b9bf8aa95308fdd5e82c92c459c) — forgenet47/gpuforge
- 2026-09-28 · commit · [Merge pull request #9 from forgenet47/codex/attestation-and-sandbox](https://github.com/forgenet47/gpuforge/commit/cf7d1713fdccaf577987b7563201ee5193f11658) — forgenet47/gpuforge
- 2026-09-28 · commit · [feat: verify training work and throughput](https://github.com/forgenet47/gpuforge/commit/5279cf8b5c30d2b9c34c0bbaa19909cc50511bb4) — forgenet47/gpuforge
- 2026-09-26 · commit · [Merge pull request #8 from forgenet47/codex/attestation-and-sandbox](https://github.com/forgenet47/gpuforge/commit/a5631390573bc362ca467048d2df0b3574238400) — forgenet47/gpuforge

## Use

```bash
m subnets.sn47/info        # live identity + market (snapshot if bt is down)
m subnets.sn47/news        # scraped news
m subnets.sn47/trades      # 24h alpha tape
m subnets.sn47/daily       # daily candles
python3 orbit/subnets/sn47/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
