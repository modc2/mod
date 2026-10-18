# sn96 — Verathos ᚛

Verified AI inference and training subnet.

Bittensor subnet **96** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/verathos-ai/verathos) · [url](https://verathos.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003865 | -0.00% | +2.23% | -5.73% | 6,086 | 2,458 | 67.41 |

## Last 24h flow

33 trades by 18 coldkeys · 7 buys (47.10 τ) / 26 sells (19.35 τ) · net 27.76 τ

## News

- 2026-10-14 · commit · [fix: move the Decide validator handover to block 9,287,280](https://github.com/verathos-ai/verathos/commit/0230a1a835fa7a5a57b9ef5e8e8a5e9d8d9ef8fb) — verathos-ai/verathos
- 2026-10-13 · commit · [fix: schedule Decide validator handover at the approved block](https://github.com/verathos-ai/verathos/commit/762ed1f3b2e0ded0e3633cd4950593bbe3728be2) — verathos-ai/verathos
- 2026-10-13 · commit · [feat: prepare validator handover to Verathos Decide](https://github.com/verathos-ai/verathos/commit/80374607c9b9888e3e30ba22b051c98b18558405) — verathos-ai/verathos
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
