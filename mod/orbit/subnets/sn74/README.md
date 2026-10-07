# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003136 | -0.00% | -0.67% | -3.11% | 16,716 | 5,875 | 119.24 |

## Last 24h flow

32 trades by 26 coldkeys · 8 buys (49.73 τ) / 24 sells (69.03 τ) · net -19.30 τ

## News

- 2026-10-07 · commit · [Rentals: the order seam poller, and dev overrides for our own test bo…](https://github.com/entrius/gittensor/commit/8f93c1e7c32b2ea5b4c20d2ff0214a4d767d9c55) — entrius/gittensor
- 2026-10-06 · commit · [Rentals: the whole-box lease (store, pod under Sysbox, firewall, reco…](https://github.com/entrius/gittensor/commit/fa28e5fbc363ef9a13ddde2753bf465f52651203) — entrius/gittensor
- 2026-10-06 · commit · [Rentable boxes: catalog box sizes, gitt up --rent, Sysbox + rent-port…](https://github.com/entrius/gittensor/commit/35e94a79ac819d9eb84a1289bdf4afd08228088c) — entrius/gittensor
- 2026-10-06 · commit · [Pay: H100 row at the same posture as the 5090 against Lium's anchor (…](https://github.com/entrius/gittensor/commit/992cdf3255963ae629e7b442f10c5acf74efbb06) — entrius/gittensor
- 2026-10-06 · commit · [Pay rows for the six new GPU types (#1810)](https://github.com/entrius/gittensor/commit/ebcc20e6ac3b2cf5f6c8e87cd91cb309318cdc99) — entrius/gittensor
- 2026-10-06 · commit · [Compute pool at 30% of miner emissions (#1809)](https://github.com/entrius/gittensor/commit/a8180b86ccde35c829f828fd36d195073c2fb48a) — entrius/gittensor
- 2026-10-06 · commit · [Sync the NVML driver allowlist with Lium's (36 drivers added) (#1808)](https://github.com/entrius/gittensor/commit/19920531f50264f575f1c0850f474d77291530b7) — entrius/gittensor
- 2026-10-05 · commit · [Remove the compute v0 image builds (sparkinfer image, gt-checker, ser…](https://github.com/entrius/gittensor/commit/46d87715e78e59eb9f79542c9dff35561260982b) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
