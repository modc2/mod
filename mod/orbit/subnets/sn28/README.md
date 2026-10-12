# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.025380 | +0.68% | +4.79% | +20.70% | 159,962 | 25,034 | 3,257 |

## Last 24h flow

379 trades by 180 coldkeys · 190 buys (1,832 τ) / 189 sells (1,332 τ) · net 499.63 τ

## News

- 2026-10-10 · release · [v0.4.27-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.27-dev) — taostat/gm-miner
- 2026-10-10 · commit · [chore(release): gm-miner 0.4.27-dev (#310)](https://github.com/taostat/gm-miner/commit/88920c8f9f6de07624f25bbb12f56e166c8d420a) — taostat/gm-miner
- 2026-10-10 · commit · [Add video job routes for KubeTEE and DeepInfra (#309)](https://github.com/taostat/gm-miner/commit/b096bbf5e3f70b9cfcecce59dc2c11259a621d19) — taostat/gm-miner
- 2026-10-06 · commit · [Remove paid Gemini image-canary command (#299)](https://github.com/taostat/gm-miner/commit/556de337c0a9f97998a9ba1fb7610e2bedf0fcbe) — taostat/gm-miner
- 2026-10-06 · release · [v0.4.26](https://github.com/taostat/gm-miner/releases/tag/v0.4.26) — taostat/gm-miner
- 2026-10-06 · commit · [Add gmcli notifications list, set, confirm, status and off (#297)](https://github.com/taostat/gm-miner/commit/deab605b3bb752aed1bb074a6e248ee3a77f46bd) — taostat/gm-miner
- 2026-10-05 · commit · [fix(cli): retry rate-limited token requests instead of re-logging in …](https://github.com/taostat/gm-miner/commit/4eb759e1326ad1f15e84dfd37542035865ca0b02) — taostat/gm-miner
- 2026-10-04 · release · [v0.4.25](https://github.com/taostat/gm-miner/releases/tag/v0.4.25) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
