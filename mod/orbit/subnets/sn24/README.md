# sn24 — Quasar ω

Bittensor subnet built to crush the long-context barrier.

Bittensor subnet **24** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/SILX-LABS/QUASAR-SUBNET/) · [url](https://silxinc.com/) · [discord](https://discordapp.com/channels/799672011265015819/1214246819886931988)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004796 | -0.06% | -3.36% | +4.79% | 29,090 | 9,485 | 347.24 |

## Last 24h flow

33 trades by 25 coldkeys · 5 buys (91.94 τ) / 28 sells (253.14 τ) · net -161.20 τ

## Use

```bash
m subnets.sn24/info        # live identity + market (snapshot if bt is down)
m subnets.sn24/news        # scraped news
m subnets.sn24/trades      # 24h alpha tape
m subnets.sn24/daily       # daily candles
python3 orbit/subnets/sn24/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
