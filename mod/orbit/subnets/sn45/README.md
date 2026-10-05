# sn45 — AlphaRidge.ai פ

Real-time market intelligence across equities, FX, crypto, commodities and indices. AlphaRidge reads the conversations of 1,000+ news sources, X posts, Telegram feeds, scoring every post into structured signals: sentiment, impact, market outlook and more. See the why behind every move, screen any market, and get alerted the moment sentiment turns.

Bittensor subnet **45** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/Team-Rizzo/alpharidge-ai) · [url](https://alpharidge.ai) · discord `canti_dev`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002453 | -0.00% | -0.68% | +2.63% | 14,360 | 6,276 | 28.21 |

## Last 24h flow

233 trades by 13 coldkeys · 1 buys (3.00 τ) / 232 sells (23.87 τ) · net -20.87 τ

## News

- 2026-10-03 · commit · [3.9.11](https://github.com/Team-Rizzo/alpharidge-ai/commit/b347edb4ec5cdc9af908bd17da8555a9e15e1033) — Team-Rizzo/alpharidge-ai
- 2026-10-01 · commit · [3.9.10](https://github.com/Team-Rizzo/alpharidge-ai/commit/cfcb64a6e637fb569506ccf74b86e2c3d332b5be) — Team-Rizzo/alpharidge-ai
- 2026-10-01 · commit · [3.9.9](https://github.com/Team-Rizzo/alpharidge-ai/commit/6feaaaa5394480848e0c04bc8176061c548fbbc9) — Team-Rizzo/alpharidge-ai
- 2026-09-30 · commit · [3.9.8](https://github.com/Team-Rizzo/alpharidge-ai/commit/f61d16bbfa3d190478f72543f1138a647afb6ab5) — Team-Rizzo/alpharidge-ai
- 2026-09-30 · commit · [3.9.7](https://github.com/Team-Rizzo/alpharidge-ai/commit/de26286aa0cb954c7e0da9e39aaee7307f4d0eea) — Team-Rizzo/alpharidge-ai
- 2026-09-29 · commit · [3.9.5](https://github.com/Team-Rizzo/alpharidge-ai/commit/4d412ddbdd128db5c92b287b4c05926b78cb2d8f) — Team-Rizzo/alpharidge-ai
- 2026-09-28 · commit · [Skip a validation sample when the validator's own reference call retu…](https://github.com/Team-Rizzo/alpharidge-ai/commit/a0c8270e3907774dad244edc78cb1c011906379c) — Team-Rizzo/alpharidge-ai
- 2026-09-24 · commit · [Score the stock anchor on a sample of audits](https://github.com/Team-Rizzo/alpharidge-ai/commit/7da3240be60a244631ef1bbf843c827fff5f33a7) — Team-Rizzo/alpharidge-ai

## Use

```bash
m subnets.sn45/info        # live identity + market (snapshot if bt is down)
m subnets.sn45/news        # scraped news
m subnets.sn45/trades      # 24h alpha tape
m subnets.sn45/daily       # daily candles
python3 orbit/subnets/sn45/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
