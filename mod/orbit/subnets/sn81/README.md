# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005870 | +0.00% | -3.32% | +18.00% | 32,837 | 17,578 | 2,455 |

## Last 24h flow

137 trades by 57 coldkeys · 51 buys (1,078 τ) / 86 sells (1,375 τ) · net -297.05 τ

## News

- 2026-10-07 · commit · [Merge pull request #331 from reliquadotai/fix/grade-oldest-first](https://github.com/reliquadotai/reliquary/commit/f6d2a402efcab2350ccea110d70c9cd75e213bee) — reliquadotai/reliquary
- 2026-10-06 · commit · [fix(corpus): let the grade lease cap follow the executors' concurrency](https://github.com/reliquadotai/reliquary/commit/fcc74e1bfb7c0a8b4a1ca9770889346bc092c2c2) — reliquadotai/reliquary
- 2026-10-06 · commit · [fix(corpus): grade a relisted backlog oldest arrival first](https://github.com/reliquadotai/reliquary/commit/7968f121f09491786d446bdfc9d67e6ad9ac4590) — reliquadotai/reliquary
- 2026-10-06 · commit · [Add pinned task contracts and immutable platform delivery (#330)](https://github.com/reliquadotai/reliquary/commit/c45f729b7b8798ed7c0753ec63aa4465f8795fc2) — reliquadotai/reliquary
- 2026-10-05 · commit · [Merge pull request #329 from reliquadotai/fix/period-pay-catchup](https://github.com/reliquadotai/reliquary/commit/41c622dbb682e3724981a64de10653934c436de7) — reliquadotai/reliquary
- 2026-10-05 · commit · [fix(corpus): pay a period backlog back within a few periods](https://github.com/reliquadotai/reliquary/commit/bcb27910f8de8f7ca345ed80da0634715baa024c) — reliquadotai/reliquary
- 2026-10-05 · commit · [Merge pull request #316 from reliquadotai/feat/instruction-dataset-ex…](https://github.com/reliquadotai/reliquary/commit/da00d2b9dd6945bceacaebf62b919b9b51ccddd2) — reliquadotai/reliquary
- 2026-10-05 · commit · [feat: export bounded instruction datasets alongside corpus deliveries](https://github.com/reliquadotai/reliquary/commit/c1f43e9a1c6f5765953db7cd86b14ab0b4644d83) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
