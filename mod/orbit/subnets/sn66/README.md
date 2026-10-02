# sn66 — conjectures ض

Incentivizing breakthroughs on decades-old open mathematical conjectures

Bittensor subnet **66** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/conjectures-io/conjectures-validator) · [url](https://conjectures.io) · discord `wejh`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003415 | +0.01% | +2.13% | +7.74% | 20,181 | 7,183 | 398.14 |

## Last 24h flow

56 trades by 38 coldkeys · 18 buys (235.56 τ) / 38 sells (160.68 τ) · net 74.88 τ

## News

- 2026-09-30 · commit · [Remove open Green targets from catalog while retaining solved entries](https://github.com/conjectures-io/conjectures-validator/commit/d24e86cab809e69d388417b9114ce8d34a2256fc) — conjectures-io/conjectures-validator
- 2026-09-28 · commit · [Merge pull request #109 from conjectures-io/chore/remove-legacy-scoring](https://github.com/conjectures-io/conjectures-validator/commit/c1df92801a7a389ee9ec251f5edc8c67632aa034) — conjectures-io/conjectures-validator
- 2026-09-28 · commit · [Delete the superseded platform-side competition scorer](https://github.com/conjectures-io/conjectures-validator/commit/5bb74bcffeee07157b008175d9103013b10cb35d) — conjectures-io/conjectures-validator
- 2026-09-28 · commit · [Merge pull request #108 from conjectures-io/docs/frontier-only-policy](https://github.com/conjectures-io/conjectures-validator/commit/a17a8ada3a0035f04dc486ac88bb0d6eea8e4d08) — conjectures-io/conjectures-validator
- 2026-09-28 · commit · [Show the frontier-only policy in the competition API examples](https://github.com/conjectures-io/conjectures-validator/commit/330ac2237c1ba7955fa83ec6237bdc63eb2084bd) — conjectures-io/conjectures-validator
- 2026-09-16 · release · [v1.0.4](https://github.com/conjectures-io/conjectures-validator/releases/tag/v1.0.4) — conjectures-io/conjectures-validator
- 2026-09-07 · release · [v.1.0.3: Web submissions, payouts, contributions...](https://github.com/conjectures-io/conjectures-validator/releases/tag/v1.0.3) — conjectures-io/conjectures-validator

## Use

```bash
m subnets.sn66/info        # live identity + market (snapshot if bt is down)
m subnets.sn66/news        # scraped news
m subnets.sn66/trades      # 24h alpha tape
m subnets.sn66/daily       # daily candles
python3 orbit/subnets/sn66/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
