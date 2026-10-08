# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006849 | +0.09% | -0.01% | +0.96% | 41,955 | 13,380 | 1,974 |

## Last 24h flow

82 trades by 44 coldkeys · 38 buys (986.03 τ) / 44 sells (985.68 τ) · net 0.35 τ

## News

- 2026-10-07 · commit · [Merge the accelerated first-epoch mainnet policy](https://github.com/urfoundation/sn/commit/baebe0e526cf427c5f560f45f6a142e6468d1f69) — urfoundation/sn
- 2026-10-07 · commit · [Merge the runtime 473 review record](https://github.com/urfoundation/sn/commit/163c07b6516364495c6f751325889d1088d532f3) — urfoundation/sn
- 2026-10-07 · commit · [Add the runtime 473 source and interface review record](https://github.com/urfoundation/sn/commit/768634402f6c4b7007efced13a33c39a28f22818) — urfoundation/sn
- 2026-10-07 · commit · [Assert the owner-validator's root seat keeps its own coldkey](https://github.com/urfoundation/sn/commit/b8b59a1d35b80957452624066396fc989ef6b3f6) — urfoundation/sn
- 2026-10-07 · commit · [Accelerate the mainnet policy's first epoch to one day](https://github.com/urfoundation/sn/commit/6eabad620c8546b3ca1987abdeec86a2f1f7b5ac) — urfoundation/sn
- 2026-10-07 · commit · [Merge the sole validator's activation-pending config path](https://github.com/urfoundation/sn/commit/86c3ff7d37c5916aa5ba57ac502535dc9c1b792d) — urfoundation/sn
- 2026-10-07 · commit · [Admit the v5 sole validator's activation-pending config at bootstrap](https://github.com/urfoundation/sn/commit/6829187427cccd0c34435c0dd7c5ed3392cdff27) — urfoundation/sn
- 2026-10-07 · commit · [Render a signed production config's pending evidence into a re-approv…](https://github.com/urfoundation/sn/commit/dc7e01b48392f9f1de3d2d1566227bc338d41925) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
