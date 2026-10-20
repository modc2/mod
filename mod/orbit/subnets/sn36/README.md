# sn36 — Epago כ

THE OPEN INTELLIGENCE LAYER FOR HUMANS AND AGENTS

Bittensor subnet **36** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/EpagoFoundation/epago) · [url](https://epago.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001057 | -3.50% | -23.68% | -34.73% | 407.6697 | 225.3193 | 106.65 |

## Last 24h flow

46 trades by 19 coldkeys · 16 buys (37.03 τ) / 30 sells (64.23 τ) · net -27.20 τ

## News

- 2026-09-21 · commit · [Merge pull request #12 from EpagoFoundation/staging](https://github.com/EpagoFoundation/epago/commit/4dcb572f2a5a8ef3a174154d97beecf61c6030e9) — EpagoFoundation/epago
- 2026-09-21 · commit · [Merge pull request #13 from EpagoFoundation/feat/transcripts-and-atte…](https://github.com/EpagoFoundation/epago/commit/0a6d6aecdcbbfb5740280c969e9cf7395d758b45) — EpagoFoundation/epago
- 2026-09-21 · commit · [Allow three attempts per hotkey, one model per round, from round 4](https://github.com/EpagoFoundation/epago/commit/e5c4ccbfcc929634e75500236cfd16ed463f234a) — EpagoFoundation/epago
- 2026-09-21 · commit · [Write full episode transcripts when a transcript directory is set](https://github.com/EpagoFoundation/epago/commit/f163c55786d7c59a66f747a91a531049eee453cf) — EpagoFoundation/epago

## Use

```bash
m subnets.sn36/info        # live identity + market (snapshot if bt is down)
m subnets.sn36/news        # scraped news
m subnets.sn36/trades      # 24h alpha tape
m subnets.sn36/daily       # daily candles
python3 orbit/subnets/sn36/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
