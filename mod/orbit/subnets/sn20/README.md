# sn20 — Witness υ

Decentralized models to understand video

Bittensor subnet **20** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/witnessvision/witness_subnet/) · [url](https://witnessvision.io/) · [discord](https://discord.gg/P2Y93BRYyC)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002289 | -0.00% | +0.77% | +0.66% | 13,862 | 6,404 | 27.11 |

## Last 24h flow

9 trades by 9 coldkeys · 5 buys (25.41 τ) / 4 sells (0.62 τ) · net 24.79 τ

## News

- 2026-10-02 · commit · [Wait for active model prefetch before compression preflight](https://github.com/witnessvision/witness_subnet/commit/4770712a5bd37a50377715a0a2973f7f4a80b8bf) — witnessvision/witness_subnet
- 2026-10-02 · commit · [Add private rotating model access audit logs and separate telemetry m…](https://github.com/witnessvision/witness_subnet/commit/fc170dce8a9918e110aee263ee7299687b93819b) — witnessvision/witness_subnet
- 2026-10-02 · commit · [Use Luna for mainnet judging and visual review from window 36](https://github.com/witnessvision/witness_subnet/commit/03e23e1c8b28485c53f94cd4736ee0f9ae2aa71c) — witnessvision/witness_subnet
- 2026-10-01 · commit · [Document required SALMONN FP8 loading dependency](https://github.com/witnessvision/witness_subnet/commit/8bd7bdf9d0626ff18027a5499f1597835f1eb379) — witnessvision/witness_subnet
- 2026-10-01 · commit · [Execute supported FP8 linears natively from window 31](https://github.com/witnessvision/witness_subnet/commit/0bb9d645858d13327426a20016d35839a40ce1ba) — witnessvision/witness_subnet
- 2026-10-01 · commit · [Reset hotkey admissions once at window 32 while preserving consensus …](https://github.com/witnessvision/witness_subnet/commit/6d4f19d4924be5c60ef458a9941f41568cdee6e4) — witnessvision/witness_subnet
- 2026-10-01 · commit · [Export signed closed-window scores without serving media or model out…](https://github.com/witnessvision/witness_subnet/commit/34990757470432a073cb76c25ed8f049e988a0c6) — witnessvision/witness_subnet
- 2026-10-01 · commit · [Keep weight submission time separate from reveal observation](https://github.com/witnessvision/witness_subnet/commit/9fe23760e68ed5e15a47673e5482d6c5b2b95280) — witnessvision/witness_subnet

## Use

```bash
m subnets.sn20/info        # live identity + market (snapshot if bt is down)
m subnets.sn20/news        # scraped news
m subnets.sn20/trades      # 24h alpha tape
m subnets.sn20/daily       # daily candles
python3 orbit/subnets/sn20/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
