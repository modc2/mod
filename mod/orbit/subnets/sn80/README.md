# sn80 — OpenRoboto ى

An open competition on Bittensor for continuously improving robotics models and collecting egocentric data

Bittensor subnet **80** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/openroboto-ai/openroboto-subnet) · [url](https://www.openroboto.ai/) · [discord](https://discord.gg/N4F7UhEBY)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.023673 | -1.60% | -1.48% | -9.35% | 58,907 | 5,420 | 3,308 |

## Last 24h flow

591 trades by 167 coldkeys · 359 buys (1,579 τ) / 232 sells (1,685 τ) · net -106.63 τ

## News

- 2026-10-05 · commit · [docs: synchronize allocation rules and simulation fee](https://github.com/openroboto-ai/openroboto-subnet/commit/822e3b668c05ff5cfa2328ed2ff6b4a6cd5dfc70) — openroboto-ai/openroboto-subnet
- 2026-09-22 · news · [OpenRoboto launches Shift, a platform paying us...](https://news.google.com/rss/articles/CBMirwFBVV95cUxONDgzQTE0SmdHVlYybFBTcDE2Y2ViR1lUQ2VVSEE1Z3lKa3ZaNVg0aEVDaEt6UVVDb01yU0lBMDI1QlJONlJOSloxM0dZdW1EQ3ZXU0Y4eFVQMTB1R1Q0eExDRHE0NEhuTkp6ZExPUHFWVnNrcVF2SWtMM1VTeXBidUNHUjc5MDc0VEZldUhPWnJES01Zajd0Tjk2YlRSWmhuUFJZa3h5RmQ1WkNPVWI4?oc=5) — Pluang

## Use

```bash
m subnets.sn80/info        # live identity + market (snapshot if bt is down)
m subnets.sn80/news        # scraped news
m subnets.sn80/trades      # 24h alpha tape
m subnets.sn80/daily       # daily candles
python3 orbit/subnets/sn80/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
