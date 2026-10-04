# sn44 — Score ף

Making every camera intelligent

Bittensor subnet **44** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/score-technologies/turbovision) · [url](https://www.wearescore.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.037051 | +0.07% | +2.70% | +8.70% | 226,480 | 67,346 | 6,104 |

## Last 24h flow

981 trades by 749 coldkeys · 796 buys (3,332 τ) / 185 sells (2,588 τ) · net 743.41 τ

## News

- 2026-10-01 · commit · [change cricket weights](https://github.com/score-technologies/turbovision/commit/bef6ad70de458c16bfaa8dfe9361b65f0d42ec3b) — score-technologies/turbovision
- 2026-09-29 · commit · [room iou](https://github.com/score-technologies/turbovision/commit/52201651f2175048a7fbdcf2c1da206667a942d2) — score-technologies/turbovision
- 2026-09-24 · commit · [add retrys set weights+add logs long emits](https://github.com/score-technologies/turbovision/commit/f1defeea7ffdcd9c0c662ecfa7dce711574a348f) — score-technologies/turbovision
- 2026-09-22 · commit · [add logs emission](https://github.com/score-technologies/turbovision/commit/b934ea66bed3cce1f42e12105134f9bba4973c30) — score-technologies/turbovision
- 2026-09-16 · commit · [add detail fail lookup chutes](https://github.com/score-technologies/turbovision/commit/d6c55296e2be449c56ed9738707730e4e300fa65) — score-technologies/turbovision

## Use

```bash
m subnets.sn44/info        # live identity + market (snapshot if bt is down)
m subnets.sn44/news        # scraped news
m subnets.sn44/trades      # 24h alpha tape
m subnets.sn44/daily       # daily candles
python3 orbit/subnets/sn44/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
