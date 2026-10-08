# sn24 — Quasar ω

Bittensor subnet built to crush the long-context barrier.

Bittensor subnet **24** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/SILX-LABS/QUASAR-SUBNET/) · [url](https://silxinc.com/) · [discord](https://discordapp.com/channels/799672011265015819/1214246819886931988)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005102 | -0.02% | -0.28% | +2.60% | 31,055 | 9,783 | 191.24 |

## Last 24h flow

53 trades by 17 coldkeys · 21 buys (88.78 τ) / 32 sells (101.59 τ) · net -12.80 τ

## Use

```bash
m subnets.sn24/info        # live identity + market (snapshot if bt is down)
m subnets.sn24/news        # scraped news
m subnets.sn24/trades      # 24h alpha tape
m subnets.sn24/daily       # daily candles
python3 orbit/subnets/sn24/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
