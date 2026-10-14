# sn10 — Pareton Ⱂ

The Intelligence Layer for AI Inference

Bittensor subnet **10** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/Pareton-ai/pareton) · [url](https://www.pareton.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005901 | -0.01% | -1.54% | -7.87% | 36,295 | 15,832 | 2,157 |

## Last 24h flow

147 trades by 64 coldkeys · 72 buys (1,017 τ) / 75 sells (1,138 τ) · net -120.83 τ

## News

- 2026-10-13 · commit · [feat: Kimi K3 B300 campaign fixture with DSPARK draft staging (#193)](https://github.com/Pareton-ai/pareton/commit/307b54be2d89a2de7f2a7439aa0d4423523ee6d0) — Pareton-ai/pareton
- 2026-10-08 · commit · [fix(gpu): normalize RTX PRO names for static SSH hosts (#192)](https://github.com/Pareton-ai/pareton/commit/2f990c31a2f9f9d3ba856e3655b669f26f11cab6) — Pareton-ai/pareton
- 2026-10-06 · commit · [fix(api): distinguish v5 completion failures from timing diagnostics …](https://github.com/Pareton-ai/pareton/commit/80540d9715fe73bd77651b4ab401b5336c361e72) — Pareton-ai/pareton
- 2026-10-06 · commit · [fix(gpu): recover Lium RTX PRO 6000 inventory identity (#190)](https://github.com/Pareton-ai/pareton/commit/dfaf4bda1665373e6b5f615518054d388442d274) — Pareton-ai/pareton
- 2026-10-06 · commit · [feat(ops): add private PRO6000 FP8 campaign with v5 C4 scoring (#188)](https://github.com/Pareton-ai/pareton/commit/7d327e9adb3e6d7e06852265ae8f769a427a4cb6) — Pareton-ai/pareton
- 2026-10-05 · commit · [feat(bench): add tier concurrency scoring with failure penalties (#187)](https://github.com/Pareton-ai/pareton/commit/8ca3970fb075d953597d2359492667c88bce3da9) — Pareton-ai/pareton
- 2026-10-05 · commit · [feat: configure campaign patch visibility outside the manifest hash (…](https://github.com/Pareton-ai/pareton/commit/25e21a5fde77fe40f568d3e98f0f9552e712a188) — Pareton-ai/pareton
- 2026-09-27 · commit · [Merge pull request #182 from Pareton-ai/bohdan/par-136-builder-cleanu…](https://github.com/Pareton-ai/pareton/commit/36852d8533b4d73e0392ea113bd803c8e8f50b3d) — Pareton-ai/pareton

## Use

```bash
m subnets.sn10/info        # live identity + market (snapshot if bt is down)
m subnets.sn10/news        # scraped news
m subnets.sn10/trades      # 24h alpha tape
m subnets.sn10/daily       # daily candles
python3 orbit/subnets/sn10/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
