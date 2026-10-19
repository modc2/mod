# sn10 — Pareton Ⱂ

The Intelligence Layer for AI Inference

Bittensor subnet **10** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/Pareton-ai/pareton) · [url](https://www.pareton.ai/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005096 | — | +0.02% | -16.86% | 31,523 | 14,714 | 575.09 |

## Last 24h flow

48 trades by 27 coldkeys · 22 buys (288.31 τ) / 26 sells (285.78 τ) · net 2.52 τ

## News

- 2026-10-18 · commit · [Merge pull request #205 from Pareton-ai/fix/bohdan/db-pool-wait](https://github.com/Pareton-ai/pareton/commit/3c2d824adb037aaf04ff938157c89c1789a9a9ec) — Pareton-ai/pareton
- 2026-10-17 · commit · [fix(db): cap pool wait at 3s and log wait timeouts once per window](https://github.com/Pareton-ai/pareton/commit/d5695762fea5671aba54d700fb0fa00dbb9886e2) — Pareton-ai/pareton
- 2026-10-17 · commit · [fix(api): serve /health on the event loop so pool waits cannot stall it](https://github.com/Pareton-ai/pareton/commit/4d805616b4902d47c9ddcd31853e6d6f69b81682) — Pareton-ai/pareton
- 2026-10-16 · commit · [fix(db): wait for a free pooled connection instead of returning 503](https://github.com/Pareton-ai/pareton/commit/ce24035ee340f422024bdce6824b5c558c610888) — Pareton-ai/pareton
- 2026-10-16 · commit · [feat: run Kimi K3 at C32 with 8 prompts per tier, reusing the qualifi…](https://github.com/Pareton-ai/pareton/commit/952b86c25fb1576d83336a6f3083aa6c5d7deca9) — Pareton-ai/pareton
- 2026-10-16 · commit · [feat(rank): let a campaign pin its own baseline drift ceiling (#196)](https://github.com/Pareton-ai/pareton/commit/e37b3138cb2440f53debd68b234e944384b1ea40) — Pareton-ai/pareton
- 2026-10-15 · commit · [Merge pull request #201 from Pareton-ai/fix/k3-natural-stop-reproduci…](https://github.com/Pareton-ai/pareton/commit/aa181158b23d6d06ffa02b1694137bb8cb09d7e3) — Pareton-ai/pareton
- 2026-10-15 · commit · [fix(bench): avoid voiding natural-stop rounds on length variance](https://github.com/Pareton-ai/pareton/commit/33c2cca21dfa7c40691e94d265752865cf3cd194) — Pareton-ai/pareton

## Use

```bash
m subnets.sn10/info        # live identity + market (snapshot if bt is down)
m subnets.sn10/news        # scraped news
m subnets.sn10/trades      # 24h alpha tape
m subnets.sn10/daily       # daily candles
python3 orbit/subnets/sn10/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
