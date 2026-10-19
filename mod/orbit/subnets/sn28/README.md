# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

Fleet mods for this subnet: `saygm`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.034063 | — | -0.95% | +34.21% | 216,595 | 29,221 | 2,970 |

## Last 24h flow

553 trades by 190 coldkeys · 149 buys (1,302 τ) / 404 sells (1,546 τ) · net -243.96 τ

## News

- 2026-10-17 · release · [v0.5.2](https://github.com/taostat/gm-miner/releases/tag/v0.5.2) — taostat/gm-miner
- 2026-10-17 · commit · [chore(release): promote gm-miner 0.5.2 (#327)](https://github.com/taostat/gm-miner/commit/2261fc7b780b82c8f34a7eb905dc62c425672202) — taostat/gm-miner
- 2026-10-17 · release · [v0.5.2-dev](https://github.com/taostat/gm-miner/releases/tag/v0.5.2-dev) — taostat/gm-miner
- 2026-10-17 · commit · [fix: allow KubeTEE DeepSeek Flash 1017 and prepare 0.5.2-dev (#326)](https://github.com/taostat/gm-miner/commit/f12202c216f358c21c82e71e9595949d120aed71) — taostat/gm-miner
- 2026-10-17 · commit · [docs: document KubeTEE DeepSeek V4.1 Flash 1017 source (#325)](https://github.com/taostat/gm-miner/commit/aa8cb0b2ec2b03f9a960144b9d869ff2a7fd4ec3) — taostat/gm-miner
- 2026-10-15 · release · [v0.5.1-dev](https://github.com/taostat/gm-miner/releases/tag/v0.5.1-dev) — taostat/gm-miner
- 2026-10-15 · commit · [Add Engy MiniMax H3 video route (#324)](https://github.com/taostat/gm-miner/commit/70bc539e8bcbcfd21cf09ee252af2e2b1b757f4b) — taostat/gm-miner
- 2026-10-15 · commit · [Document DeepInfra GLM-5.3 and Flash sourcing (#323)](https://github.com/taostat/gm-miner/commit/3de24065faf3357e2116529677f4dcb0c6c2b402) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
