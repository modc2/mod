# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009422 | +0.01% | -2.22% | -9.47% | 53,598 | 28,305 | 867.21 |

## Last 24h flow

225 trades by 86 coldkeys · 156 buys (254.65 τ) / 69 sells (591.83 τ) · net -337.18 τ

## News

- 2026-10-09 · release · [v0.3.10](https://github.com/ridgesai/ridges/releases/tag/v0.3.10) — ridgesai/ridges
- 2026-10-08 · commit · [Merge pull request #520 from ridgesai/feat/dynamic-submission-pricing](https://github.com/ridgesai/ridges/commit/75740b3aaafd714a39e1c25cbd84641152bb6244) — ridgesai/ridges
- 2026-10-08 · commit · [remove quote restriction](https://github.com/ridgesai/ridges/commit/1af4acb58fcdc425a6526b834e441cd192a3c35a) — ridgesai/ridges
- 2026-10-08 · commit · [ruff](https://github.com/ridgesai/ridges/commit/8a61aa002231bf963249424cf5c25075b56c268f) — ridgesai/ridges
- 2026-10-08 · commit · [update + add query for endpoint](https://github.com/ridgesai/ridges/commit/87b4cd732465cb3fc864dcb2c383d28b10a50383) — ridgesai/ridges
- 2026-10-03 · commit · [Merge pull request #518 from ridgesai/update/concurrency-endpoint](https://github.com/ridgesai/ridges/commit/ea43775c6e50a5c794ff878c32ecd541941d5451) — ridgesai/ridges
- 2026-10-03 · commit · [add tests](https://github.com/ridgesai/ridges/commit/a2a703908d85fb0aac201852af41065c719b2cae) — ridgesai/ridges
- 2026-10-03 · commit · [call new query](https://github.com/ridgesai/ridges/commit/3f0b73ae3300d67b0fde91be23ea5c1e2c0aaa61) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
