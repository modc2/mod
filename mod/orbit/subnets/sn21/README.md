# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004610 | +0.91% | +22.55% | +33.35% | 28,322 | 8,732 | 6,237 |

## Last 24h flow

519 trades by 153 coldkeys · 288 buys (3,541 τ) / 231 sells (2,694 τ) · net 847.26 τ

## News

- 2026-10-06 · commit · [fix(validator): /health in daily mode no longer echoes the loaded rel…](https://github.com/ippcteam/SN21-adtao/commit/67bac47190ffd16145733b703437017699dbf872) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): weekly prediction submissions are off unless SN21_WEE…](https://github.com/ippcteam/SN21-adtao/commit/0977912ce07fe4378e165feadd8e80dff6dfbaa6) — ippcteam/SN21-adtao
- 2026-10-06 · commit · [fix(validator): /health reports daily mode once the weekly window has…](https://github.com/ippcteam/SN21-adtao/commit/afac3d0454cc56244a183f113333e35a35a3be42) — ippcteam/SN21-adtao
- 2026-10-05 · commit · [fix(validator): the reg-index staleness alarm follows the head refresh](https://github.com/ippcteam/SN21-adtao/commit/b0bc70889e3351780e614a74d81ecee325213c63) — ippcteam/SN21-adtao
- 2026-10-02 · commit · [Revert "ci: post commits to the project board"](https://github.com/ippcteam/SN21-adtao/commit/305b9941e070d8f597e9701ed3160e53329c3404) — ippcteam/SN21-adtao
- 2026-10-02 · commit · [ci: post commits to the project board](https://github.com/ippcteam/SN21-adtao/commit/e9caaf75729d435d035830b01117fbe5c1c1dae9) — ippcteam/SN21-adtao
- 2026-09-24 · release · [SN21 rich training data v3 (slice 2)](https://github.com/ippcteam/SN21-adtao/releases/tag/training-v3-2026-09) — ippcteam/SN21-adtao
- 2026-09-24 · commit · [docs: training data v3 slice (windows 5-14 Aug) and the slice release…](https://github.com/ippcteam/SN21-adtao/commit/476b4f80439455680c1a75746ab252c6cb7a0b61) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
