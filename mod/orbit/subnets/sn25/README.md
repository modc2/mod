# sn25 — UR א

The Privacy Protocol

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005776 | — | -0.20% | -9.25% | 35,897 | 12,351 | 317.59 |

## Last 24h flow

47 trades by 34 coldkeys · 21 buys (150.68 τ) / 26 sells (164.23 τ) · net -13.56 τ

## News

- 2026-10-19 · release · [v2026.10.18-1075707760](https://github.com/urfoundation/sn/releases/tag/v2026.10.18-1075707760) — urfoundation/sn
- 2026-10-19 · commit · [Merge fix/provider-diagnostics-windows-console into main](https://github.com/urfoundation/sn/commit/6e22ce9a574cafa20736d1df2f01df130336a7d9) — urfoundation/sn
- 2026-10-19 · commit · [Deliver diagnostics to Windows consoles and pipes](https://github.com/urfoundation/sn/commit/e817976c85d2a1ac43591fc4bb728e849291d0b0) — urfoundation/sn
- 2026-10-19 · commit · [Merge fix/provider-diagnostics-sink into main](https://github.com/urfoundation/sn/commit/9a2075784702d34ac5e10fba44dc2312536427d8) — urfoundation/sn
- 2026-10-18 · commit · [Admit regular files and macOS terminals as diagnostic sinks](https://github.com/urfoundation/sn/commit/3306a0bb3b86170d793aa960543b215c60e8cc92) — urfoundation/sn
- 2026-10-18 · release · [v2026.10.18-1075514170](https://github.com/urfoundation/sn/releases/tag/v2026.10.18-1075514170) — urfoundation/sn
- 2026-10-18 · commit · [Merge completed gate fixes with explicit staging contracts](https://github.com/urfoundation/sn/commit/26fb89501ee44e0581a06efaa29b2a09cdfed4ba) — urfoundation/sn
- 2026-10-18 · commit · [feat(competition): document explicit v2 staging contracts](https://github.com/urfoundation/sn/commit/906cc7d179f9f4381644c71cc2038ad1cfa4d8cb) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
