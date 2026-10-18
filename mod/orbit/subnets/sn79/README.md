# sn79 — MVTRX ي

Building a SOTA Exchange for dTAO and Beyond

Bittensor subnet **79** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/taos-im/sn-79) · [url](https://taos.im)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003030 | -0.03% | +0.71% | -3.26% | 16,177 | 6,927 | 67.69 |

## Last 24h flow

105 trades by 19 coldkeys · 5 buys (42.21 τ) / 100 sells (20.77 τ) · net 21.44 τ

## News

- 2026-10-16 · commit · [20261016 - 0.6.4 final](https://github.com/taos-im/sn-79/commit/690e5bab4492dcba552e59f2f5cabe5459f0346a) — taos-im/sn-79
- 2026-10-14 · commit · [20261014 - 0.6.4 fee-aware market making](https://github.com/taos-im/sn-79/commit/19f9fbd90798994c9760dd5fc1e2d31ae55f9af5) — taos-im/sn-79
- 2026-10-08 · commit · [20261008 - 0.6.3 final](https://github.com/taos-im/sn-79/commit/49e79d5ffac8dc0b95717c249b8cf6200eb15537) — taos-im/sn-79
- 2026-10-06 · commit · [20261006 - 0.6.3 multi-asset simulation](https://github.com/taos-im/sn-79/commit/eb46a0b93bf4a9d8742949fc6adf9df97809a6c9) — taos-im/sn-79
- 2026-09-29 · commit · [20260929 - 0.6.2 pool pay vector and slot reset](https://github.com/taos-im/sn-79/commit/47932d4c7bca8403f9a2942903d153a39caa7f5f) — taos-im/sn-79
- 2026-09-28 · commit · [20260928 - 0.6.2 skill floor basis](https://github.com/taos-im/sn-79/commit/77fcb3098e6f6da059f4c9d4368ff5046a4357c4) — taos-im/sn-79
- 2026-09-25 · commit · [20260925 - 0.6.2 making pool](https://github.com/taos-im/sn-79/commit/c05768ce2740eb3d9c7f09f41be5198208820fcd) — taos-im/sn-79
- 2026-09-23 · commit · [20260923 - 0.6.2](https://github.com/taos-im/sn-79/commit/2564fa5bb6a1b62cb95f3af546b09080a0577819) — taos-im/sn-79

## Use

```bash
m subnets.sn79/info        # live identity + market (snapshot if bt is down)
m subnets.sn79/news        # scraped news
m subnets.sn79/trades      # 24h alpha tape
m subnets.sn79/daily       # daily candles
python3 orbit/subnets/sn79/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
