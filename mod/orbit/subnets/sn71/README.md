# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004111 | +0.25% | +11.07% | +6.86% | 24,150 | 5,105 | 4,266 |

## Last 24h flow

418 trades by 108 coldkeys · 246 buys (2,264 τ) / 172 sells (2,000 τ) · net 263.65 τ

## News

- 2026-10-07 · commit · [Merge PR #247: clarify paid subscription evidence](https://github.com/leadpoet/leadpoet/commit/4148ec5215c76a399e85822c0bcc7b96d9887cf8) — leadpoet/leadpoet
- 2026-10-07 · commit · [Clarify supported paid subscription evidence in company review](https://github.com/leadpoet/leadpoet/commit/3878031f28a65a4a38d049a81bc71b4816148f2e) — leadpoet/leadpoet
- 2026-10-07 · commit · [Merge PR #246: prefer fresh verification evidence](https://github.com/leadpoet/leadpoet/commit/c5e5c2e08047f657e7bc94dac0d2f1c8d86a7b14) — leadpoet/leadpoet
- 2026-10-07 · commit · [Prefer fresh investigator source over cached priority marker](https://github.com/leadpoet/leadpoet/commit/f412c54201706ee0e7be92307c8530cec6ee8d76) — leadpoet/leadpoet
- 2026-10-07 · commit · [Merge PR #244: isolate repeated provider refusals](https://github.com/leadpoet/leadpoet/commit/62d4afe5ffade155aefd5b636a2d0497b9225a09) — leadpoet/leadpoet
- 2026-10-06 · commit · [Merge PR #238: retain validated company evidence](https://github.com/leadpoet/leadpoet/commit/ab6b32347e2a3d4bcfa48975b2ee4bb14b6b9fc6) — leadpoet/leadpoet
- 2026-10-06 · commit · [Bind reviewed profile evidence fix in protected manifest](https://github.com/leadpoet/leadpoet/commit/63b79d3b01b8095a4324596d5caf84b9a6545ba2) — leadpoet/leadpoet
- 2026-10-06 · commit · [Bind adjacent quote-card prices to their exact issuer](https://github.com/leadpoet/leadpoet/commit/04a30acce9e3ca45a124f0d2daa964031e88fe93) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
