# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004116 | -0.00% | -4.53% | -22.86% | 25,680 | 8,297 | 2,019 |

## Last 24h flow

180 trades by 90 coldkeys · 81 buys (957.43 τ) / 99 sells (1,104 τ) · net -146.71 τ

## News

- 2026-10-19 · commit · [feat(scoring): the pooled count score can be published before the cou…](https://github.com/ippcteam/SN21-adtao/commit/5be3c8faec1aedfe86ae2082ba3e55fa9a0f9254) — ippcteam/SN21-adtao
- 2026-10-19 · commit · [feat(scoring): conversions on small campaigns can be scored as a coun…](https://github.com/ippcteam/SN21-adtao/commit/935a8677a45e0ef2f641615887ead26a4372b4c9) — ippcteam/SN21-adtao
- 2026-10-19 · commit · [feat(executor): the scored run can wait for the basket's change descr…](https://github.com/ippcteam/SN21-adtao/commit/e2d4c12c7bcc472840240e484653ea22d8a51af3) — ippcteam/SN21-adtao
- 2026-10-19 · commit · [docs(privacy): the planned rule keeps an earning model closed to all …](https://github.com/ippcteam/SN21-adtao/commit/3142e6bbbcfc8847809ea93a2133b4d64482e06d) — ippcteam/SN21-adtao
- 2026-10-19 · commit · [feat(registry): a hotkey whose new model waits or was rejected keeps …](https://github.com/ippcteam/SN21-adtao/commit/bf655959f3500b4a0bc228ad8d198f012f05f6a2) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(executor): unpacked images are removed even with read-only direct…](https://github.com/ippcteam/SN21-adtao/commit/c4575db4fbf14e1287798d9d6cf7e958931e5d25) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scripts): the curve-tail preview removes its ledger copies when i…](https://github.com/ippcteam/SN21-adtao/commit/a9f0ba3095672741c188b5c218d9bed856869bfb) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [ops(executor): each run reports models that failed on our side and th…](https://github.com/ippcteam/SN21-adtao/commit/a7115e071fa781dd732f95ccb33fa71baa5a12df) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
