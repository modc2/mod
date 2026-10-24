# sn12 — Compute Horde μ

Trusted compute for bittensor validators

Bittensor subnet **12** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/backend-developers-ltd/ComputeHorde/) · [url](https://computehorde.io) · [discord](https://discordapp.com/channels/799672011265015819/1201941624243109888)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004778 | -0.00% | -0.08% | -3.03% | 30,870 | 12,658 | 4.99 |

## Last 24h flow

4 trades by 4 coldkeys · 1 buys (0.04 τ) / 3 sells (3.51 τ) · net -3.48 τ

## News

- 2026-10-18 · news · [Compute Horde USD (SN12-USD) Cryptocurrency Profile & Facts](https://news.google.com/rss/articles/CBMiYkFVX3lxTFBfZ0lfREFpUWxCZXR6QUd1aEhyLU9QRFRrNUdyeEI4dUdCcWJxOFQ2ZUF4Z3ktQmQtemRrN2VjTU02MHVfSFNISkl0LUZyeVdKYmdRbHpmaGstZlVncFlxR0tB?oc=5) — Yahoo! Finance Canada

## Use

```bash
m subnets.sn12/info        # live identity + market (snapshot if bt is down)
m subnets.sn12/news        # scraped news
m subnets.sn12/trades      # 24h alpha tape
m subnets.sn12/daily       # daily candles
python3 orbit/subnets/sn12/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
