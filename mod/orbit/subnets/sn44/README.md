# sn44 — Score ף

Making every camera intelligent

Bittensor subnet **44** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/score-technologies/turbovision) · [url](https://www.wearescore.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.034078 | -0.71% | -2.50% | -5.60% | 211,642 | 65,000 | 5,512 |

## Last 24h flow

333 trades by 161 coldkeys · 135 buys (2,197 τ) / 198 sells (3,164 τ) · net -967.29 τ

## News

- 2026-10-16 · commit · [update index once](https://github.com/score-technologies/turbovision/commit/d56cb8279444897bc3d60b2c7e02106e23a4a56a) — score-technologies/turbovision
- 2026-10-15 · commit · [fix bytes read index](https://github.com/score-technologies/turbovision/commit/5432c1898dc61f9bfad7bfef9573a6405cc105c2) — score-technologies/turbovision
- 2026-10-14 · commit · [add cricket gt path](https://github.com/score-technologies/turbovision/commit/215a2de5a14cc0ab0c2f109540b816ef5a783db1) — score-technologies/turbovision
- 2026-10-12 · commit · [add blacklist](https://github.com/score-technologies/turbovision/commit/6408194c0e4b1222af810fa41cabd9e2c352ed96) — score-technologies/turbovision
- 2026-10-01 · commit · [change cricket weights](https://github.com/score-technologies/turbovision/commit/bef6ad70de458c16bfaa8dfe9361b65f0d42ec3b) — score-technologies/turbovision
- 2026-09-29 · commit · [room iou](https://github.com/score-technologies/turbovision/commit/52201651f2175048a7fbdcf2c1da206667a942d2) — score-technologies/turbovision
- 2026-09-24 · commit · [add retrys set weights+add logs long emits](https://github.com/score-technologies/turbovision/commit/f1defeea7ffdcd9c0c662ecfa7dce711574a348f) — score-technologies/turbovision
- 2026-09-22 · commit · [add logs emission](https://github.com/score-technologies/turbovision/commit/b934ea66bed3cce1f42e12105134f9bba4973c30) — score-technologies/turbovision

## Use

```bash
m subnets.sn44/info        # live identity + market (snapshot if bt is down)
m subnets.sn44/news        # scraped news
m subnets.sn44/trades      # 24h alpha tape
m subnets.sn44/daily       # daily candles
python3 orbit/subnets/sn44/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
