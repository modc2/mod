# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.035339 | -0.25% | +4.89% | +19.47% | 225,024 | 29,799 | 24,633 |

## Last 24h flow

1151 trades by 360 coldkeys · 610 buys (12,529 τ) / 541 sells (12,049 τ) · net 479.45 τ

## News

- 2026-10-19 · release · [v0.5.4](https://github.com/taostat/gm-miner/releases/tag/v0.5.4) — taostat/gm-miner
- 2026-10-19 · commit · [chore(release): promote gm-miner 0.5.4 (#333)](https://github.com/taostat/gm-miner/commit/b8daef9290bbdd964fcd2a8f103f3aed387cd616) — taostat/gm-miner
- 2026-10-19 · release · [v0.5.4-dev](https://github.com/taostat/gm-miner/releases/tag/v0.5.4-dev) — taostat/gm-miner
- 2026-10-19 · commit · [Use one request per connection to Engy and prepare 0.5.4-dev (#332)](https://github.com/taostat/gm-miner/commit/7f5d4ae97ec5f870e6770d7ecb87b3cc2e5bec5e) — taostat/gm-miner
- 2026-10-19 · release · [v0.5.3-dev](https://github.com/taostat/gm-miner/releases/tag/v0.5.3-dev) — taostat/gm-miner
- 2026-10-19 · commit · [Cap HTTP/2 upstream streams per connection and prepare 0.5.3-dev (#331)](https://github.com/taostat/gm-miner/commit/63190d8d0ceccb8943c8988ec7a0c38f556ccd3f) — taostat/gm-miner
- 2026-10-17 · release · [v0.5.2](https://github.com/taostat/gm-miner/releases/tag/v0.5.2) — taostat/gm-miner
- 2026-10-17 · commit · [chore(release): promote gm-miner 0.5.2 (#327)](https://github.com/taostat/gm-miner/commit/2261fc7b780b82c8f34a7eb905dc62c425672202) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
