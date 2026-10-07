# sn111 — Claims Ё

Turning scientific literature into a structured claim-evidence graph

Bittensor subnet **111** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/DeSciClaims/Claims) · [discord](https://discord.com/channels/799672011265015819/1515007366016401599)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004179 | +0.01% | -0.74% | -5.52% | 20,606 | 5,498 | 69.71 |

## Last 24h flow

33 trades by 24 coldkeys · 4 buys (23.53 τ) / 29 sells (44.57 τ) · net -21.04 τ

## News

- 2026-10-06 · commit · [feat(validator): default miner burn to 90 percent](https://github.com/DeSciClaims/Claims/commit/6b4a427c58b524c826b6dfc32e82f3092cfe2d25) — DeSciClaims/Claims
- 2026-10-06 · commit · [fix(validator): scope split audit repairs to expected draft units](https://github.com/DeSciClaims/Claims/commit/366d4797f405677635935675c6b562aea529d341) — DeSciClaims/Claims
- 2026-10-06 · commit · [feat(validator): support configurable owner miner burn](https://github.com/DeSciClaims/Claims/commit/2ab7dda3311929e687484345190c4d0a6c37cdf5) — DeSciClaims/Claims
- 2026-10-02 · release · [v1.0.1](https://github.com/DeSciClaims/Claims/releases/tag/v1.0.1) — DeSciClaims/Claims
- 2026-09-29 · commit · [feat(validator): wait for due canonical batches](https://github.com/DeSciClaims/Claims/commit/e3bceb9503171a23912ce472cb0653bfc9499757) — DeSciClaims/Claims
- 2026-09-29 · commit · [fix(silver): reduce adjudication batch size to four](https://github.com/DeSciClaims/Claims/commit/a85c4f55461e134c575191e4c7e35124c8287b96) — DeSciClaims/Claims
- 2026-09-29 · commit · [fix(silver): raise appellate output limit](https://github.com/DeSciClaims/Claims/commit/05eab72d194b5e78c72367fed748a648730581df) — DeSciClaims/Claims
- 2026-09-29 · commit · [fix(sources): isolate consensus PDF failures](https://github.com/DeSciClaims/Claims/commit/23655b86e4285f1f1fddcf5a4700ca301b4d8164) — DeSciClaims/Claims

## Use

```bash
m subnets.sn111/info        # live identity + market (snapshot if bt is down)
m subnets.sn111/news        # scraped news
m subnets.sn111/trades      # 24h alpha tape
m subnets.sn111/daily       # daily candles
python3 orbit/subnets/sn111/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
