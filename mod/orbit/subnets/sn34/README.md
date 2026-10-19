# sn34 — BitMind י

Deepfake Detection

Bittensor subnet **34** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/BitMind-AI/bitmind-subnet) · [url](https://www.bitmind.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.012102 | — | -0.36% | -0.49% | 71,600 | 35,542 | 120.44 |

## Last 24h flow

76 trades by 28 coldkeys · 4 buys (0.12 τ) / 72 sells (90.64 τ) · net -90.51 τ

## News

- 2026-09-28 · commit · [Merge pull request #468 from BitMind-AI/testnet](https://github.com/BitMind-AI/bitmind-subnet/commit/5fd96727b8f41d8376348f6dd8a11cd5069bce45) — BitMind-AI/bitmind-subnet
- 2026-09-28 · commit · [Bump version to 5.0.10](https://github.com/BitMind-AI/bitmind-subnet/commit/2b095971a1be12d8b9bcc443bd1f01d889e3b0fa) — BitMind-AI/bitmind-subnet
- 2026-09-28 · commit · [Merge pull request #467 from BitMind-AI/feat/generator-performance-ch…](https://github.com/BitMind-AI/bitmind-subnet/commit/3792985282280f2fc2e69ac2668bd97326e5ad9b) — BitMind-AI/bitmind-subnet
- 2026-09-28 · commit · [Keep commit and reveal markers visible on separate event tracks](https://github.com/BitMind-AI/bitmind-subnet/commit/50145fafb46390866efe392d36db1cc8467a6900) — BitMind-AI/bitmind-subnet
- 2026-09-28 · commit · [Show recent chain-verified reveals alongside validator submissions](https://github.com/BitMind-AI/bitmind-subnet/commit/2974b113387e371c340b1ff3034d77218b1c1dc4) — BitMind-AI/bitmind-subnet

## Use

```bash
m subnets.sn34/info        # live identity + market (snapshot if bt is down)
m subnets.sn34/news        # scraped news
m subnets.sn34/trades      # 24h alpha tape
m subnets.sn34/daily       # daily candles
python3 orbit/subnets/sn34/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
