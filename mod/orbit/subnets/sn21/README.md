# sn21 — AdTAO φ

Counterfactual impact prediction for advertising interventions

Bittensor subnet **21** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/ippcteam/SN21-adtao) · [url](https://adtao.io)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005335 | +0.16% | +4.95% | +48.19% | 32,993 | 9,416 | 1,123 |

## Last 24h flow

166 trades by 91 coldkeys · 91 buys (673.76 τ) / 75 sells (448.19 τ) · net 225.57 τ

## News

- 2026-10-12 · commit · [docs(training): from the next slice, account and campaign codes are n…](https://github.com/ippcteam/SN21-adtao/commit/43c698a0f610a87f483b3fc6e43940b28d920739) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [ops(executor): each memory line also shows the container split into p…](https://github.com/ippcteam/SN21-adtao/commit/d0fad2db190b8a67f4e7ef2698f4814e32d3f0ea) — ippcteam/SN21-adtao
- 2026-10-12 · commit · [fix(validator): the validator site sends daily-results requests to th…](https://github.com/ippcteam/SN21-adtao/commit/8398c350839f067498b213de27641ce1277ebee4) — ippcteam/SN21-adtao
- 2026-10-11 · commit · [docs(verifying): the standing preview exists only during a notice per…](https://github.com/ippcteam/SN21-adtao/commit/35e33379210974a7d5d9e65de7a3c5a017a54eb1) — ippcteam/SN21-adtao
- 2026-10-11 · commit · [docs(privacy): say how model images are pulled today; validator-only …](https://github.com/ippcteam/SN21-adtao/commit/4651d3a7277368fda165e193a0ed67480d42b1fe) — ippcteam/SN21-adtao
- 2026-10-09 · commit · [docs(rewards): self-mining section lists the operator-run miners (UID…](https://github.com/ippcteam/SN21-adtao/commit/585b5aa25a3e7cc17a713e5a1a4b6cd8d32fecfe) — ippcteam/SN21-adtao
- 2026-10-09 · release · [SN21 training data v4 (live basket shape)](https://github.com/ippcteam/SN21-adtao/releases/tag/training-v4-2026-10) — ippcteam/SN21-adtao
- 2026-10-09 · commit · [docs(training): v4 training data in the live daily basket shape](https://github.com/ippcteam/SN21-adtao/commit/95d50f617eeea040c57b6baaafb2d4f6d2f6cdb6) — ippcteam/SN21-adtao

## Use

```bash
m subnets.sn21/info        # live identity + market (snapshot if bt is down)
m subnets.sn21/news        # scraped news
m subnets.sn21/trades      # 24h alpha tape
m subnets.sn21/daily       # daily candles
python3 orbit/subnets/sn21/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
