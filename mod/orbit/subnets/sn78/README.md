# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003465 | +0.73% | -0.70% | -8.83% | 5,438 | 1,405 | 606.45 |

## Last 24h flow

255 trades by 118 coldkeys · 86 buys (300.44 τ) / 169 sells (304.83 τ) · net -4.39 τ

## News

- 2026-10-15 · commit · [Merge pull request #261 from Umi-BitSign/fix/c6-progression-hold-2026…](https://github.com/Umi-BitSign/umi/commit/6d5569d8fb5a714d05ecdb403ce71f05afe53249) — Umi-BitSign/umi
- 2026-10-15 · commit · [Allow explicit per-cohort automatic progression holds during releases](https://github.com/Umi-BitSign/umi/commit/f2e521758bbe0e6453c68d84626fc1b315fdfe5a) — Umi-BitSign/umi
- 2026-10-14 · commit · [Merge pull request #260 from Umi-BitSign/codex/c5-reconciliation-cont…](https://github.com/Umi-BitSign/umi/commit/6dea5aff084753e87f511e37347421694d3d6759) — Umi-BitSign/umi
- 2026-10-14 · commit · [Keep stopped reward reconciliation alive across native history batches](https://github.com/Umi-BitSign/umi/commit/b932ac09c2470cb449ea52d94148fbed84b08f89) — Umi-BitSign/umi
- 2026-10-14 · commit · [Merge pull request #259 from Umi-BitSign/codex/history-finality-windo…](https://github.com/Umi-BitSign/umi/commit/17c8ea8a63eb15c8dfc0259a1809120224b4734d) — Umi-BitSign/umi
- 2026-10-14 · commit · [Reuse owned finalized heads within bounded historical proof batches](https://github.com/Umi-BitSign/umi/commit/d6ed858d0b14fafe8e86dd991affa6da395b1f1b) — Umi-BitSign/umi
- 2026-10-14 · commit · [Merge pull request #258 from Umi-BitSign/codex/retained-host-capacity…](https://github.com/Umi-BitSign/umi/commit/365e34817cc45ce669607af300a60917340329f6) — Umi-BitSign/umi
- 2026-10-14 · commit · [Allow explicit retained host staging capacity](https://github.com/Umi-BitSign/umi/commit/979583e7ed62e73bbbd7f75203062546ebd92ae7) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
