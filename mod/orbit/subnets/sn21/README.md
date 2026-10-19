# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004310 | — | -5.85% | -15.22% | 26,855 | 8,488 | 596.10 |

## Last 24h flow

85 trades by 52 coldkeys · 26 buys (168.30 τ) / 59 sells (427.15 τ) · net -258.85 τ

## News

- 2026-10-19 · commit · [docs(privacy): the planned rule keeps an earning model closed to all …](https://github.com/ippcteam/SN21-adtao/commit/3142e6bbbcfc8847809ea93a2133b4d64482e06d) — ippcteam/SN21-adtao
- 2026-10-19 · commit · [feat(registry): a hotkey whose new model waits or was rejected keeps …](https://github.com/ippcteam/SN21-adtao/commit/bf655959f3500b4a0bc228ad8d198f012f05f6a2) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(executor): unpacked images are removed even with read-only direct…](https://github.com/ippcteam/SN21-adtao/commit/c4575db4fbf14e1287798d9d6cf7e958931e5d25) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scripts): the curve-tail preview removes its ledger copies when i…](https://github.com/ippcteam/SN21-adtao/commit/a9f0ba3095672741c188b5c218d9bed856869bfb) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [ops(executor): each run reports models that failed on our side and th…](https://github.com/ippcteam/SN21-adtao/commit/a7115e071fa781dd732f95ccb33fa71baa5a12df) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [feat(scoring): absence charges stop once a hotkey holds no real entry…](https://github.com/ippcteam/SN21-adtao/commit/b22ce9e9dafa1f2360704aaea5babd8b97abe1e9) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scoring): a result with no conversions or efficiency value is ski…](https://github.com/ippcteam/SN21-adtao/commit/a48209c7ac293c628a23148c98d88d4ee53c7c98) — ippcteam/SN21-adtao
- 2026-10-17 · commit · [fix(scoring): free the standing entries after type accuracy and after…](https://github.com/ippcteam/SN21-adtao/commit/4c513538a4fd11ecc514c3d1494356a18eb68c26) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
