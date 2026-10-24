# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004970 | -0.22% | +2.67% | -1.62% | 29,660 | 5,613 | 2,571 |

## Last 24h flow

317 trades by 113 coldkeys · 166 buys (1,323 τ) / 151 sells (1,246 τ) · net 76.36 τ

## News

- 2026-10-20 · commit · [Merge pull request #579 from leadpoet/fix/arena-closed-cost-round-mem…](https://github.com/leadpoet/leadpoet/commit/e8f6063e2a188bc5217f39740be3be9a423f48bc) — leadpoet/leadpoet
- 2026-10-20 · commit · [Merge remote-tracking branch 'origin/main' into fix/arena-closed-cost…](https://github.com/leadpoet/leadpoet/commit/503ed579005e7ca6dad60efc0bd4255c21963683) — leadpoet/leadpoet
- 2026-10-20 · commit · [Merge pull request #578 from leadpoet/fix/arena-public-result-round-c…](https://github.com/leadpoet/leadpoet/commit/394b7fb816071c903738a9d828bfbad3e932670e) — leadpoet/leadpoet
- 2026-10-20 · commit · [Merge remote-tracking branch 'origin/main' into fix/arena-public-resu…](https://github.com/leadpoet/leadpoet/commit/f38121a1f3984b19f71d619968016d22feceea41) — leadpoet/leadpoet
- 2026-10-19 · commit · [Merge pull request #562 from leadpoet/fix/gateway-chain-startup-recov…](https://github.com/leadpoet/leadpoet/commit/c26a116c244f4b4894e7f77d61b03ebde2c4969a) — leadpoet/leadpoet
- 2026-10-19 · commit · [Bind reviewed chain startup recovery to protected source](https://github.com/leadpoet/leadpoet/commit/9360cc74fdc1cf6f81c75b6acc2147fa710abb20) — leadpoet/leadpoet
- 2026-10-19 · commit · [Recover transient gateway chain startup failures within deadline](https://github.com/leadpoet/leadpoet/commit/fe79289ebf25941a1e5bf8c2c1f8a91f1fc200ad) — leadpoet/leadpoet
- 2026-10-19 · commit · [Merge pull request #561 from leadpoet/fix/arena-linkedin-actor-normal…](https://github.com/leadpoet/leadpoet/commit/e02a73c7343ddbe9475a7b0654c1bf244ab07901) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
