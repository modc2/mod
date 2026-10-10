# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003121 | -0.00% | -0.27% | -2.26% | 16,689 | 5,861 | 198.32 |

## Last 24h flow

28 trades by 15 coldkeys · 12 buys (95.21 τ) / 16 sells (102.72 τ) · net -7.51 τ

## News

- 2026-10-09 · commit · [Interconnect: nvidia-smi topo -m wraps its header in terminal codes; …](https://github.com/entrius/gittensor/commit/aae77d4ea86ab788eb672b217cbb60f8aa3c7ec8) — entrius/gittensor
- 2026-10-09 · commit · [Host specs per box: measure every round, floor download, show the cus…](https://github.com/entrius/gittensor/commit/3654abffb738687619bd7b962604f0d5f5c1f0f1) — entrius/gittensor
- 2026-10-09 · commit · [NVIDIA final run: AMD iGPU is not a mixed box; gitt down waits for th…](https://github.com/entrius/gittensor/commit/01f2c230e22b1e1cc3042bfb01b91c18b2598dd8) — entrius/gittensor
- 2026-10-09 · commit · [AMD: MI300X qualified on the MI325X digest (vault 33 section 10) (#1833)](https://github.com/entrius/gittensor/commit/f761d1f00f87acd00d1a6e3a17e0581c9174a887) — entrius/gittensor
- 2026-10-09 · commit · [Rentals: every start/stop outcome reaches the log; gitt rent reasons …](https://github.com/entrius/gittensor/commit/914fe46bd1ca871e56b36416bf511339c09425c5) — entrius/gittensor
- 2026-10-09 · commit · [Miner-facing messages: gitt up rows say what to fix; bench phrases ca…](https://github.com/entrius/gittensor/commit/4c8eb98b953c9596d08c67602d34c164178020b5) — entrius/gittensor
- 2026-10-09 · commit · [Controller log: rental thread results, typed rental events, at_iso, e…](https://github.com/entrius/gittensor/commit/d506c45a8f6ffd7975af1800a50029dc2c5274c6) — entrius/gittensor
- 2026-10-09 · commit · [Compute: build and publish entrius/gt-proof-rocm in agent-images (vau…](https://github.com/entrius/gittensor/commit/8dce88f76cbcfb2faa786682701bfa2130bf37e4) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
