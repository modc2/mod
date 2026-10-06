# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.046554 | +0.06% | +0.87% | -5.94% | 205,021 | 77,347 | 1,452 |

## Last 24h flow

235 trades by 87 coldkeys · 62 buys (690.35 τ) / 173 sells (537.25 τ) · net 153.10 τ

## News

- 2026-10-06 · commit · [Document current cache acknowledgment and prospective fast evaluation](https://github.com/AffineFoundation/affine/commit/a019dc611a340775b9956854c368ea41e287ee81) — AffineFoundation/affine
- 2026-10-06 · commit · [Add explicit trusted native evaluation with unchanged sampling](https://github.com/AffineFoundation/affine/commit/8a597f18fb360073f2a6f682ba440a8ebcfea4fa) — AffineFoundation/affine
- 2026-10-06 · commit · [Publish measured E19 durable completion and remaining warm-cache vali…](https://github.com/AffineFoundation/affine/commit/af48df1aa3ffb72761c0d9fd5f39900cac2a0f7a) — AffineFoundation/affine
- 2026-10-06 · commit · [Report completed training backend and measured compact H200 qualifica…](https://github.com/AffineFoundation/affine/commit/e02de4c5e27923e49059e3ccc69d517ab0254128) — AffineFoundation/affine
- 2026-10-06 · commit · [Reject unsupported compact probability policies before queue admission](https://github.com/AffineFoundation/affine/commit/e48787d9c29e912c6594fce437e422d4ddead4c2) — AffineFoundation/affine
- 2026-10-05 · commit · [Distinguish prospective evaluator hooks from live historical diagnostics](https://github.com/AffineFoundation/affine/commit/ec939a6fe6b2033b08f3c1bac7a0a6badbfa87d2) — AffineFoundation/affine
- 2026-10-05 · commit · [Retain evaluator current checkpoint and automatically retire authenti…](https://github.com/AffineFoundation/affine/commit/013c27015e5c00ddbc9001627804d6c46e32a054) — AffineFoundation/affine
- 2026-10-05 · commit · [Keep evaluator adoption standalone and reject escaping catalog paths …](https://github.com/AffineFoundation/affine/commit/7ca21ad18b611c97cd3a44e24a74690ffabd106b) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
