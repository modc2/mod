# sn23 — Trishool ψ

Trishool is the AI alignment protocol built on Bittensor

Bittensor subnet **23** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/TrishoolAI/trishool-phase2) · [url](https://trishool.ai) · [discord](https://discord.com/channels/799672011265015819/1437447445176127618)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005244 | +0.11% | -1.53% | -1.77% | 32,390 | 8,278 | 304.29 |

## Last 24h flow

90 trades by 41 coldkeys · 34 buys (120.24 τ) / 56 sells (183.67 τ) · net -63.43 τ

## News

- 2026-10-06 · commit · [Update version to 2.0.32 and modify questions for enhanced safety and…](https://github.com/TrishoolAI/trishool-phase2/commit/b392682a18feedf0eba29a6d9f2a6de53a104db5) — TrishoolAI/trishool-phase2
- 2026-09-30 · commit · [Merge branch 'main' of https://github.com/TrishoolAI/trishool-phase2](https://github.com/TrishoolAI/trishool-phase2/commit/d67f9ce48656d0ba497d4ad33105512f3704f033) — TrishoolAI/trishool-phase2
- 2026-09-30 · commit · [Remove example exploit markdown file due to safety concerns and compl…](https://github.com/TrishoolAI/trishool-phase2/commit/c6a7ef7609537ca6c9fe9f41af8c2d1657f0a43c) — TrishoolAI/trishool-phase2
- 2026-09-29 · commit · [Merge pull request #58 from TrishoolAI/tightening-judge](https://github.com/TrishoolAI/trishool-phase2/commit/a4459bd432f58ba20d924866f79c40745b8c1124) — TrishoolAI/trishool-phase2
- 2026-09-29 · commit · [Update version numbers to 2.0.31 in configuration files and modify qu…](https://github.com/TrishoolAI/trishool-phase2/commit/8056c0214bea4259eca32843bf1488fbb923f5dc) — TrishoolAI/trishool-phase2

## Use

```bash
m subnets.sn23/info        # live identity + market (snapshot if bt is down)
m subnets.sn23/news        # scraped news
m subnets.sn23/trades      # 24h alpha tape
m subnets.sn23/daily       # daily candles
python3 orbit/subnets/sn23/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
