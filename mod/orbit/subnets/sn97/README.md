# sn97 — Ingot ა

Open models, cast small.

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/DendriteLabs/ingot) · [url](https://theingot.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.013026 | +0.00% | +4.10% | -12.99% | 26,273 | 10,871 | 4,044 |

## Last 24h flow

332 trades by 89 coldkeys · 174 buys (2,111 τ) / 158 sells (1,900 τ) · net 211.81 τ

## News

- 2026-10-16 · commit · [feat: order samples by prompt length, more teacher models](https://github.com/DendriteLabs/ingot/commit/08d9f36c4632aa3a4f94a6250072d6c7a94fc730) — DendriteLabs/ingot
- 2026-10-16 · commit · [fix(promoter): publish a king in one commit after the repo's initial one](https://github.com/DendriteLabs/ingot/commit/93b28b686f22c79a3c51b40fe5c38258237b9567) — DendriteLabs/ingot
- 2026-10-16 · commit · [feat: eval stage timing logs, loosened dedup thresholds, chain reader…](https://github.com/DendriteLabs/ingot/commit/271a12ae53303b79b747485d80f5297fbe58814a) — DendriteLabs/ingot
- 2026-10-16 · commit · [init: init of ingot](https://github.com/DendriteLabs/ingot/commit/e6c8206179153e228a021ff709dd9e311c066225) — DendriteLabs/ingot
- 2026-10-09 · commit · [fix: git -C diffs count as patches; heredoc edits reach the simulator…](https://github.com/unarbos/albedo/commit/4983bfbcb2d238390d967ca5bdd8ac7d01dc0899) — unarbos/albedo
- 2026-10-09 · commit · [fix: manifest upload works on R2; defaults point at the 13-source man…](https://github.com/unarbos/albedo/commit/2fdceb103c49c279150df4512bbe94cc2e37ccff) — unarbos/albedo
- 2026-10-09 · commit · [perf: simulator prompt shares the sample's history cache across rollo…](https://github.com/unarbos/albedo/commit/934f3dd898b41f5a16f13188f92d2b2ef1feefd3) — unarbos/albedo
- 2026-10-08 · commit · [feat: display new datasets on frontend](https://github.com/unarbos/albedo/commit/6ff609d0eaaf904d55731858d0d733eab1c0c6b3) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
