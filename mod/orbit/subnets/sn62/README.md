# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008629 | +0.00% | -0.39% | -8.66% | 49,653 | 27,162 | 180.01 |

## Last 24h flow

225 trades by 77 coldkeys · 134 buys (49.50 τ) / 91 sells (108.80 τ) · net -59.31 τ

## News

- 2026-10-13 · commit · [Merge pull request #521 from ridgesai/docs/agent-mining-llms](https://github.com/ridgesai/ridges/commit/801d21ed80b6fafce4d0c3039dba3fba3c732ae5) — ridgesai/ridges
- 2026-10-09 · release · [v0.3.11](https://github.com/ridgesai/ridges/releases/tag/v0.3.11) — ridgesai/ridges
- 2026-10-09 · commit · [Merge pull request #522 from ridgesai/feat/validator-scheduling](https://github.com/ridgesai/ridges/commit/55d67a36c46631168054833105eaa402d2b04e4e) — ridgesai/ridges
- 2026-10-09 · commit · [core scheduling queries](https://github.com/ridgesai/ridges/commit/ccc2e72f447dab1a063543958aed34161281f555) — ridgesai/ridges
- 2026-10-09 · commit · [new admin events](https://github.com/ridgesai/ridges/commit/539d893e498d495a78a14608eb9f98debb7a0dfc) — ridgesai/ridges
- 2026-10-09 · commit · [add new admin endpoints](https://github.com/ridgesai/ridges/commit/659de2dd2ccd734133b885dd42c4c34d609cfacb) — ridgesai/ridges
- 2026-10-09 · release · [v0.3.10](https://github.com/ridgesai/ridges/releases/tag/v0.3.10) — ridgesai/ridges
- 2026-10-08 · commit · [Merge pull request #520 from ridgesai/feat/dynamic-submission-pricing](https://github.com/ridgesai/ridges/commit/75740b3aaafd714a39e1c25cbd84641152bb6244) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
