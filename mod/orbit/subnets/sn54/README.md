# sn54 — Yanez ت

Yanez SN54 generates synthetic identities for challenging Yanez humanhood, presence, and uniqueness detection models.

Bittensor subnet **54** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/yanez-compliance/MIID-subnet) · [url](https://www.yanez.ai) · [discord](https://discord.com/channels/799672011265015819/1351934165964296232)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006187 | +0.39% | +2.07% | +5.96% | 37,336 | 9,966 | 1,191 |

## Last 24h flow

140 trades by 75 coldkeys · 78 buys (646.20 τ) / 62 sells (543.26 τ) · net 102.95 τ

## News

- 2026-09-29 · blog · [Give Your Agent a Pulse](https://www.yanez.ai/post/give-your-agent-a-pulse) — www.yanez.ai
- 2026-09-25 · commit · [uav -> partner when empty (#114)](https://github.com/yanez-compliance/MIID-subnet/commit/a0218c09a7a9737071aa08cf19e934782196982c) — yanez-compliance/MIID-subnet
- 2026-09-25 · commit · [uav -> partner when empty](https://github.com/yanez-compliance/MIID-subnet/commit/e989f2ee36e9a2336c0d8859cb9c6d7adf305026) — yanez-compliance/MIID-subnet
- 2026-09-24 · commit · [starting of the phase 4 cycle 6 sandbox (#113)](https://github.com/yanez-compliance/MIID-subnet/commit/ef3498f423eb2f2da44ba4708b1a478450451df3) — yanez-compliance/MIID-subnet
- 2026-09-24 · commit · [starting of the sandbox](https://github.com/yanez-compliance/MIID-subnet/commit/b92b18c90e121d52d374cadfe36094468e1aa080) — yanez-compliance/MIID-subnet

## Use

```bash
m subnets.sn54/info        # live identity + market (snapshot if bt is down)
m subnets.sn54/news        # scraped news
m subnets.sn54/trades      # 24h alpha tape
m subnets.sn54/daily       # daily candles
python3 orbit/subnets/sn54/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
