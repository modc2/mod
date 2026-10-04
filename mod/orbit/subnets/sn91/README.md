# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005209 | -0.03% | -9.78% | +3.50% | 9,675 | 3,382 | 1,657 |

## Last 24h flow

334 trades by 140 coldkeys · 127 buys (739.29 τ) / 207 sells (916.11 τ) · net -176.81 τ

## News

- 2026-10-03 · commit · [Merge pull request #351 from TensorLink-AI/fix/king-pod-on-demand](https://github.com/TensorLink-AI/cascade/commit/f0abef4989b3bd7ceac0580510daa23ce45c33a1) — TensorLink-AI/cascade
- 2026-10-03 · commit · [Rolling: release an era's king pod as soon as it is idle](https://github.com/TensorLink-AI/cascade/commit/4a6f30c3302dfc2c804183aa88974628936cb6cd) — TensorLink-AI/cascade
- 2026-10-03 · commit · [Merge pull request #350 from TensorLink-AI/fix/settle-before-bench](https://github.com/TensorLink-AI/cascade/commit/8b802afbdeb8bd58be57cc84013578b5c6759a89) — TensorLink-AI/cascade
- 2026-10-02 · commit · [Rolling: a leg settles when TRAINING ends; its bench runs alongside](https://github.com/TensorLink-AI/cascade/commit/c778f0869aafcf17b39a932e83c99d1eaf61e84e) — TensorLink-AI/cascade
- 2026-10-02 · commit · [Merge pull request #349 from TensorLink-AI/fix/scrape-kings-archive-key](https://github.com/TensorLink-AI/cascade/commit/b89417935dec0f637a7c2c06c729e99f60069faa) — TensorLink-AI/cascade
- 2026-10-02 · commit · [scrape-kings: pass the dedicated KING_ARCHIVE_S3_* key to the scraper](https://github.com/TensorLink-AI/cascade/commit/eb2c53dace751fce0e8cda64507a5058fe02379e) — TensorLink-AI/cascade
- 2026-10-02 · commit · [Merge pull request #348 from TensorLink-AI/fix/receipt-forward-compat](https://github.com/TensorLink-AI/cascade/commit/e9f4f84b630d76aa9fea544ae1be754a5c3283ee) — TensorLink-AI/cascade
- 2026-10-02 · commit · [test_margin_v2: drop Yoda comparisons (ruff SIM300, red on main since…](https://github.com/TensorLink-AI/cascade/commit/8f0d0ee6168bd69e2e35a7684230640f2ce8586b) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
