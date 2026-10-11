# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.044346 | -0.34% | -1.97% | -3.82% | 196,988 | 75,785 | 1,193 |

## Last 24h flow

481 trades by 130 coldkeys · 75 buys (28.43 τ) / 406 sells (969.64 τ) · net -941.21 τ

## News

- 2026-10-11 · commit · [Report complete historical base comparison and current learning limits](https://github.com/AffineFoundation/affine/commit/62c6b661ddf60c77c1a874ee1ebe1320ebd64825) — AffineFoundation/affine
- 2026-10-11 · commit · [Add read-only admission check for pristine base checkpoints](https://github.com/AffineFoundation/affine/commit/ead892269d0fb547aaa5c3f634d32d33d9d3023d) — AffineFoundation/affine
- 2026-10-11 · commit · [Correct provenance of historical restart checkpoint](https://github.com/AffineFoundation/affine/commit/6d41c0a6e590a6e30689400b8b208b5f6d05bb3d) — AffineFoundation/affine
- 2026-10-10 · commit · [Document public evidence download clients](https://github.com/AffineFoundation/affine/commit/9b9f5292223539838a380cbca591edd91ca8f6c5) — AffineFoundation/affine
- 2026-10-10 · commit · [Publish actual training selection and exclusion accounting](https://github.com/AffineFoundation/affine/commit/3cab00bf11ca864bf999fb1283d77969adfb8eb4) — AffineFoundation/affine
- 2026-10-10 · commit · [Clarify availability of reused historical evaluations](https://github.com/AffineFoundation/affine/commit/260fb0b36b45b4cde4c6a816717c721961c51c02) — AffineFoundation/affine
- 2026-10-10 · commit · [Publish finalized training inputs, diagnostics and evaluation evidence](https://github.com/AffineFoundation/affine/commit/32c08eb7dc4d76eb4f2bbd5e050cc03e55cd2307) — AffineFoundation/affine
- 2026-10-10 · commit · [Restore prepared v3 token artifacts with task bindings](https://github.com/AffineFoundation/affine/commit/44429ff1c2954d17aebac3f2eee8ae0ba0081232) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
