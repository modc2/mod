# sn45 — AlphaRidge.ai פ

Real-time market intelligence across equities, FX, crypto, commodities and indices. AlphaRidge reads the conversations of 1,000+ news sources, X posts, Telegram feeds, scoring every post into structured signals: sentiment, impact, market outlook and more. See the why behind every move, screen any market, and get alerted the moment sentiment turns.

Bittensor subnet **45** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/Team-Rizzo/alpharidge-ai) · [url](https://alpharidge.ai) · discord `canti_dev`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002360 | -0.08% | -2.50% | -4.39% | 13,972 | 6,156 | 203.87 |

## Last 24h flow

307 trades by 24 coldkeys · 11 buys (61.14 τ) / 296 sells (140.78 τ) · net -79.65 τ

## News

- 2026-10-15 · commit · [3.9.14: skip canaries in the overrule check](https://github.com/Team-Rizzo/alpharidge-ai/commit/a0ab38ad7fcddfb1d6e4ae6a2903bf1516eaa83f) — Team-Rizzo/alpharidge-ai
- 2026-10-15 · commit · [3.9.14: published check overrules gazetteer false negatives](https://github.com/Team-Rizzo/alpharidge-ai/commit/8d6faa69e8d53712eee4bb647e8805e973cbe18a) — Team-Rizzo/alpharidge-ai
- 2026-10-13 · commit · [3.9.13: tidy comments](https://github.com/Team-Rizzo/alpharidge-ai/commit/9292a03c2e754f9eac72b938cde6db9144234912) — Team-Rizzo/alpharidge-ai
- 2026-10-13 · commit · [soak report: read rotated validator logs](https://github.com/Team-Rizzo/alpharidge-ai/commit/0eb7589dbdf666cd93f6bf82503c8cf9a02be96f) — Team-Rizzo/alpharidge-ai
- 2026-10-13 · commit · [3.9.13: judge calls in LLM spend, soak report script](https://github.com/Team-Rizzo/alpharidge-ai/commit/bb4bfed1180696801c69116e3a37f6497d4ca22a) — Team-Rizzo/alpharidge-ai
- 2026-10-13 · commit · [3.9.13: shadow on by default](https://github.com/Team-Rizzo/alpharidge-ai/commit/04260241c4b54616e9395ac89636a1bdab176810) — Team-Rizzo/alpharidge-ai
- 2026-10-13 · commit · [3.9.13: record judged ids](https://github.com/Team-Rizzo/alpharidge-ai/commit/b966589bc8aa3490c43275b1cf5fef1a95bfbb0a) — Team-Rizzo/alpharidge-ai
- 2026-10-06 · commit · [3.9.12: clean no-reference on an empty error body](https://github.com/Team-Rizzo/alpharidge-ai/commit/9f9e0144b42782a4e063d6e2e84e51392ad58c98) — Team-Rizzo/alpharidge-ai

## Use

```bash
m subnets.sn45/info        # live identity + market (snapshot if bt is down)
m subnets.sn45/news        # scraped news
m subnets.sn45/trades      # 24h alpha tape
m subnets.sn45/daily       # daily candles
python3 orbit/subnets/sn45/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
