# sn8 — Vanta θ

The first decentralized & trustless liquidity and execution engine for prop firms and traders

Bittensor subnet **8** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/taoshidev/vanta-network) · [url](https://www.vantanetwork.io/) · discord `tl_arrash`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.030677 | -0.08% | +3.55% | +4.50% | 194,113 | 84,225 | 5,230 |

## Last 24h flow

272 trades by 146 coldkeys · 148 buys (3,236 τ) / 124 sells (1,874 τ) · net 1,362 τ

## News

- 2026-10-15 · commit · [Lazy check unrealized pnl on payout (#958)](https://github.com/taoshidev/vanta-network/commit/4b630b1d7126241be1ac5523a5bdbce2410a1706) — taoshidev/vanta-network
- 2026-10-14 · commit · [Merge pull request #955 from taoshidev/development](https://github.com/taoshidev/vanta-network/commit/80a7331857f89facaa4e62375548cc0116c2249e) — taoshidev/vanta-network
- 2026-10-14 · commit · [fix bugs update docs](https://github.com/taoshidev/vanta-network/commit/f925f14e481ef07e7bee9233e647045730a7c36c) — taoshidev/vanta-network
- 2026-10-14 · commit · [allow order type patch (#957)](https://github.com/taoshidev/vanta-network/commit/e3db33d464a153f8ac4cace540839faf65e2b8cc) — taoshidev/vanta-network
- 2026-10-14 · commit · [fix: Adds better state shutdown handling, fixes rest broken pipe erro…](https://github.com/taoshidev/vanta-network/commit/d8ef8025e73e77226aafafd29310a3f9ad4c9b8d) — taoshidev/vanta-network
- 2026-10-09 · commit · [Fix Restore (#953)](https://github.com/taoshidev/vanta-network/commit/fbd8d19a3e3425d4c3b2b6423fd83186706dd095) — taoshidev/vanta-network
- 2026-10-09 · commit · [Sync price updates and challenge loop (#954)](https://github.com/taoshidev/vanta-network/commit/52756b4e103c1059c3ff26ae2853a77754d74e54) — taoshidev/vanta-network
- 2026-10-05 · commit · [account size endpoint added (#946)](https://github.com/taoshidev/vanta-network/commit/618d5e43f059a4dd08eee67275db0e20eb275837) — taoshidev/vanta-network

## Use

```bash
m subnets.sn8/info        # live identity + market (snapshot if bt is down)
m subnets.sn8/news        # scraped news
m subnets.sn8/trades      # 24h alpha tape
m subnets.sn8/daily       # daily candles
python3 orbit/subnets/sn8/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
