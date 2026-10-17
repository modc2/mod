# sn14 — Cacheon ㄷ

A competition where miners submit optimized kernels to compete on end-to-end inference speed against target models.

Bittensor subnet **14** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/latent-to/cacheon) · [url](https://cacheon.ai) · [discord](https://discord.gg/SFt8s4gJD)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008663 | +0.00% | -1.43% | -1.97% | 53,341 | 24,576 | 237.96 |

## Last 24h flow

23 trades by 18 coldkeys · 3 buys (15.13 τ) / 20 sells (204.05 τ) · net -188.92 τ

## News

- 2026-10-15 · commit · [Allow sealed audit decode budgets for speculative arenas (#155)](https://github.com/latent-to/cacheon/commit/b0e5090ab7918b166150ea708a9d4f8f884d563a) — latent-to/cacheon
- 2026-10-15 · commit · [Audit native recurrent MTP state and hybrid prefix keys (#153)](https://github.com/latent-to/cacheon/commit/89e13099f3e1fa7969a772a8e81e45d9817247dc) — latent-to/cacheon
- 2026-10-15 · commit · [Resume checkpointed evaluations across GPU worker replacement (#151)](https://github.com/latent-to/cacheon/commit/b634933772700515f7023ca52c07724337144690) — latent-to/cacheon
- 2026-10-14 · commit · [Show evaluator activity from the current worker heartbeat (#150)](https://github.com/latent-to/cacheon/commit/233e2797710c6637bf0b459e55b53ce8716183d4) — latent-to/cacheon
- 2026-10-14 · commit · [Merge pull request #147 from latent-to/codex/qwen38-recurrent-audit](https://github.com/latent-to/cacheon/commit/074e472406978ba8b051f7455cf83d72d46ecaa5) — latent-to/cacheon
- 2026-10-14 · commit · [Allow future arena source additions without repricing prior submissions](https://github.com/latent-to/cacheon/commit/a5128bacc0b6956819c24a1e1acfd86ed23e86b5) — latent-to/cacheon
- 2026-10-14 · commit · [Document Qwen GPU reservation through worker drain and resume](https://github.com/latent-to/cacheon/commit/623f0d7fa5a682edd80881f884a9fb99e6521439) — latent-to/cacheon
- 2026-10-14 · commit · [Audit native recurrent prefix checkpoints and model state](https://github.com/latent-to/cacheon/commit/4562c1909e6ef16347aeff1416d11fd506056cda) — latent-to/cacheon

## Use

```bash
m subnets.sn14/info        # live identity + market (snapshot if bt is down)
m subnets.sn14/news        # scraped news
m subnets.sn14/trades      # 24h alpha tape
m subnets.sn14/daily       # daily candles
python3 orbit/subnets/sn14/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
