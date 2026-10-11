# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003097 | +0.11% | -0.76% | -2.10% | 16,579 | 5,838 | 35.15 |

## Last 24h flow

31 trades by 22 coldkeys · 8 buys (6.37 τ) / 23 sells (28.52 τ) · net -22.15 τ

## News

- 2026-10-10 · commit · [Box listing: market status per box, the hotkey pin, box_busy, gitt re…](https://github.com/entrius/gittensor/commit/75e0f2ee3a8008b4dba3755629fd5db1fb7bac1e) — entrius/gittensor
- 2026-10-10 · commit · [Compute: upload advertised, the rental heartbeat, 7 days in the contr…](https://github.com/entrius/gittensor/commit/e0d23ccd929376786238c64f8d00a28bb1d91a96) — entrius/gittensor
- 2026-10-09 · commit · [Interconnect: nvidia-smi topo -m wraps its header in terminal codes; …](https://github.com/entrius/gittensor/commit/aae77d4ea86ab788eb672b217cbb60f8aa3c7ec8) — entrius/gittensor
- 2026-10-09 · commit · [Host specs per box: measure every round, floor download, show the cus…](https://github.com/entrius/gittensor/commit/3654abffb738687619bd7b962604f0d5f5c1f0f1) — entrius/gittensor
- 2026-10-09 · commit · [NVIDIA final run: AMD iGPU is not a mixed box; gitt down waits for th…](https://github.com/entrius/gittensor/commit/01f2c230e22b1e1cc3042bfb01b91c18b2598dd8) — entrius/gittensor
- 2026-10-09 · commit · [AMD: MI300X qualified on the MI325X digest (vault 33 section 10) (#1833)](https://github.com/entrius/gittensor/commit/f761d1f00f87acd00d1a6e3a17e0581c9174a887) — entrius/gittensor
- 2026-10-09 · commit · [Rentals: every start/stop outcome reaches the log; gitt rent reasons …](https://github.com/entrius/gittensor/commit/914fe46bd1ca871e56b36416bf511339c09425c5) — entrius/gittensor
- 2026-10-09 · commit · [Miner-facing messages: gitt up rows say what to fix; bench phrases ca…](https://github.com/entrius/gittensor/commit/4c8eb98b953c9596d08c67602d34c164178020b5) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
