# sn80 — OpenRoboto ى

An open competition on Bittensor for continuously improving robotics models and collecting egocentric data

Bittensor subnet **80** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/openroboto-ai/openroboto-subnet) · [url](https://www.openroboto.ai/) · [discord](https://discord.gg/N4F7UhEBY)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.024631 | +0.02% | -5.10% | -4.08% | 60,380 | 5,332 | 4,779 |

## Last 24h flow

656 trades by 172 coldkeys · 409 buys (2,239 τ) / 247 sells (2,437 τ) · net -198.01 τ

## News

- 2026-09-22 · news · [OpenRoboto launches Shift, a platform paying us...](https://news.google.com/rss/articles/CBMirwFBVV95cUxONDgzQTE0SmdHVlYybFBTcDE2Y2ViR1lUQ2VVSEE1Z3lKa3ZaNVg0aEVDaEt6UVVDb01yU0lBMDI1QlJONlJOSloxM0dZdW1EQ3ZXU0Y4eFVQMTB1R1Q0eExDRHE0NEhuTkp6ZExPUHFWVnNrcVF2SWtMM1VTeXBidUNHUjc5MDc0VEZldUhPWnJES01Zajd0Tjk2YlRSWmhuUFJZa3h5RmQ1WkNPVWI4?oc=5) — Pluang
- 2026-09-08 · commit · [docs: link shared real-robot task catalog and training data](https://github.com/openroboto-ai/openroboto-subnet/commit/726e42aea3a8901dc2f431ab8d17c7d5c46594a4) — openroboto-ai/openroboto-subnet
- 2026-09-07 · commit · [docs: update active emission allocation across all three tracks](https://github.com/openroboto-ai/openroboto-subnet/commit/a2754c33f477f1402e4babeee763c6ac13eb8d35) — openroboto-ai/openroboto-subnet
- 2026-09-07 · commit · [docs: show workstation camera-view reference](https://github.com/openroboto-ai/openroboto-subnet/commit/1d57c5640ea41a1d2d7ef2c4b2c01b27f2cc8857) — openroboto-ai/openroboto-subnet
- 2026-09-07 · commit · [docs: remove translation link and unfinished interface notices](https://github.com/openroboto-ai/openroboto-subnet/commit/0da4a5d30e88f6c273fe10c1d2b983aecb11fbe9) — openroboto-ai/openroboto-subnet
- 2026-09-07 · commit · [docs: reference confirmed workstation interface and unresolved hardwa…](https://github.com/openroboto-ai/openroboto-subnet/commit/cf0092f8e7e45e09d2ea5b19ea4457276bc94ebd) — openroboto-ai/openroboto-subnet

## Use

```bash
m subnets.sn80/info        # live identity + market (snapshot if bt is down)
m subnets.sn80/news        # scraped news
m subnets.sn80/trades      # 24h alpha tape
m subnets.sn80/daily       # daily candles
python3 orbit/subnets/sn80/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
