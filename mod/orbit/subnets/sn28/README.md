# sn28 — SayGM ד

Drop-in access to Claude, GPT, and Gemini.

Bittensor subnet **28** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/taostat/gm-miner) · [url](https://saygm.com/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.020860 | -1.31% | -2.70% | -5.70% | 129,837 | 22,491 | 4,080 |

## Last 24h flow

463 trades by 208 coldkeys · 193 buys (1,819 τ) / 270 sells (2,187 τ) · net -367.59 τ

## News

- 2026-09-28 · release · [v0.4.24](https://github.com/taostat/gm-miner/releases/tag/v0.4.24) — taostat/gm-miner
- 2026-09-28 · commit · [chore(release): promote gm-miner 0.4.24 (#291)](https://github.com/taostat/gm-miner/commit/163a5c676c3a65558694a36f6b3bc8bbe59f27d8) — taostat/gm-miner
- 2026-09-28 · release · [v0.4.24-dev](https://github.com/taostat/gm-miner/releases/tag/v0.4.24-dev) — taostat/gm-miner
- 2026-09-27 · commit · [chore(release): gm-miner 0.4.24-dev (#290)](https://github.com/taostat/gm-miner/commit/ee94e78f819c795f75092bbf740d4cc15ef018da) — taostat/gm-miner
- 2026-09-27 · commit · [test(attestd): forward supplier trailers through the KubeTEE verifier…](https://github.com/taostat/gm-miner/commit/3bd8db58e8199358add52dcd112283e1cd270d22) — taostat/gm-miner
- 2026-09-27 · commit · [Serve KubeTEE chat on attested upstream connections (#286)](https://github.com/taostat/gm-miner/commit/88a771dcd99bb4357bfbce9a31ac1d1728e8e417) — taostat/gm-miner
- 2026-09-27 · commit · [fix(cli): give the Azure OpenAI echo probe headroom for reasoning mod…](https://github.com/taostat/gm-miner/commit/d7d2bae954666dfdab732594e54289bed283b207) — taostat/gm-miner
- 2026-09-24 · release · [v0.4.23](https://github.com/taostat/gm-miner/releases/tag/v0.4.23) — taostat/gm-miner

## Use

```bash
m subnets.sn28/info        # live identity + market (snapshot if bt is down)
m subnets.sn28/news        # scraped news
m subnets.sn28/trades      # 24h alpha tape
m subnets.sn28/daily       # daily candles
python3 orbit/subnets/sn28/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
