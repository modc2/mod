# sn38 — ChronoLLM ם

Competitive training of chronologically consistent Large Language Models

Bittensor subnet **38** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/chronollm/sn38) · [url](https://chronollm.crunchdao.com/) · [discord](https://discord.com/channels/799672011265015819/1485634202895519844)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021810 | +0.05% | +1.63% | -4.98% | 52,521 | 8,909 | 1,335 |

## Last 24h flow

149 trades by 73 coldkeys · 77 buys (648.09 τ) / 72 sells (607.75 τ) · net 40.33 τ

## News

- 2026-09-22 · commit · [fix: quality duels — async fix, skip private models, cache completion…](https://github.com/chronollm/sn38/commit/a1f1aa75452e4e81aa75e3cafdfc1aa89b5be14f) — chronollm/sn38
- 2026-09-21 · commit · [fix: increase max_new_tokens from 50 to 100 for quality evaluation](https://github.com/chronollm/sn38/commit/261809ce6cd0e48305f0b06f0ef2b084393722d1) — chronollm/sn38
- 2026-09-14 · commit · [Update validator image to the latest version in docker-compose.valida…](https://github.com/chronollm/sn38/commit/01f596b424a01d40ec14db10493d329c0d1c97be) — chronollm/sn38
- 2026-09-14 · commit · [Round 11: instruction following in quality evaluation (#26)](https://github.com/chronollm/sn38/commit/b520da90ad0f6748bfb3982380e757cf60c183da) — chronollm/sn38

## Use

```bash
m subnets.sn38/info        # live identity + market (snapshot if bt is down)
m subnets.sn38/news        # scraped news
m subnets.sn38/trades      # 24h alpha tape
m subnets.sn38/daily       # daily candles
python3 orbit/subnets/sn38/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
