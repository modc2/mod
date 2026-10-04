# sn100 — Cortex დ

The open alternative to frontier AI | Efficient autonomous research

Bittensor subnet **100** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/CortexLM/cortex) · [url](https://network.cortex.foundation)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003893 | +0.00% | +3.20% | +3.49% | 10,440 | 2,507 | 516.18 |

## Last 24h flow

120 trades by 57 coldkeys · 56 buys (277.68 τ) / 64 sells (236.95 τ) · net 40.73 τ

## News

- 2026-09-28 · commit · [feat(master): send the completed epoch's chain time to challenges (#315)](https://github.com/CortexLM/cortex/commit/d738424c09213af3634b24a1ba1a0fddb7a9379d) — CortexLM/cortex
- 2026-09-25 · commit · [test(network): verify opentype 75/25 sealed payouts (#314)](https://github.com/CortexLM/cortex/commit/540b4fe48cc65e6d0e1645f00f9de5a00ba394e7) — CortexLM/cortex
- 2026-09-25 · commit · [fix: bring production hotfixes into main; algorithm 3 empty-epoch bur…](https://github.com/CortexLM/cortex/commit/163f814698d1c3db0e0d98e1c5020db86607dd93) — CortexLM/cortex
- 2026-09-24 · commit · [feat(challenges): load docker challenges and move bounty out (#312)](https://github.com/CortexLM/cortex/commit/6c6d72b15c45b35c29428abb7ef87a509212ec85) — CortexLM/cortex
- 2026-09-21 · commit · [fix(hooks): isolate fixture git context before push (#308)](https://github.com/CortexLM/cortex/commit/89b9760067fbc444cc387cc595bcd51709e05179) — CortexLM/cortex
- 2026-09-10 · release · [ctx CLI v3.3.31](https://github.com/CortexLM/cortex/releases/tag/v3.3.31) — CortexLM/cortex
- 2026-09-09 · release · [ctx CLI v3.3.30](https://github.com/CortexLM/cortex/releases/tag/v3.3.30) — CortexLM/cortex
- 2026-09-09 · release · [v0.2.0: feat(proof): continuous leaf emitter with ChallengeInternal cover (#256)](https://github.com/CortexLM/cortex/releases/tag/v0.2.0) — CortexLM/cortex

## Use

```bash
m subnets.sn100/info        # live identity + market (snapshot if bt is down)
m subnets.sn100/news        # scraped news
m subnets.sn100/trades      # 24h alpha tape
m subnets.sn100/daily       # daily candles
python3 orbit/subnets/sn100/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
