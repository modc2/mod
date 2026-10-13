# sn64 — Chutes ش

Breakthrough Serverless Compute for AI, At Scale.

Bittensor subnet **64** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/chutesai/chutes) · [url](https://chutes.ai) · [discord](https://discord.gg/chutes)

Fleet mods for this subnet: `chutes`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.068440 | +0.01% | -1.18% | -3.11% | 439,742 | 200,849 | 1,979 |

## Last 24h flow

358 trades by 140 coldkeys · 179 buys (122.37 τ) / 179 sells (1,563 τ) · net -1,440 τ

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
