# sn14 — Cacheon ㄷ

A competition where miners submit optimized kernels to compete on end-to-end inference speed against target models.

Bittensor subnet **14** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/latent-to/cacheon) · [url](https://cacheon.ai) · [discord](https://discord.gg/SFt8s4gJD)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.008647 | +0.00% | +0.09% | -1.02% | 53,038 | 24,529 | 120.77 |

## Last 24h flow

12 trades by 9 coldkeys · 5 buys (51.12 τ) / 7 sells (53.16 τ) · net -2.03 τ

## News

- 2026-10-12 · commit · [Retain audit diagnostics and post bundle results to Discord (#145)](https://github.com/latent-to/cacheon/commit/f37721d8d50aab8aef148a25e95803ce5cb573aa) — latent-to/cacheon
- 2026-10-10 · commit · [DeepSeek-V4.1-Flash arena preparation and Qwen-era deletions (#144)](https://github.com/latent-to/cacheon/commit/1b9be0b4aa5ca3c9eab4d884504141ebc69fd2bf) — latent-to/cacheon
- 2026-10-09 · commit · [Notify Discord when new hotkeys receive committed and active weights …](https://github.com/latent-to/cacheon/commit/efbc582bfaf73f7e3e7a0ff66a4edce25cd3bf5b) — latent-to/cacheon
- 2026-10-06 · commit · [Complete follower publication on commit and monitor reveals separatel…](https://github.com/latent-to/cacheon/commit/5d421cc24a46bd316775a2c97ad2193ed8c73891) — latent-to/cacheon
- 2026-10-05 · commit · [Add branded stock-SGLang submission link previews (#140)](https://github.com/latent-to/cacheon/commit/d9e375f73d7467270cfd1b3f66286887963ef267) — latent-to/cacheon
- 2026-10-02 · commit · [Keep weight publication running through intake and gateway outages (#…](https://github.com/latent-to/cacheon/commit/5763f5643cc2d30aaa9bcb9146776bd960306ef5) — latent-to/cacheon
- 2026-10-02 · commit · [Add versioned competition paths to the dashboard (#139)](https://github.com/latent-to/cacheon/commit/256109458288cce202b12f24a3912459cb43c411) — latent-to/cacheon
- 2026-10-02 · commit · [Show consumed evaluation credits in dashboard fee labels (#136)](https://github.com/latent-to/cacheon/commit/76bb51f36584e0966de5a8226a5ddbd3e670bf46) — latent-to/cacheon

## Use

```bash
m subnets.sn14/info        # live identity + market (snapshot if bt is down)
m subnets.sn14/news        # scraped news
m subnets.sn14/trades      # 24h alpha tape
m subnets.sn14/daily       # daily candles
python3 orbit/subnets/sn14/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
