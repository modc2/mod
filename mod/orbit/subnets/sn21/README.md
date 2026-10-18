# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004559 | -0.00% | -3.91% | -8.28% | 28,374 | 8,726 | 1,237 |

## Last 24h flow

131 trades by 78 coldkeys · 43 buys (530.67 τ) / 88 sells (705.30 τ) · net -174.63 τ

## News

- 2026-10-17 · commit · [fix(executor): unpacked images are removed even with read-only direct…](https://github.com/ippcteam/SN21-adtao/commit/c4575db4fbf14e1287798d9d6cf7e958931e5d25) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scripts): the curve-tail preview removes its ledger copies when i…](https://github.com/ippcteam/SN21-adtao/commit/a9f0ba3095672741c188b5c218d9bed856869bfb) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [ops(executor): each run reports models that failed on our side and th…](https://github.com/ippcteam/SN21-adtao/commit/a7115e071fa781dd732f95ccb33fa71baa5a12df) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [feat(scoring): absence charges stop once a hotkey holds no real entry…](https://github.com/ippcteam/SN21-adtao/commit/b22ce9e9dafa1f2360704aaea5babd8b97abe1e9) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scoring): a result with no conversions or efficiency value is ski…](https://github.com/ippcteam/SN21-adtao/commit/a48209c7ac293c628a23148c98d88d4ee53c7c98) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scoring): free the standing entries after type accuracy and after…](https://github.com/ippcteam/SN21-adtao/commit/4c513538a4fd11ecc514c3d1494356a18eb68c26) — ippcteam/SN21-adtao
- 2026-10-16 · commit · [feat(privacy): model release step, release copy, and pulls with an op…](https://github.com/ippcteam/SN21-adtao/commit/96386711ef07374d9bb1a7f743a0aec25b7f5225) — ippcteam/SN21-adtao
- 2026-10-16 · commit · [daily loop: log memory after each step](https://github.com/ippcteam/SN21-adtao/commit/808bc224a68f8e1e6e0454fe8759d550ac5d52d2) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
