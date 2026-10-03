# sn98 — NeverPlayAlone ბ

Living AI Companions that create memories alongside you

Bittensor subnet **98** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/neverplayalone/neverplayalone_subnet) · [url](https://neverplayalone.ai) · [discord](https://discord.com/invite/MG3nkhayjh)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002128 | +0.00% | +0.08% | -0.23% | 10,559 | 4,540 | 2.07 |

## Last 24h flow

5 trades by 3 coldkeys · 2 buys (1.48 τ) / 3 sells (0.02 τ) · net 1.46 τ

## News

- 2026-09-09 · commit · [feat: retry transient artifact uploads and skip failed entries instea…](https://github.com/neverplayalone/neverplayalone_subnet/commit/0d43c5ace7cbbf763199ac3a1a8f2a3324f2776b) — neverplayalone/neverplayalone_subnet

## Use

```bash
m subnets.sn98/info        # live identity + market (snapshot if bt is down)
m subnets.sn98/news        # scraped news
m subnets.sn98/trades      # 24h alpha tape
m subnets.sn98/daily       # daily candles
python3 orbit/subnets/sn98/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
