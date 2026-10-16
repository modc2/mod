# sn16 — kenju run next week π

Robot task disruptions and recovery demos for VLA training

Bittensor subnet **16** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/fast-thinker/kenju)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.001037 | -3.95% | +7.66% | +26.65% | 1,007 | 342.7819 | 1,984 |

## Last 24h flow

655 trades by 111 coldkeys · 362 buys (998.53 τ) / 293 sells (984.76 τ) · net 13.77 τ

## News

- 2026-10-15 · commit · [first commit](https://github.com/fast-thinker/kenju/commit/055c22a2a5b4d8e03fa2e07949b7bf52f096a5de) — fast-thinker/kenju

## Use

```bash
m subnets.sn16/info        # live identity + market (snapshot if bt is down)
m subnets.sn16/news        # scraped news
m subnets.sn16/trades      # 24h alpha tape
m subnets.sn16/daily       # daily candles
python3 orbit/subnets/sn16/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
