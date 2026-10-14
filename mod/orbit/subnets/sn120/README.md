# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.043148 | +0.00% | -1.17% | -7.25% | 192,671 | 74,924 | 1,073 |

## Last 24h flow

856 trades by 105 coldkeys · 61 buys (127.95 τ) / 795 sells (721.62 τ) · net -593.66 τ

## News

- 2026-10-14 · commit · [Freeze finalized evidence inventory during history exports](https://github.com/AffineFoundation/affine/commit/8b819de8c0f57c6a12d433d9505f8af5b1e13dc2) — AffineFoundation/affine
- 2026-10-13 · commit · [Reduce repeated commitment derivation in hourly reward assessment](https://github.com/AffineFoundation/affine/commit/767046c7704c265e15994789137ed447bab71187) — AffineFoundation/affine
- 2026-10-13 · commit · [docs: explain current audit penalties and decaying reward assessment](https://github.com/AffineFoundation/affine/commit/13585ac50a190c5d73d94810d0768ef201c8920b) — AffineFoundation/affine
- 2026-10-13 · commit · [Document and support unfunded PoW miner onboarding](https://github.com/AffineFoundation/affine/commit/7eef2de6f146888f92a990d41c300546f42f4780) — AffineFoundation/affine
- 2026-10-13 · commit · [Support 1000 registered UIDs after Null consensus switch](https://github.com/AffineFoundation/affine/commit/0a24c9befa5c2d268ec86e55a463ef9ee204dc77) — AffineFoundation/affine
- 2026-10-13 · commit · [docs: document Null consensus and PoW registration at 100M difficulty](https://github.com/AffineFoundation/affine/commit/ad6ffc6a9ea4b43b8a389dccd1ca20afc9ed93cc) — AffineFoundation/affine
- 2026-10-13 · commit · [Reuse immutable manifest derivations during hourly evidence reads](https://github.com/AffineFoundation/affine/commit/35be6eaf5d5887eaf2f44f1ee04dda5a89d39087) — AffineFoundation/affine
- 2026-10-13 · commit · [Clarify qualified cached MiMo generation and diagnostic details](https://github.com/AffineFoundation/affine/commit/d1f60ed49c7b4d177805636eae369e541077a4f7) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
