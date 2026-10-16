# sn25 — UR א

The Privacy Protocol

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005425 | -0.42% | -5.57% | -17.84% | 33,588 | 11,954 | 1,449 |

## Last 24h flow

77 trades by 40 coldkeys · 28 buys (549.09 τ) / 49 sells (897.20 τ) · net -348.10 τ

## News

- 2026-10-16 · commit · [Merge v2/gate-pin-hardening into main](https://github.com/urfoundation/sn/commit/b2c184b361b51a3443bff6527bf4e97148cf000e) — urfoundation/sn
- 2026-10-16 · commit · [Merge origin/main into v2/gate-pin-hardening](https://github.com/urfoundation/sn/commit/70c1346e67d80f579e4947dc851d1ac111a7b65f) — urfoundation/sn
- 2026-10-16 · commit · [Census every sn script that runs go test -run, with root-cause tests](https://github.com/urfoundation/sn/commit/3eeabdbdfad02a2d3629b342d0a97f9b0a687d14) — urfoundation/sn
- 2026-10-16 · commit · [Refuse an evidence source census of a package that no command of its …](https://github.com/urfoundation/sn/commit/7787869d34d01a15202c8aa70d2ae2466321c5d9) — urfoundation/sn
- 2026-10-16 · commit · [Give server-db's ./proxy run a 40-minute deadline](https://github.com/urfoundation/sn/commit/eb11f5fcc3a9ed0f110ae4926c2438b173f01be3) — urfoundation/sn
- 2026-10-16 · release · [v2026.10.15-1073121200](https://github.com/urfoundation/sn/releases/tag/v2026.10.15-1073121200) — urfoundation/sn
- 2026-10-15 · release · [v2026.10.15-1073082520](https://github.com/urfoundation/sn/releases/tag/v2026.10.15-1073082520) — urfoundation/sn
- 2026-10-15 · release · [v2026.10.15-1073057980](https://github.com/urfoundation/sn/releases/tag/v2026.10.15-1073057980) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
