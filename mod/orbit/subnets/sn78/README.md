# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003710 | -0.50% | -2.36% | -11.33% | 5,662 | 1,452 | 289.45 |

## Last 24h flow

124 trades by 49 coldkeys · 61 buys (135.27 τ) / 63 sells (151.84 τ) · net -16.57 τ

## News

- 2026-10-09 · commit · [Merge pull request #235 from Umi-BitSign/codex/c5-recovery-integratio…](https://github.com/Umi-BitSign/umi/commit/0a2c1738dd1df52bdd5a04af9f613379ed5196fb) — Umi-BitSign/umi
- 2026-10-09 · commit · [Align recovery integration checks with shared windows and retained pr…](https://github.com/Umi-BitSign/umi/commit/9a04924db87c070543609b30be6cc0bf042acfb1) — Umi-BitSign/umi
- 2026-10-09 · commit · [Merge pull request #234 from Umi-BitSign/codex/c5-retained-export-rec…](https://github.com/Umi-BitSign/umi/commit/76f605c1b59edfb0c76b5f1ed78eae2cce28d77f) — Umi-BitSign/umi
- 2026-10-09 · commit · [Keep cohort status reads and retained exports moving during recovery](https://github.com/Umi-BitSign/umi/commit/d72028dd137f4a09b9cd0d38a6aab9faf98d010f) — Umi-BitSign/umi
- 2026-10-09 · commit · [Merge pull request #233 from Umi-BitSign/codex/c5-coordinator-read-la…](https://github.com/Umi-BitSign/umi/commit/4b9a1dfa64b6f96548e8137aa20a4a45eb1d78ab) — Umi-BitSign/umi
- 2026-10-08 · commit · [Restore original C5 progression and preserve miner upgrade state (#231)](https://github.com/Umi-BitSign/umi/commit/d61b62838a3089f4c25af0303a4309a1e12c9d16) — Umi-BitSign/umi
- 2026-10-06 · commit · [Reduce intake seal contention and isolate upgraded miner imports (#230)](https://github.com/Umi-BitSign/umi/commit/e7ef2c16b9abe7487f2926de294a1ed57d8fa5f5) — Umi-BitSign/umi
- 2026-10-06 · commit · [Preserve manual miner state during upgrades and bound supervisor catc…](https://github.com/Umi-BitSign/umi/commit/4953ccf9c8d20bbab2d1174a2b5f3a51932d8dc5) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
