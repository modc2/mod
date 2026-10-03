# sn79 — MVTRX ي

Building a SOTA Exchange for dTAO and Beyond

Bittensor subnet **79** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/taos-im/sn-79) · [url](https://taos.im)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003096 | -0.01% | -0.03% | +1.35% | 16,271 | 6,989 | 121.89 |

## Last 24h flow

153 trades by 26 coldkeys · 5 buys (56.35 τ) / 148 sells (61.10 τ) · net -4.75 τ

## News

- 2026-09-29 · commit · [20260929 - 0.6.2 pool pay vector and slot reset](https://github.com/taos-im/sn-79/commit/47932d4c7bca8403f9a2942903d153a39caa7f5f) — taos-im/sn-79
- 2026-09-28 · commit · [20260928 - 0.6.2 skill floor basis](https://github.com/taos-im/sn-79/commit/77fcb3098e6f6da059f4c9d4368ff5046a4357c4) — taos-im/sn-79
- 2026-09-25 · commit · [20260925 - 0.6.2 making pool](https://github.com/taos-im/sn-79/commit/c05768ce2740eb3d9c7f09f41be5198208820fcd) — taos-im/sn-79
- 2026-09-23 · commit · [20260923 - 0.6.2](https://github.com/taos-im/sn-79/commit/2564fa5bb6a1b62cb95f3af546b09080a0577819) — taos-im/sn-79
- 2026-09-21 · commit · [20260921 - 0.6.1 final rung](https://github.com/taos-im/sn-79/commit/7a3cad796062a3279e16f7ee9d3b7d48b201ac88) — taos-im/sn-79

## Use

```bash
m subnets.sn79/info        # live identity + market (snapshot if bt is down)
m subnets.sn79/news        # scraped news
m subnets.sn79/trades      # 24h alpha tape
m subnets.sn79/daily       # daily candles
python3 orbit/subnets/sn79/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
