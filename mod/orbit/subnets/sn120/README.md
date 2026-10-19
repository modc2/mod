# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.043255 | — | +0.74% | -2.33% | 194,916 | 75,289 | 1,303 |

## Last 24h flow

671 trades by 99 coldkeys · 71 buys (676.40 τ) / 600 sells (505.18 τ) · net 171.22 τ

## News

- 2026-10-14 · commit · [Merge pull request #80 from AffineFoundation/fix/first-claim-wins](https://github.com/AffineFoundation/affine/commit/f07c54e31bbfe8d941f6250421bf7959a4f1f67c) — AffineFoundation/affine
- 2026-10-14 · commit · [Award contested tasks to the earliest Cloudflare R2 claim](https://github.com/AffineFoundation/affine/commit/bcc9ead35aabeaf58256e6f3ad500a2e2ba26265) — AffineFoundation/affine
- 2026-10-14 · commit · [Record confirmed hourly weight freshness recovery](https://github.com/AffineFoundation/affine/commit/f704f5683f8a675da95aacc39b548013c61e8529) — AffineFoundation/affine
- 2026-10-14 · commit · [Restore hourly assessment freshness with parallel bounded evidence reads](https://github.com/AffineFoundation/affine/commit/d3aac91fe4b35b82e1891e2c098d21f4ab33f1a1) — AffineFoundation/affine
- 2026-10-14 · commit · [Freeze finalized evidence inventory during history exports](https://github.com/AffineFoundation/affine/commit/8b819de8c0f57c6a12d433d9505f8af5b1e13dc2) — AffineFoundation/affine
- 2026-10-13 · commit · [Reduce repeated commitment derivation in hourly reward assessment](https://github.com/AffineFoundation/affine/commit/767046c7704c265e15994789137ed447bab71187) — AffineFoundation/affine
- 2026-10-13 · commit · [docs: explain current audit penalties and decaying reward assessment](https://github.com/AffineFoundation/affine/commit/13585ac50a190c5d73d94810d0768ef201c8920b) — AffineFoundation/affine
- 2026-10-13 · commit · [Document and support unfunded PoW miner onboarding](https://github.com/AffineFoundation/affine/commit/7eef2de6f146888f92a990d41c300546f42f4780) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
