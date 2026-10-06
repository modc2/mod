# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006071 | -0.00% | -0.26% | +13.54% | 33,920 | 17,877 | 276.60 |

## Last 24h flow

66 trades by 29 coldkeys · 7 buys (126.59 τ) / 59 sells (147.87 τ) · net -21.28 τ

## News

- 2026-10-06 · commit · [Add pinned task contracts and immutable platform delivery (#330)](https://github.com/reliquadotai/reliquary/commit/c45f729b7b8798ed7c0753ec63aa4465f8795fc2) — reliquadotai/reliquary
- 2026-10-05 · commit · [Merge pull request #329 from reliquadotai/fix/period-pay-catchup](https://github.com/reliquadotai/reliquary/commit/41c622dbb682e3724981a64de10653934c436de7) — reliquadotai/reliquary
- 2026-10-05 · commit · [fix(corpus): pay a period backlog back within a few periods](https://github.com/reliquadotai/reliquary/commit/bcb27910f8de8f7ca345ed80da0634715baa024c) — reliquadotai/reliquary
- 2026-10-05 · commit · [Merge pull request #316 from reliquadotai/feat/instruction-dataset-ex…](https://github.com/reliquadotai/reliquary/commit/da00d2b9dd6945bceacaebf62b919b9b51ccddd2) — reliquadotai/reliquary
- 2026-10-05 · commit · [feat: export bounded instruction datasets alongside corpus deliveries](https://github.com/reliquadotai/reliquary/commit/c1f43e9a1c6f5765953db7cd86b14ab0b4644d83) — reliquadotai/reliquary
- 2026-10-05 · commit · [Merge pull request #315 from reliquadotai/fix/grade-single-provider-l…](https://github.com/reliquadotai/reliquary/commit/484f2cd8910b4cfb3e194dbfaf72e50d6bf7c9e8) — reliquadotai/reliquary
- 2026-10-05 · commit · [fix(grade): leave an expired lease to the sweep, not the heartbeat ta…](https://github.com/reliquadotai/reliquary/commit/c6c974bde9070345ed4f29aca5cbed533fb1f444) — reliquadotai/reliquary
- 2026-10-05 · commit · [fix(grade): take back leases the executor does not hold; avoid stale …](https://github.com/reliquadotai/reliquary/commit/edc050e54b6970f976e02dd7d00ac35c3690c5f2) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
