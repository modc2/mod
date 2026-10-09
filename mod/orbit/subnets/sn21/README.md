# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004748 | +1.71% | +3.00% | +36.04% | 29,209 | 8,865 | 4,258 |

## Last 24h flow

490 trades by 215 coldkeys · 264 buys (2,195 τ) / 226 sells (2,062 τ) · net 132.41 τ

## News

- 2026-10-08 · commit · [fix(scoring): copy groups agree on one earner, the earliest submission](https://github.com/ippcteam/SN21-adtao/commit/662fad784e2214fe21408db1aee2898a54c9e10c) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): /health in daily mode no longer echoes the loaded rel…](https://github.com/ippcteam/SN21-adtao/commit/67bac47190ffd16145733b703437017699dbf872) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): weekly prediction submissions are off unless SN21_WEE…](https://github.com/ippcteam/SN21-adtao/commit/0977912ce07fe4378e165feadd8e80dff6dfbaa6) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): /health reports daily mode once the weekly window has…](https://github.com/ippcteam/SN21-adtao/commit/afac3d0454cc56244a183f113333e35a35a3be42) — ippcteam/SN21-adtao
- 2026-10-05 · commit · [fix(validator): the reg-index staleness alarm follows the head refresh](https://github.com/ippcteam/SN21-adtao/commit/b0bc70889e3351780e614a74d81ecee325213c63) — ippcteam/SN21-adtao
- 2026-10-02 · commit · [Revert "ci: post commits to the project board"](https://github.com/ippcteam/SN21-adtao/commit/305b9941e070d8f597e9701ed3160e53329c3404) — ippcteam/SN21-adtao
- 2026-10-02 · commit · [ci: post commits to the project board](https://github.com/ippcteam/SN21-adtao/commit/e9caaf75729d435d035830b01117fbe5c1c1dae9) — ippcteam/SN21-adtao
- 2026-09-24 · release · [SN21 rich training data v3 (slice 2)](https://github.com/ippcteam/SN21-adtao/releases/tag/training-v3-2026-09) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
