# sn60 — Bitsec.ai ذ

find and fix exploits in codebases

Bittensor subnet **60** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Bitsec-AI/sandbox) · [url](https://bitsec.ai) · discord `yubo`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003826 | +0.15% | -1.60% | -6.10% | 22,537 | 6,827 | 1,209 |

## Last 24h flow

136 trades by 75 coldkeys · 69 buys (576.88 τ) / 67 sells (631.23 τ) · net -54.35 τ

## News

- 2026-10-15 · commit · [\[skip ci\] increment to build 62](https://github.com/Bitsec-AI/sandbox/commit/f1dbce601a3ff1b34ccd414a914c1c1a3f50eed2) — Bitsec-AI/sandbox
- 2026-10-15 · commit · [Merge pull request #45 from Bitsec-AI/sentios-endpoint-badge](https://github.com/Bitsec-AI/sandbox/commit/63873632227ea183fbbde6a0c9ca4663fda7044c) — Bitsec-AI/sandbox
- 2026-10-15 · commit · [Use Sentios endpoint badge](https://github.com/Bitsec-AI/sandbox/commit/6185a852aecc3a04f6eb7e19d5e96217ef90cb2d) — Bitsec-AI/sandbox
- 2026-09-29 · commit · [\[skip ci\] increment to build 61](https://github.com/Bitsec-AI/sandbox/commit/3f84cb35d50c112ada0dec22f38b77e36a117790) — Bitsec-AI/sandbox
- 2026-09-29 · commit · [activate sentios](https://github.com/Bitsec-AI/sandbox/commit/cf9acf215a7535109879f84b8b1d72377ef49b12) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [\[skip ci\] increment to build 60](https://github.com/Bitsec-AI/sandbox/commit/529516b7f03ca70b6377c23eb371e74217224346) — Bitsec-AI/sandbox
- 2026-09-22 · commit · [added endpoint for fetching answers to hidden projects](https://github.com/Bitsec-AI/sandbox/commit/2c87079d1ad8651b4511c54b60053a5cbd0a0ec8) — Bitsec-AI/sandbox
- 2026-09-17 · commit · [\[skip ci\] increment to build 59](https://github.com/Bitsec-AI/sandbox/commit/80d0293278e0606bc2f74c87be6f082600a97e0c) — Bitsec-AI/sandbox

## Use

```bash
m subnets.sn60/info        # live identity + market (snapshot if bt is down)
m subnets.sn60/news        # scraped news
m subnets.sn60/trades      # 24h alpha tape
m subnets.sn60/daily       # daily candles
python3 orbit/subnets/sn60/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
