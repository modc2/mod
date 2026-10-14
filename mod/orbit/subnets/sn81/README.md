# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005573 | -0.00% | -0.57% | -5.06% | 31,457 | 17,128 | 473.70 |

## Last 24h flow

43 trades by 23 coldkeys · 15 buys (212.20 τ) / 28 sells (260.52 τ) · net -48.32 τ

## News

- 2026-10-13 · commit · [Merge pull request #352 from reliquadotai/feat/envscaler-tools-v2](https://github.com/reliquadotai/reliquary/commit/8c1ae92ca4bb9d4f6ef20909083785c9ffdac984) — reliquadotai/reliquary
- 2026-10-13 · commit · [feat(envscaler): corpus jobs draw from 12 of the 25 SFT worlds, at \[7…](https://github.com/reliquadotai/reliquary/commit/8cc82faf204fe40e030ec5423ee06a4ce86c9aae) — reliquadotai/reliquary
- 2026-10-13 · commit · [test(envscaler): the v2 suite job declares a 3072-token turn cap](https://github.com/reliquadotai/reliquary/commit/41945192548d9b06fdd1f234fc6125a2b3af84e4) — reliquadotai/reliquary
- 2026-10-13 · commit · [fix(envscaler): every world starts on Python 3.12 and 3.13 alike; men…](https://github.com/reliquadotai/reliquary/commit/b1dd61fe66579fb0bc297933e7c4d6d75b66dfb1) — reliquadotai/reliquary
- 2026-10-13 · commit · [feat(envscaler): one call per message in the task prompt; episodes bo…](https://github.com/reliquadotai/reliquary/commit/10f53aea06de253d65e313668a06ba022a3d8e86) — reliquadotai/reliquary
- 2026-10-11 · commit · [Merge pull request #349 from reliquadotai/feat/corpus-pay-route](https://github.com/reliquadotai/reliquary/commit/b1698c62da8226194a2b9bc851aebc2bb3d7b23d) — reliquadotai/reliquary
- 2026-10-11 · commit · [feat(corpus): GET /corpus/pay estimates each active job's pay per token](https://github.com/reliquadotai/reliquary/commit/e380e2e0e4c4e7d12b8c010f7cc86df3675afb59) — reliquadotai/reliquary
- 2026-10-11 · commit · [Merge pull request #348 from reliquadotai/feat/corpus-token-price-cei…](https://github.com/reliquadotai/reliquary/commit/03678e182dc4025138393061bff134018127b09b) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
