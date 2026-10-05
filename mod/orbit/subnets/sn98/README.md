# sn98 — NeverPlayAlone ბ

Living AI Companions that create memories alongside you

Bittensor subnet **98** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/neverplayalone/neverplayalone_subnet) · [url](https://neverplayalone.ai) · [discord](https://discord.com/invite/MG3nkhayjh)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002138 | +0.00% | +0.43% | +0.29% | 10,637 | 4,550 | 15.10 |

## Last 24h flow

12 trades by 6 coldkeys · 5 buys (11.47 τ) / 7 sells (1.84 τ) · net 9.63 τ

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
