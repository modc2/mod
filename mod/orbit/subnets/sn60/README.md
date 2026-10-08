# sn60 — Bitsec.ai ذ

find and fix exploits in codebases

Bittensor subnet **60** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/Bitsec-AI/sandbox) · [url](https://bitsec.ai) · discord `yubo`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003928 | -0.00% | -1.13% | -3.51% | 22,907 | 6,913 | 508.70 |

## Last 24h flow

56 trades by 32 coldkeys · 23 buys (234.72 τ) / 33 sells (273.10 τ) · net -38.38 τ

## News

- 2026-09-29 · commit · [[skip ci] increment to build 61](https://github.com/Bitsec-AI/sandbox/commit/3f84cb35d50c112ada0dec22f38b77e36a117790) — Bitsec-AI/sandbox
- 2026-09-29 · commit · [activate sentios](https://github.com/Bitsec-AI/sandbox/commit/cf9acf215a7535109879f84b8b1d72377ef49b12) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [[skip ci] increment to build 60](https://github.com/Bitsec-AI/sandbox/commit/529516b7f03ca70b6377c23eb371e74217224346) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [added endpoint for fetching answers to hidden projects](https://github.com/Bitsec-AI/sandbox/commit/2c87079d1ad8651b4511c54b60053a5cbd0a0ec8) — Bitsec-AI/sandbox
- 2026-09-17 · commit · [[skip ci] increment to build 59](https://github.com/Bitsec-AI/sandbox/commit/80d0293278e0606bc2f74c87be6f082600a97e0c) — Bitsec-AI/sandbox

## Use

```bash
m subnets.sn60/info        # live identity + market (snapshot if bt is down)
m subnets.sn60/news        # scraped news
m subnets.sn60/trades      # 24h alpha tape
m subnets.sn60/daily       # daily candles
python3 orbit/subnets/sn60/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
