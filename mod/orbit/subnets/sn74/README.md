# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003163 | +0.00% | -0.94% | -2.44% | 16,807 | 5,900 | 28.16 |

## Last 24h flow

18 trades by 16 coldkeys · 1 buys (0.00 τ) / 17 sells (27.69 τ) · net -27.69 τ

## News

- 2026-10-02 · commit · [Remove compute (#1803)](https://github.com/entrius/gittensor/commit/c0d9dc8ddd7e4a0f95b8028df4ca4f52e8d7f4ca) — entrius/gittensor
- 2026-10-02 · release · [release-20261002-144641](https://github.com/entrius/gittensor/releases/tag/release-20261002-144641) — entrius/gittensor
- 2026-10-01 · commit · [chore(weights): slow sparkinfer's time decay to a 7-day midpoint with…](https://github.com/entrius/gittensor/commit/ba87ddec7db7410df2fa94737e649ce029447859) — entrius/gittensor
- 2026-10-01 · release · [release-20261001-004737](https://github.com/entrius/gittensor/releases/tag/release-20261001-004737) — entrius/gittensor
- 2026-09-30 · commit · [Raise spark-hermes emission share to 0.20 (#1802)](https://github.com/entrius/gittensor/commit/5cdf813cdec7096d4b22df290630c8dc0cf050ae) — entrius/gittensor
- 2026-09-30 · release · [release-20260930-235444](https://github.com/entrius/gittensor/releases/tag/release-20260930-235444) — entrius/gittensor
- 2026-09-25 · commit · [registry: bless qwen3.8-27b-nvfp4@8 (entrius/qwen3.8-27b-nvfp4:8@sha2…](https://github.com/entrius/gittensor/commit/e850db5b29d1b034fd82c877ad01cd76e12cce5e) — entrius/gittensor
- 2026-09-25 · release · [release-20260925-183535](https://github.com/entrius/gittensor/releases/tag/release-20260925-183535) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
