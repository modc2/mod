# sn14 — Cacheon ㄷ

A competition where miners submit optimized kernels to compete on end-to-end inference speed against target models.

Bittensor subnet **14** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/latent-to/cacheon) · [url](https://cacheon.ai) · [discord](https://discord.gg/SFt8s4gJD)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008811 | +0.00% | +1.89% | -1.04% | 54,115 | 24,769 | 1,528 |

## Last 24h flow

72 trades by 32 coldkeys · 40 buys (863.95 τ) / 32 sells (646.62 τ) · net 217.34 τ

## News

- 2026-10-14 · commit · [Show evaluator activity from the current worker heartbeat (#150)](https://github.com/latent-to/cacheon/commit/233e2797710c6637bf0b459e55b53ce8716183d4) — latent-to/cacheon
- 2026-10-14 · commit · [Merge pull request #147 from latent-to/codex/qwen38-recurrent-audit](https://github.com/latent-to/cacheon/commit/074e472406978ba8b051f7455cf83d72d46ecaa5) — latent-to/cacheon
- 2026-10-14 · commit · [Allow future arena source additions without repricing prior submissions](https://github.com/latent-to/cacheon/commit/a5128bacc0b6956819c24a1e1acfd86ed23e86b5) — latent-to/cacheon
- 2026-10-14 · commit · [Document Qwen GPU reservation through worker drain and resume](https://github.com/latent-to/cacheon/commit/623f0d7fa5a682edd80881f884a9fb99e6521439) — latent-to/cacheon
- 2026-10-14 · commit · [Audit native recurrent prefix checkpoints and model state](https://github.com/latent-to/cacheon/commit/4562c1909e6ef16347aeff1416d11fd506056cda) — latent-to/cacheon
- 2026-10-12 · commit · [Retain audit diagnostics and post bundle results to Discord (#145)](https://github.com/latent-to/cacheon/commit/f37721d8d50aab8aef148a25e95803ce5cb573aa) — latent-to/cacheon
- 2026-10-10 · commit · [DeepSeek-V4.1-Flash arena preparation and Qwen-era deletions (#144)](https://github.com/latent-to/cacheon/commit/1b9be0b4aa5ca3c9eab4d884504141ebc69fd2bf) — latent-to/cacheon
- 2026-10-09 · commit · [Notify Discord when new hotkeys receive committed and active weights …](https://github.com/latent-to/cacheon/commit/efbc582bfaf73f7e3e7a0ff66a4edce25cd3bf5b) — latent-to/cacheon

## Use

```bash
m subnets.sn14/info        # live identity + market (snapshot if bt is down)
m subnets.sn14/news        # scraped news
m subnets.sn14/trades      # 24h alpha tape
m subnets.sn14/daily       # daily candles
python3 orbit/subnets/sn14/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
