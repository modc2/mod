# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.093622 | +0.03% | -0.33% | +0.03% | 567,843 | 164,231 | 3,005 |

## Last 24h flow

339 trades by 184 coldkeys · 133 buys (1,011 τ) / 206 sells (1,608 τ) · net -597.36 τ

## News

- 2026-10-05 · release · [executor-v1.137](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.137) — Datura-ai/lium-io
- 2026-10-05 · commit · [NO-TICKET - [P1] verifyx: vendor libverifyx.so from celium-gpu-verifi…](https://github.com/Datura-ai/lium-io/commit/2b4dc97bf014e9a3944420b72c2a4a8b5c3c0d49) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3980 - validator: connector reads the chain off its event loop an…](https://github.com/Datura-ai/lium-io/commit/2535a5af297a53ab038af4a46c52b162367f8a44) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3980 - validator: port mapping works on a copy of the preferred p…](https://github.com/Datura-ai/lium-io/commit/c2b652889cb202e3d00b13ba5367a05c51b9abf2) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3803 - [P1] VerifyX: keep the upload EMA when the Cloudflare prob…](https://github.com/Datura-ai/lium-io/commit/7684a2490637ba6a88399d9f50576b003b87453b) — Datura-ai/lium-io
- 2026-10-05 · commit · [DAH-3887 - [P1] Docker Hub login by OIDC, no static token (Guard 3) (…](https://github.com/Datura-ai/lium-io/commit/e8eef9be981b14923d829ff2b2ad160068addd10) — Datura-ai/lium-io
- 2026-10-05 · commit · [NO-TICKET - [P2] Validator scrape: record the host's Sysbox version (…](https://github.com/Datura-ai/lium-io/commit/95cabc382818f6e0cf8f6e9835ee3698cd71510e) — Datura-ai/lium-io
- 2026-10-05 · commit · [NO-TICKET - [P1] validator: accept GB300 and pay it idle at the B300 …](https://github.com/Datura-ai/lium-io/commit/2faaffe3ffcba2a082cd62244cde4e24575d7529) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
