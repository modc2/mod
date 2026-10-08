# sn96 — Verathos ᚛

Verified AI inference and training subnet.

Bittensor subnet **96** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/verathos-ai/verathos) · [url](https://verathos.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004300 | -0.00% | +0.54% | -6.17% | 6,461 | 2,590 | 76.72 |

## Last 24h flow

72 trades by 33 coldkeys · 12 buys (41.83 τ) / 60 sells (32.92 τ) · net 8.92 τ

## News

- 2026-09-25 · release · [Verathos v0.2.3 – DeepSeek Mesh Proof Compatibility](https://github.com/verathos-ai/verathos/releases/tag/v0.2.3) — verathos-ai/verathos
- 2026-09-24 · commit · [fix: release DeepSeek mesh proof compatibility as v0.2.3](https://github.com/verathos-ai/verathos/commit/a859ff97c744eaf4b18761a8c7d9fd42d4a33573) — verathos-ai/verathos
- 2026-09-24 · commit · [fix: place hyper-connection head tensors in the final stage](https://github.com/verathos-ai/verathos/commit/4b969dfb0630b60cfb2e40fdae36851709ac3a12) — verathos-ai/verathos
- 2026-09-08 · release · [Verathos v0.2.2 – Consistent Validator Decisions and Microtensor Support](https://github.com/verathos-ai/verathos/releases/tag/v0.2.2) — verathos-ai/verathos
- 2026-09-08 · commit · [fix: preserve bounded mesh snapshot recovery](https://github.com/verathos-ai/verathos/commit/3135b74f4403aaaf1d847e45acec080329d1643e) — verathos-ai/verathos
- 2026-09-08 · commit · [fix: align follower eligibility and add Microtensor catalogue support](https://github.com/verathos-ai/verathos/commit/1ebb10658cd3a1475d9454b4b21d036011b892dd) — verathos-ai/verathos
- 2026-09-08 · commit · [fix: restrict setup choices to registered models](https://github.com/verathos-ai/verathos/commit/b986597248a9508544cacbbb0a2e41b0c424d9ae) — verathos-ai/verathos

## Use

```bash
m subnets.sn96/info        # live identity + market (snapshot if bt is down)
m subnets.sn96/news        # scraped news
m subnets.sn96/trades      # 24h alpha tape
m subnets.sn96/daily       # daily candles
python3 orbit/subnets/sn96/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
