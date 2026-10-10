# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014971 | +0.37% | -1.23% | -7.89% | 29,239 | 11,431 | 856.80 |

## Last 24h flow

94 trades by 52 coldkeys · 24 buys (362.18 τ) / 70 sells (459.59 τ) · net -97.41 τ

## News

- 2026-10-09 · commit · [fix: git -C diffs count as patches; heredoc edits reach the simulator…](https://github.com/unarbos/albedo/commit/4983bfbcb2d238390d967ca5bdd8ac7d01dc0899) — unarbos/albedo
- 2026-10-09 · commit · [fix: manifest upload works on R2; defaults point at the 13-source man…](https://github.com/unarbos/albedo/commit/2fdceb103c49c279150df4512bbe94cc2e37ccff) — unarbos/albedo
- 2026-10-09 · commit · [perf: simulator prompt shares the sample's history cache across rollo…](https://github.com/unarbos/albedo/commit/934f3dd898b41f5a16f13188f92d2b2ef1feefd3) — unarbos/albedo
- 2026-10-08 · commit · [feat: display new datasets on frontend](https://github.com/unarbos/albedo/commit/6ff609d0eaaf904d55731858d0d733eab1c0c6b3) — unarbos/albedo
- 2026-10-08 · commit · [feat: new affine dataset, new open-swe-traces splits](https://github.com/unarbos/albedo/commit/fcf2b97206c30f01cdd810ce6889fceea1240f0b) — unarbos/albedo
- 2026-10-08 · commit · [feat: unified fonts, layout, darker background. more readable benchma…](https://github.com/unarbos/albedo/commit/aee0b011ba317c8cd4c68512e284dd1709c6e05f) — unarbos/albedo
- 2026-10-07 · commit · [fix: a code block in the reasoning no longer hides the git diff that …](https://github.com/unarbos/albedo/commit/0b7a1681f291a27a11f212f9068ca1a7a71c32c1) — unarbos/albedo
- 2026-10-06 · commit · [perf: fonts no longer block the first paint, no preds probes for fini…](https://github.com/unarbos/albedo/commit/6104011fe925c96f1c33b36495cd0d85fa3fba78) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
