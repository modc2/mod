# sn10 — Pareton Ⱂ

The Intelligence Layer for AI Inference

Bittensor subnet **10** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Pareton-ai/pareton) · [url](https://www.pareton.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005095 | -0.00% | -2.06% | -16.90% | 31,483 | 14,712 | 428.11 |

## Last 24h flow

38 trades by 28 coldkeys · 10 buys (137.12 τ) / 28 sells (290.28 τ) · net -153.16 τ

## News

- 2026-10-16 · commit · [feat: run Kimi K3 at C32 with 8 prompts per tier, reusing the qualifi…](https://github.com/Pareton-ai/pareton/commit/952b86c25fb1576d83336a6f3083aa6c5d7deca9) — Pareton-ai/pareton
- 2026-10-16 · commit · [feat(rank): let a campaign pin its own baseline drift ceiling (#196)](https://github.com/Pareton-ai/pareton/commit/e37b3138cb2440f53debd68b234e944384b1ea40) — Pareton-ai/pareton
- 2026-10-15 · commit · [Merge pull request #201 from Pareton-ai/fix/k3-natural-stop-reproduci…](https://github.com/Pareton-ai/pareton/commit/aa181158b23d6d06ffa02b1694137bb8cb09d7e3) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(bench): avoid voiding natural-stop rounds on length variance](https://github.com/Pareton-ai/pareton/commit/33c2cca21dfa7c40691e94d265752865cf3cd194) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(round): run rounds only for open campaigns (#200)](https://github.com/Pareton-ai/pareton/commit/1d9175ff6a162bf2ed5a558ec30772043a236b4d) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(score): charge short completions at full length in tier timing (#…](https://github.com/Pareton-ai/pareton/commit/6b7f895652cfe051e27d295e959a2dbde5889c50) — Pareton-ai/pareton
- 2026-10-14 · commit · [perf(weights): reuse the weights hash while cached files are unchange…](https://github.com/Pareton-ai/pareton/commit/3be5c020d595cf4f52b78914c0d606f50dac6a3c) — Pareton-ai/pareton
- 2026-10-14 · commit · [fix(bench): give the SGLang /server_info read a 600s timeout (#198)](https://github.com/Pareton-ai/pareton/commit/8d38b48ed25ab2bd28826f916e12bce0b6e2151b) — Pareton-ai/pareton

## Use

```bash
m subnets.sn10/info        # live identity + market (snapshot if bt is down)
m subnets.sn10/news        # scraped news
m subnets.sn10/trades      # 24h alpha tape
m subnets.sn10/daily       # daily candles
python3 orbit/subnets/sn10/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
