# sn38 — ChronoLLM ם

Competitive training of chronologically consistent Large Language Models

Bittensor subnet **38** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/chronollm/sn38) · [url](https://chronollm.crunchdao.com/) · [discord](https://discord.com/channels/799672011265015819/1485634202895519844)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.019920 | +0.81% | -1.33% | +5.92% | 51,069 | 9,201 | 646.73 |

## Last 24h flow

89 trades by 54 coldkeys · 44 buys (247.58 τ) / 45 sells (339.32 τ) · net -91.74 τ

## News

- 2026-10-07 · commit · [Leak returns to the score at 30%, over a \[-11, -25\] window](https://github.com/chronollm/sn38/commit/f0f5ce21868545b5b305d6f57e0d55e3c69bda08) — chronollm/sn38
- 2026-10-05 · commit · [Round 14: Cache downloaded models and batch the duel generation (#29)](https://github.com/chronollm/sn38/commit/c2f5c9aeafeb473645ca6d83a50fe562ddf1a781) — chronollm/sn38
- 2026-09-22 · commit · [fix: quality duels — async fix, skip private models, cache completion…](https://github.com/chronollm/sn38/commit/a1f1aa75452e4e81aa75e3cafdfc1aa89b5be14f) — chronollm/sn38
- 2026-09-21 · commit · [fix: increase max_new_tokens from 50 to 100 for quality evaluation](https://github.com/chronollm/sn38/commit/261809ce6cd0e48305f0b06f0ef2b084393722d1) — chronollm/sn38

## Use

```bash
m subnets.sn38/info        # live identity + market (snapshot if bt is down)
m subnets.sn38/news        # scraped news
m subnets.sn38/trades      # 24h alpha tape
m subnets.sn38/daily       # daily candles
python3 orbit/subnets/sn38/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
