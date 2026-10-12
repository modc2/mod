# sn64 — Chutes ش

Breakthrough Serverless Compute for AI, At Scale.

Bittensor subnet **64** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/chutesai/chutes) · [url](https://chutes.ai) · [discord](https://discord.gg/chutes)

Fleet mods for this subnet: `chutes`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.069260 | -0.02% | -0.49% | -3.26% | 444,445 | 201,982 | 1,349 |

## Last 24h flow

266 trades by 122 coldkeys · 92 buys (170.63 τ) / 174 sells (918.79 τ) · net -748.16 τ

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
