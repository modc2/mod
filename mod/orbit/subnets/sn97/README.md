# sn97 — Ingot ა

Open models, cast small.

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/DendriteLabs/ingot) · [url](https://theingot.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.013033 | +0.00% | +0.04% | -14.94% | 26,398 | 10,897 | 1,031 |

## Last 24h flow

94 trades by 51 coldkeys · 54 buys (508.28 τ) / 40 sells (510.13 τ) · net -1.85 τ

## News

- 2026-10-17 · commit · [feat(web): link the vs-king name in the history table to the king's H…](https://github.com/DendriteLabs/ingot/commit/2e3ad444e56ae40c8a3d207b2793bf4647f216ba) — DendriteLabs/ingot
- 2026-10-17 · commit · [fix: reject FP8 where genesis is not and split fused projections; a c…](https://github.com/DendriteLabs/ingot/commit/eca63a0bc61038a1b051379709bc048cf68f2a03) — DendriteLabs/ingot
- 2026-10-16 · commit · [feat: order samples by prompt length, more teacher models](https://github.com/DendriteLabs/ingot/commit/08d9f36c4632aa3a4f94a6250072d6c7a94fc730) — DendriteLabs/ingot
- 2026-10-16 · commit · [fix(promoter): publish a king in one commit after the repo's initial one](https://github.com/DendriteLabs/ingot/commit/93b28b686f22c79a3c51b40fe5c38258237b9567) — DendriteLabs/ingot
- 2026-10-16 · commit · [feat: eval stage timing logs, loosened dedup thresholds, chain reader…](https://github.com/DendriteLabs/ingot/commit/271a12ae53303b79b747485d80f5297fbe58814a) — DendriteLabs/ingot
- 2026-10-16 · commit · [init: init of ingot](https://github.com/DendriteLabs/ingot/commit/e6c8206179153e228a021ff709dd9e311c066225) — DendriteLabs/ingot
- 2026-10-09 · commit · [fix: git -C diffs count as patches; heredoc edits reach the simulator…](https://github.com/unarbos/albedo/commit/4983bfbcb2d238390d967ca5bdd8ac7d01dc0899) — unarbos/albedo
- 2026-10-09 · commit · [fix: manifest upload works on R2; defaults point at the 13-source man…](https://github.com/unarbos/albedo/commit/2fdceb103c49c279150df4512bbe94cc2e37ccff) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
