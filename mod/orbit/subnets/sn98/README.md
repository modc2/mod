# sn98 — NeverPlayAlone ბ

Living AI Companions that create memories alongside you

Bittensor subnet **98** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/neverplayalone/neverplayalone_subnet) · [url](https://neverplayalone.ai) · [discord](https://discord.com/invite/MG3nkhayjh)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002151 | +0.00% | -0.02% | +1.13% | 10,749 | 4,565 | 1.20 |

## Last 24h flow

3 trades by 2 coldkeys · 2 buys (0.02 τ) / 1 sells (0.44 τ) · net -0.41 τ

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
