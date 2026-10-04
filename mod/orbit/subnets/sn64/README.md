# sn64 — Chutes ش

Breakthrough Serverless Compute for AI, At Scale.

Bittensor subnet **64** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/chutesai/chutes) · [url](https://chutes.ai) · [discord](https://discord.gg/chutes)

Fleet mods for this subnet: `chutes`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.071237 | +0.04% | +1.22% | +3.77% | 452,479 | 204,301 | 2,299 |

## Last 24h flow

639 trades by 254 coldkeys · 387 buys (1,484 τ) / 252 sells (526.83 τ) · net 957.17 τ

## News

- 2026-09-15 · blog · [I let Hermes write its own Chutes config](https://chutes.ai/news/open-agent-open-weights-open-enclave) — chutes.ai

## Use

```bash
m subnets.sn64/info        # live identity + market (snapshot if bt is down)
m subnets.sn64/news        # scraped news
m subnets.sn64/trades      # 24h alpha tape
m subnets.sn64/daily       # daily candles
python3 orbit/subnets/sn64/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
