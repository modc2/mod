# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004146 | -0.57% | +0.86% | +9.72% | 24,387 | 5,127 | 2,745 |

## Last 24h flow

387 trades by 111 coldkeys · 221 buys (1,384 τ) / 166 sells (1,360 τ) · net 23.78 τ

## News

- 2026-10-08 · commit · [Preserve Deepline rejections across concurrent settlement (#270)](https://github.com/leadpoet/leadpoet/commit/cded1fdae9bb4c3e97418e8c2ad312c0d6a4e485) — leadpoet/leadpoet
- 2026-10-08 · commit · [Preserve Deepline rejection status and billing across replay (#268)](https://github.com/leadpoet/leadpoet/commit/0323492ab9144cf456ea9aa2fda95fae4839bba6) — leadpoet/leadpoet
- 2026-10-08 · commit · [Merge pull request #269 from leadpoet/codex/arena-stage-creation-time…](https://github.com/leadpoet/leadpoet/commit/affdc8af6b1ea83fd73b16d68c5f59fe96671abc) — leadpoet/leadpoet
- 2026-10-08 · commit · [Bound bulk Arena queue creation with scoped database timeouts](https://github.com/leadpoet/leadpoet/commit/32c08b97895632749b8e3f6bb096990bf65337e8) — leadpoet/leadpoet
- 2026-10-07 · commit · [Merge pull request #267 from leadpoet/codex/arena-admission-ceiling-o…](https://github.com/leadpoet/leadpoet/commit/8df8ad8524b585f1077c6c35cf1f8a91556d98e7) — leadpoet/leadpoet
- 2026-10-07 · commit · [Merge PR #264: preserve locality verdicts and free response recovery](https://github.com/leadpoet/leadpoet/commit/fedb11681fd6c612a7208823592a9dd846340448) — leadpoet/leadpoet
- 2026-10-07 · commit · [Keep historical migration fixtures pinned while updating current flows](https://github.com/leadpoet/leadpoet/commit/372053bfdfab5b795921e59632176b0ccae980e5) — leadpoet/leadpoet
- 2026-10-07 · commit · [Keep service flow fixtures on current billing schema](https://github.com/leadpoet/leadpoet/commit/8a05750542db8f8b7cd78ef2bdd3b5c196a6e6dd) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
