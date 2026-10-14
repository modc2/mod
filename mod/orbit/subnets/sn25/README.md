# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005830 | -0.00% | -10.13% | -14.88% | 36,002 | 12,382 | 4,214 |

## Last 24h flow

210 trades by 65 coldkeys · 113 buys (1,764 τ) / 97 sells (2,444 τ) · net -680.06 τ

## News

- 2026-10-14 · commit · [Bring the handoff note to the deposit floors, the apps' gas hints and…](https://github.com/urfoundation/sn/commit/37364436d18ddb15191050d4e88929ad1010480a) — urfoundation/sn
- 2026-10-14 · commit · [Select the deposit-key gas-floor and pending-reservation tests in the…](https://github.com/urfoundation/sn/commit/839c906b473df52023a16a0337c9b03486065032) — urfoundation/sn
- 2026-10-14 · commit · [Bring the runbook and the handoff note to the dual-stack hops, the st…](https://github.com/urfoundation/sn/commit/8381bb598db75bc16ceee14f8115d2e5911734f2) — urfoundation/sn
- 2026-10-14 · commit · [Merge v2/dual-stack-hops into main](https://github.com/urfoundation/sn/commit/00e383fe2dd4805be897ff03f1aea5631ecc10f6) — urfoundation/sn
- 2026-10-14 · release · [v2026.10.13-1071380620](https://github.com/urfoundation/sn/releases/tag/v2026.10.13-1071380620) — urfoundation/sn
- 2026-10-14 · commit · [Merge v2/gate-selection into main](https://github.com/urfoundation/sn/commit/6caf96674e4656933568c4437acc02526b228e13) — urfoundation/sn
- 2026-10-13 · commit · [Sign the printed escrow envelope and register the pool through regist…](https://github.com/urfoundation/sn/commit/188c15471dff763ce974b4ed68469cb494c9dd71) — urfoundation/sn
- 2026-10-13 · commit · [Merge v2/launch-tools into main](https://github.com/urfoundation/sn/commit/c7ab626006f75692b49b4b603d248247a05ba704) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
