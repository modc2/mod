# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005186 | -0.00% | +0.70% | -8.85% | 29,424 | 16,524 | 229.98 |

## Last 24h flow

37 trades by 18 coldkeys · 9 buys (143.72 τ) / 28 sells (85.76 τ) · net 57.96 τ

## News

- 2026-10-16 · commit · [Merge pull request #357 from reliquadotai/feat/corpus-sandbox-yield-q…](https://github.com/reliquadotai/reliquary/commit/b781cc55da988fe957ba2d0359d5fac167d75b38) — reliquadotai/reliquary
- 2026-10-16 · commit · [Merge pull request #355 from reliquadotai/fix/corpus-split-sandbox-ho…](https://github.com/reliquadotai/reliquary/commit/8bb7e326af6d2ed5797866eb316b99d941174e7b) — reliquadotai/reliquary
- 2026-10-16 · commit · [test(corpus): skip the quota tests without the sandbox extra](https://github.com/reliquadotai/reliquary/commit/c08284320395f66bf21725ff9cb59e4dbfb379ff) — reliquadotai/reliquary
- 2026-10-16 · commit · [feat(corpus): demand-driven, fleet-bounded quota for signed sandbox s…](https://github.com/reliquadotai/reliquary/commit/cadb2f29e44cac812aadeb834d0c804759b64744) — reliquadotai/reliquary
- 2026-10-15 · commit · [feat(competitive-code): register the v1.1 wheel (1,893 SFT problems a…](https://github.com/reliquadotai/reliquary/commit/b7207a9c79969311f72d7bc14059bab4009f71fa) — reliquadotai/reliquary
- 2026-10-15 · commit · [Merge pull request #354 from reliquadotai/merge/main-into-next-rl-run](https://github.com/reliquadotai/reliquary/commit/fde75c0c3b5ffd4a94cf7fc97db8f248f74a4761) — reliquadotai/reliquary
- 2026-10-14 · commit · [test(proof): the startup-order test stubs the episode services step](https://github.com/reliquadotai/reliquary/commit/3d0eb6e6da45c2176eb8a87df88ba2b9d49dfc30) — reliquadotai/reliquary
- 2026-10-14 · commit · [test(admission): wait for the spawned worker longer than its imports](https://github.com/reliquadotai/reliquary/commit/2f569b445eb18f8e7c822b14696b6d72a24bf865) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
