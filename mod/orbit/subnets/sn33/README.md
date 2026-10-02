# sn33 — ReadyAI ט

Bittensor subnet **33** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/afterpartyai/bittensor-conversation-genome-project)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003900 | -0.00% | -1.20% | -11.31% | 21,356 | 13,378 | 91.30 |

## Last 24h flow

161 trades by 25 coldkeys · 4 buys (2.04 τ) / 157 sells (85.65 τ) · net -83.61 τ

## News

- 2026-09-24 · commit · [Merge pull request #138 from afterpartyai/bump-docker-image-version](https://github.com/afterpartyai/bittensor-conversation-genome-project/commit/92b3e701058379a2cf772befdcff86868ae55afb) — afterpartyai/bittensor-conversation-genome-project
- 2026-09-23 · commit · [Dockerfile](https://github.com/afterpartyai/bittensor-conversation-genome-project/commit/d3775435d953576f87885dfe447490bfdff4617e) — afterpartyai/bittensor-conversation-genome-project
- 2026-09-21 · commit · [Merge pull request #137 from afterpartyai/adjust-put-task-ordering](https://github.com/afterpartyai/bittensor-conversation-genome-project/commit/412ce25185dc3fb7acf05d52050b214d9a7e0c5a) — afterpartyai/bittensor-conversation-genome-project
- 2026-09-21 · commit · [Buffer tasks and send them at the end of the validation loop](https://github.com/afterpartyai/bittensor-conversation-genome-project/commit/5b0707c5854da666dc41019b8ec3e3f301832451) — afterpartyai/bittensor-conversation-genome-project

## Use

```bash
m subnets.sn33/info        # live identity + market (snapshot if bt is down)
m subnets.sn33/news        # scraped news
m subnets.sn33/trades      # 24h alpha tape
m subnets.sn33/daily       # daily candles
python3 orbit/subnets/sn33/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
