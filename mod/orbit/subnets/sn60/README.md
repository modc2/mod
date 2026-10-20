# sn60 — Bitsec.ai ذ

find and fix exploits in codebases

Bittensor subnet **60** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/Bitsec-AI/sandbox) · [url](https://bitsec.ai) · discord `yubo`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003638 | -2.58% | -3.95% | -15.22% | 21,537 | 6,658 | 1,708 |

## Last 24h flow

146 trades by 60 coldkeys · 73 buys (786.46 τ) / 73 sells (919.56 τ) · net -133.10 τ

## News

- 2026-10-15 · commit · [\[skip ci\] increment to build 62](https://github.com/Bitsec-AI/sandbox/commit/f1dbce601a3ff1b34ccd414a914c1c1a3f50eed2) — Bitsec-AI/sandbox
- 2026-10-15 · commit · [Merge pull request #45 from Bitsec-AI/sentios-endpoint-badge](https://github.com/Bitsec-AI/sandbox/commit/63873632227ea183fbbde6a0c9ca4663fda7044c) — Bitsec-AI/sandbox
- 2026-10-15 · commit · [Use Sentios endpoint badge](https://github.com/Bitsec-AI/sandbox/commit/6185a852aecc3a04f6eb7e19d5e96217ef90cb2d) — Bitsec-AI/sandbox
- 2026-09-29 · commit · [\[skip ci\] increment to build 61](https://github.com/Bitsec-AI/sandbox/commit/3f84cb35d50c112ada0dec22f38b77e36a117790) — Bitsec-AI/sandbox
- 2026-09-29 · commit · [activate sentios](https://github.com/Bitsec-AI/sandbox/commit/cf9acf215a7535109879f84b8b1d72377ef49b12) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [\[skip ci\] increment to build 60](https://github.com/Bitsec-AI/sandbox/commit/529516b7f03ca70b6377c23eb371e74217224346) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [added endpoint for fetching answers to hidden projects](https://github.com/Bitsec-AI/sandbox/commit/2c87079d1ad8651b4511c54b60053a5cbd0a0ec8) — Bitsec-AI/sandbox

## Use

```bash
m subnets.sn60/info        # live identity + market (snapshot if bt is down)
m subnets.sn60/news        # scraped news
m subnets.sn60/trades      # 24h alpha tape
m subnets.sn60/daily       # daily candles
python3 orbit/subnets/sn60/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
