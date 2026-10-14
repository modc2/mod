# sn96 — Verathos ᚛

Verified AI inference and training subnet.

Bittensor subnet **96** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/verathos-ai/verathos) · [url](https://verathos.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004655 | +2.58% | +1.58% | +8.84% | 7,196 | 2,697 | 393.46 |

## Last 24h flow

149 trades by 45 coldkeys · 38 buys (207.28 τ) / 111 sells (184.29 τ) · net 22.99 τ

## News

- 2026-09-25 · release · [Verathos v0.2.3 – DeepSeek Mesh Proof Compatibility](https://github.com/verathos-ai/verathos/releases/tag/v0.2.3) — verathos-ai/verathos
- 2026-09-24 · commit · [fix: release DeepSeek mesh proof compatibility as v0.2.3](https://github.com/verathos-ai/verathos/commit/a859ff97c744eaf4b18761a8c7d9fd42d4a33573) — verathos-ai/verathos
- 2026-09-24 · commit · [fix: place hyper-connection head tensors in the final stage](https://github.com/verathos-ai/verathos/commit/4b969dfb0630b60cfb2e40fdae36851709ac3a12) — verathos-ai/verathos

## Use

```bash
m subnets.sn96/info        # live identity + market (snapshot if bt is down)
m subnets.sn96/news        # scraped news
m subnets.sn96/trades      # 24h alpha tape
m subnets.sn96/daily       # daily candles
python3 orbit/subnets/sn96/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
