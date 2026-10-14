# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004990 | +0.10% | -6.47% | +32.65% | 30,898 | 9,111 | 3,818 |

## Last 24h flow

293 trades by 125 coldkeys · 134 buys (1,764 τ) / 159 sells (2,060 τ) · net -296.86 τ

## News

- 2026-10-13 · commit · [feat(reporting): performance by change type in more families; combine…](https://github.com/ippcteam/SN21-adtao/commit/128f7d9ad70474f513a7cb51184241e29309a523) — ippcteam/SN21-adtao
- 2026-10-13 · commit · [fix(scoring): the configured goal is ranked high, medium, low, then n…](https://github.com/ippcteam/SN21-adtao/commit/3fd7b123f7b846ebb2f6ad38ecc76e861daa2c7b) — ippcteam/SN21-adtao
- 2026-10-13 · commit · [docs(rewards): every episode lists the 28-day horizon from the basket…](https://github.com/ippcteam/SN21-adtao/commit/9736e6512b8de37a9dd635333431a6c029b653ce) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [docs(training): from the next slice, account and campaign codes are n…](https://github.com/ippcteam/SN21-adtao/commit/43c698a0f610a87f483b3fc6e43940b28d920739) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [ops(executor): each memory line also shows the container split into p…](https://github.com/ippcteam/SN21-adtao/commit/d0fad2db190b8a67f4e7ef2698f4814e32d3f0ea) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [fix(validator): the validator site sends daily-results requests to th…](https://github.com/ippcteam/SN21-adtao/commit/8398c350839f067498b213de27641ce1277ebee4) — ippcteam/SN21-adtao
- 2026-10-11 · commit · [docs(verifying): the standing preview exists only during a notice per…](https://github.com/ippcteam/SN21-adtao/commit/35e33379210974a7d5d9e65de7a3c5a017a54eb1) — ippcteam/SN21-adtao
- 2026-10-11 · commit · [docs(privacy): say how model images are pulled today; validator-only …](https://github.com/ippcteam/SN21-adtao/commit/4651d3a7277368fda165e193a0ed67480d42b1fe) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
