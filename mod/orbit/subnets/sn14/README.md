# sn14 — Cacheon ㄷ

A competition where miners submit optimized kernels to compete on end-to-end inference speed against target models.

Bittensor subnet **14** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/latent-to/cacheon) · [url](https://cacheon.ai) · [discord](https://discord.gg/SFt8s4gJD)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008786 | -0.06% | -0.08% | -0.95% | 53,167 | 24,637 | 56.76 |

## Last 24h flow

62 trades by 17 coldkeys · 6 buys (7.72 τ) / 56 sells (31.33 τ) · net -23.61 τ

## News

- 2026-10-02 · commit · [Keep weight publication running through intake and gateway outages (#…](https://github.com/latent-to/cacheon/commit/5763f5643cc2d30aaa9bcb9146776bd960306ef5) — latent-to/cacheon
- 2026-10-02 · commit · [Add versioned competition paths to the dashboard (#139)](https://github.com/latent-to/cacheon/commit/256109458288cce202b12f24a3912459cb43c411) — latent-to/cacheon
- 2026-10-02 · commit · [Show consumed evaluation credits in dashboard fee labels (#136)](https://github.com/latent-to/cacheon/commit/76bb51f36584e0966de5a8226a5ddbd3e670bf46) — latent-to/cacheon
- 2026-10-02 · commit · [Separate worker observations from CPU relay heartbeats (#137)](https://github.com/latent-to/cacheon/commit/ad53f729e27138d1e26ee8befa561bf6aaa7f512) — latent-to/cacheon
- 2026-10-01 · release · [GLM crowned baseline source — 2026-10-01](https://github.com/latent-to/cacheon/releases/tag/glm-baseline-20261001) — latent-to/cacheon
- 2026-10-01 · commit · [Replay eval, prefix-cache target, and retirement of the batch-cell er…](https://github.com/latent-to/cacheon/commit/0fb3faf8c88da6e21621ab8c65e8ebd6b001a5b2) — latent-to/cacheon
- 2026-09-26 · commit · [Merge pull request #128 from latent-to/codex/admission-baseline-cutoff](https://github.com/latent-to/cacheon/commit/bd10ec730cbc7f7ab3b188c5d37a6d0ac0a18bda) — latent-to/cacheon
- 2026-09-26 · commit · [Enforce baseline cutoff at admission and explain lost potential winners](https://github.com/latent-to/cacheon/commit/f7a29a3d189e0edafabb9e98ae423b1bc54d37bc) — latent-to/cacheon

## Use

```bash
m subnets.sn14/info        # live identity + market (snapshot if bt is down)
m subnets.sn14/news        # scraped news
m subnets.sn14/trades      # 24h alpha tape
m subnets.sn14/daily       # daily candles
python3 orbit/subnets/sn14/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
