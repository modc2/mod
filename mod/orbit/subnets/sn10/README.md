# sn10 — Pareton Ⱂ

The Intelligence Layer for AI Inference

Bittensor subnet **10** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Pareton-ai/pareton) · [url](https://www.pareton.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005435 | +0.01% | -4.83% | -12.86% | 33,504 | 15,194 | 1,250 |

## Last 24h flow

79 trades by 50 coldkeys · 35 buys (434.63 τ) / 44 sells (814.10 τ) · net -379.47 τ

## News

- 2026-10-15 · commit · [Merge pull request #201 from Pareton-ai/fix/k3-natural-stop-reproduci…](https://github.com/Pareton-ai/pareton/commit/aa181158b23d6d06ffa02b1694137bb8cb09d7e3) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(bench): avoid voiding natural-stop rounds on length variance](https://github.com/Pareton-ai/pareton/commit/33c2cca21dfa7c40691e94d265752865cf3cd194) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(round): run rounds only for open campaigns (#200)](https://github.com/Pareton-ai/pareton/commit/1d9175ff6a162bf2ed5a558ec30772043a236b4d) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(score): charge short completions at full length in tier timing (#…](https://github.com/Pareton-ai/pareton/commit/6b7f895652cfe051e27d295e959a2dbde5889c50) — Pareton-ai/pareton
- 2026-10-14 · commit · [perf(weights): reuse the weights hash while cached files are unchange…](https://github.com/Pareton-ai/pareton/commit/3be5c020d595cf4f52b78914c0d606f50dac6a3c) — Pareton-ai/pareton
- 2026-10-14 · commit · [fix(bench): give the SGLang /server_info read a 600s timeout (#198)](https://github.com/Pareton-ai/pareton/commit/8d38b48ed25ab2bd28826f916e12bce0b6e2151b) — Pareton-ai/pareton
- 2026-10-13 · commit · [feat: Kimi K3 B300 campaign fixture with DSPARK draft staging (#193)](https://github.com/Pareton-ai/pareton/commit/307b54be2d89a2de7f2a7439aa0d4423523ee6d0) — Pareton-ai/pareton
- 2026-10-08 · commit · [fix(gpu): normalize RTX PRO names for static SSH hosts (#192)](https://github.com/Pareton-ai/pareton/commit/2f990c31a2f9f9d3ba856e3655b669f26f11cab6) — Pareton-ai/pareton

## Use

```bash
m subnets.sn10/info        # live identity + market (snapshot if bt is down)
m subnets.sn10/news        # scraped news
m subnets.sn10/trades      # 24h alpha tape
m subnets.sn10/daily       # daily candles
python3 orbit/subnets/sn10/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
