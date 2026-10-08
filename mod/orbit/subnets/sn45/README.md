# sn45 — AlphaRidge.ai פ

Real-time market intelligence across equities, FX, crypto, commodities and indices. AlphaRidge reads the conversations of 1,000+ news sources, X posts, Telegram feeds, scoring every post into structured signals: sentiment, impact, market outlook and more. See the why behind every move, screen any market, and get alerted the moment sentiment turns.

Bittensor subnet **45** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/Team-Rizzo/alpharidge-ai) · [url](https://alpharidge.ai) · discord `canti_dev`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002468 | -0.00% | +0.93% | -5.10% | 14,500 | 6,296 | 34.87 |

## Last 24h flow

94 trades by 11 coldkeys · 4 buys (31.24 τ) / 90 sells (2.26 τ) · net 28.98 τ

## News

- 2026-10-06 · commit · [3.9.12: clean no-reference on an empty error body](https://github.com/Team-Rizzo/alpharidge-ai/commit/9f9e0144b42782a4e063d6e2e84e51392ad58c98) — Team-Rizzo/alpharidge-ai
- 2026-10-05 · commit · [3.9.12: replacement fixes](https://github.com/Team-Rizzo/alpharidge-ai/commit/a2b1adb8a0594b048088135e92b662bf0f0b1093) — Team-Rizzo/alpharidge-ai
- 2026-10-05 · commit · [3.9.12](https://github.com/Team-Rizzo/alpharidge-ai/commit/c2e0c26b4eb81540b081d2294515ace3ba59526c) — Team-Rizzo/alpharidge-ai
- 2026-10-03 · commit · [3.9.11](https://github.com/Team-Rizzo/alpharidge-ai/commit/b347edb4ec5cdc9af908bd17da8555a9e15e1033) — Team-Rizzo/alpharidge-ai
- 2026-10-01 · commit · [3.9.10](https://github.com/Team-Rizzo/alpharidge-ai/commit/cfcb64a6e637fb569506ccf74b86e2c3d332b5be) — Team-Rizzo/alpharidge-ai
- 2026-10-01 · commit · [3.9.9](https://github.com/Team-Rizzo/alpharidge-ai/commit/6feaaaa5394480848e0c04bc8176061c548fbbc9) — Team-Rizzo/alpharidge-ai
- 2026-09-30 · commit · [3.9.8](https://github.com/Team-Rizzo/alpharidge-ai/commit/f61d16bbfa3d190478f72543f1138a647afb6ab5) — Team-Rizzo/alpharidge-ai
- 2026-09-30 · commit · [3.9.7](https://github.com/Team-Rizzo/alpharidge-ai/commit/de26286aa0cb954c7e0da9e39aaee7307f4d0eea) — Team-Rizzo/alpharidge-ai

## Use

```bash
m subnets.sn45/info        # live identity + market (snapshot if bt is down)
m subnets.sn45/news        # scraped news
m subnets.sn45/trades      # 24h alpha tape
m subnets.sn45/daily       # daily candles
python3 orbit/subnets/sn45/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
