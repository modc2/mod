# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004662 | -0.27% | -7.17% | -3.33% | 28,979 | 8,821 | 3,620 |

## Last 24h flow

262 trades by 117 coldkeys · 112 buys (1,643 τ) / 150 sells (1,975 τ) · net -331.93 τ

## News

- 2026-10-16 · commit · [feat(privacy): model release step, release copy, and pulls with an op…](https://github.com/ippcteam/SN21-adtao/commit/96386711ef07374d9bb1a7f743a0aec25b7f5225) — ippcteam/SN21-adtao
- 2026-10-16 · commit · [daily loop: log memory after each step](https://github.com/ippcteam/SN21-adtao/commit/808bc224a68f8e1e6e0454fe8759d550ac5d52d2) — ippcteam/SN21-adtao
- 2026-10-16 · commit · [scoring: an entry without a measurable efficiency is scored on cost a…](https://github.com/ippcteam/SN21-adtao/commit/5cd7bd9465501c98eb7e00595b4aae40ed0d0269) — ippcteam/SN21-adtao
- 2026-10-15 · commit · [reporting: group shared negative list changes with negative keywords](https://github.com/ippcteam/SN21-adtao/commit/4b56ee1290d3b2eeac8ab9e9a564eb12e970ed14) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [perf(scoring): read receipts through a small per-day standing index](https://github.com/ippcteam/SN21-adtao/commit/8aec3c862d9b09df919649f86eddbad4e4629b10) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [feat(executor): the current champion's forecast for what-if episodes,…](https://github.com/ippcteam/SN21-adtao/commit/68f20ee4f35b4d3a6d3ac15d6afe879a29e3de06) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [feat(privacy): closed and released model digests as a state machine (…](https://github.com/ippcteam/SN21-adtao/commit/f1bc01296981adf98f178ab1085956f8411cbcac) — ippcteam/SN21-adtao
- 2026-10-14 · commit · [fix(publication): the performance page waits up to 5 minutes for its …](https://github.com/ippcteam/SN21-adtao/commit/55f096802b7fba55556df3c6dd804cb04d44916d) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
