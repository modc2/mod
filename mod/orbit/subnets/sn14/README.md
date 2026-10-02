# sn14 — Cacheon ㄷ

A competition where miners submit optimized kernels to compete on end-to-end inference speed against target models.

Bittensor subnet **14** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/latent-to/cacheon) · [url](https://cacheon.ai) · [discord](https://discord.gg/SFt8s4gJD)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008812 | +0.00% | -0.57% | -0.90% | 53,208 | 24,660 | 104.23 |

## Last 24h flow

65 trades by 20 coldkeys · 1 buys (0.03 τ) / 64 sells (78.49 τ) · net -78.46 τ

## News

- 2026-10-02 · commit · [Show consumed evaluation credits in dashboard fee labels (#136)](https://github.com/latent-to/cacheon/commit/76bb51f36584e0966de5a8226a5ddbd3e670bf46) — latent-to/cacheon
- 2026-10-02 · commit · [Separate worker observations from CPU relay heartbeats (#137)](https://github.com/latent-to/cacheon/commit/ad53f729e27138d1e26ee8befa561bf6aaa7f512) — latent-to/cacheon
- 2026-10-01 · release · [GLM crowned baseline source — 2026-10-01](https://github.com/latent-to/cacheon/releases/tag/glm-baseline-20261001) — latent-to/cacheon
- 2026-10-01 · commit · [Replay eval, prefix-cache target, and retirement of the batch-cell er…](https://github.com/latent-to/cacheon/commit/0fb3faf8c88da6e21621ab8c65e8ebd6b001a5b2) — latent-to/cacheon
- 2026-09-26 · commit · [Merge pull request #128 from latent-to/codex/admission-baseline-cutoff](https://github.com/latent-to/cacheon/commit/bd10ec730cbc7f7ab3b188c5d37a6d0ac0a18bda) — latent-to/cacheon
- 2026-09-26 · commit · [Enforce baseline cutoff at admission and explain lost potential winners](https://github.com/latent-to/cacheon/commit/f7a29a3d189e0edafabb9e98ae423b1bc54d37bc) — latent-to/cacheon
- 2026-09-26 · commit · [Show potential winners and link scoring baselines (#127)](https://github.com/latent-to/cacheon/commit/df26a04663fbe3da0b9134ca14d332c2bb3ae8b0) — latent-to/cacheon
- 2026-09-26 · commit · [Merge pull request #126 from latent-to/codex/score-against-previous-best](https://github.com/latent-to/cacheon/commit/c764f2028bd17a15c86c0245748efec5458d96c8) — latent-to/cacheon

## Use

```bash
m subnets.sn14/info        # live identity + market (snapshot if bt is down)
m subnets.sn14/news        # scraped news
m subnets.sn14/trades      # 24h alpha tape
m subnets.sn14/daily       # daily candles
python3 orbit/subnets/sn14/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
