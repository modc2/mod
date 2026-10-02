# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005687 | -0.43% | +12.17% | +2.06% | 10,487 | 3,523 | 2,376 |

## Last 24h flow

412 trades by 131 coldkeys · 239 buys (1,286 τ) / 173 sells (1,084 τ) · net 202.27 τ

## News

- 2026-10-02 · commit · [Merge pull request #348 from TensorLink-AI/fix/receipt-forward-compat](https://github.com/TensorLink-AI/cascade/commit/e9f4f84b630d76aa9fea544ae1be754a5c3283ee) — TensorLink-AI/cascade
- 2026-10-02 · commit · [test_margin_v2: drop Yoda comparisons (ruff SIM300, red on main since…](https://github.com/TensorLink-AI/cascade/commit/8f0d0ee6168bd69e2e35a7684230640f2ce8586b) — TensorLink-AI/cascade
- 2026-10-02 · commit · [king sync (finney): vault/direct@sha256:4500367f1004fc38ca7c1fac18532…](https://github.com/TensorLink-AI/cascade/commit/04a97108e5cd9b9f507d88ef2d10b6458c916806) — TensorLink-AI/cascade
- 2026-10-02 · commit · [Receipts verify across code versions; a stale receipt king is never s…](https://github.com/TensorLink-AI/cascade/commit/a680c8ff9894f6afe44fb8592cb5a8807867c9cd) — TensorLink-AI/cascade
- 2026-10-01 · commit · [Merge pull request #347 from TensorLink-AI/fix/validator-startup-rest…](https://github.com/TensorLink-AI/cascade/commit/a6e6dc4c2590b598b3ed67c69f205fde9115e0d1) — TensorLink-AI/cascade
- 2026-10-01 · commit · [validator: restore a chain-decided rollover before the forfeiture at …](https://github.com/TensorLink-AI/cascade/commit/22e734badd80aa4dd4c2b93d144909a3f53f48af) — TensorLink-AI/cascade
- 2026-10-01 · commit · [Merge pull request #345 from TensorLink-AI/feat/margin-v2-schedule](https://github.com/TensorLink-AI/cascade/commit/f273366a98621f4063fe0be042877d43a29148da) — TensorLink-AI/cascade
- 2026-10-01 · commit · [Margin v2: switch on at the next era start; level fallback keeps the …](https://github.com/TensorLink-AI/cascade/commit/2cd65bc088249abb707b194f641ae66564f2b727) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
