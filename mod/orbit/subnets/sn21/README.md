# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004704 | -0.00% | -5.93% | +2.05% | 29,167 | 8,851 | 4,521 |

## Last 24h flow

388 trades by 129 coldkeys · 182 buys (2,123 τ) / 206 sells (2,396 τ) · net -273.10 τ

## News

- 2026-10-14 · commit · [perf(scoring): read receipts through a small per-day standing index](https://github.com/ippcteam/SN21-adtao/commit/8aec3c862d9b09df919649f86eddbad4e4629b10) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [feat(executor): the current champion's forecast for what-if episodes,…](https://github.com/ippcteam/SN21-adtao/commit/68f20ee4f35b4d3a6d3ac15d6afe879a29e3de06) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [feat(privacy): closed and released model digests as a state machine (…](https://github.com/ippcteam/SN21-adtao/commit/f1bc01296981adf98f178ab1085956f8411cbcac) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [fix(publication): the performance page waits up to 5 minutes for its …](https://github.com/ippcteam/SN21-adtao/commit/55f096802b7fba55556df3c6dd804cb04d44916d) — ippcteam/SN21-adtao
- 2026-10-13 · commit · [feat(reporting): performance by change type in more families; combine…](https://github.com/ippcteam/SN21-adtao/commit/128f7d9ad70474f513a7cb51184241e29309a523) — ippcteam/SN21-adtao
- 2026-10-13 · commit · [fix(scoring): the configured goal is ranked high, medium, low, then n…](https://github.com/ippcteam/SN21-adtao/commit/3fd7b123f7b846ebb2f6ad38ecc76e861daa2c7b) — ippcteam/SN21-adtao
- 2026-10-13 · commit · [docs(rewards): every episode lists the 28-day horizon from the basket…](https://github.com/ippcteam/SN21-adtao/commit/9736e6512b8de37a9dd635333431a6c029b653ce) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [docs(training): from the next slice, account and campaign codes are n…](https://github.com/ippcteam/SN21-adtao/commit/43c698a0f610a87f483b3fc6e43940b28d920739) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
