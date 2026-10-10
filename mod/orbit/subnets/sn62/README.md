# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009446 | +0.00% | +0.26% | -9.09% | 53,816 | 28,352 | 151.55 |

## Last 24h flow

184 trades by 92 coldkeys · 132 buys (75.88 τ) / 52 sells (56.14 τ) · net 19.74 τ

## News

- 2026-10-09 · release · [v0.3.11](https://github.com/ridgesai/ridges/releases/tag/v0.3.11) — ridgesai/ridges
- 2026-10-09 · commit · [Merge pull request #522 from ridgesai/feat/validator-scheduling](https://github.com/ridgesai/ridges/commit/55d67a36c46631168054833105eaa402d2b04e4e) — ridgesai/ridges
- 2026-10-09 · commit · [core scheduling queries](https://github.com/ridgesai/ridges/commit/ccc2e72f447dab1a063543958aed34161281f555) — ridgesai/ridges
- 2026-10-09 · commit · [new admin events](https://github.com/ridgesai/ridges/commit/539d893e498d495a78a14608eb9f98debb7a0dfc) — ridgesai/ridges
- 2026-10-09 · commit · [add new admin endpoints](https://github.com/ridgesai/ridges/commit/659de2dd2ccd734133b885dd42c4c34d609cfacb) — ridgesai/ridges
- 2026-10-09 · release · [v0.3.10](https://github.com/ridgesai/ridges/releases/tag/v0.3.10) — ridgesai/ridges
- 2026-10-08 · commit · [Merge pull request #520 from ridgesai/feat/dynamic-submission-pricing](https://github.com/ridgesai/ridges/commit/75740b3aaafd714a39e1c25cbd84641152bb6244) — ridgesai/ridges
- 2026-10-08 · commit · [remove quote restriction](https://github.com/ridgesai/ridges/commit/1af4acb58fcdc425a6526b834e441cd192a3c35a) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
