# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003372 | -0.01% | -2.68% | -9.12% | 5,317 | 1,387 | 271.96 |

## Last 24h flow

211 trades by 141 coldkeys · 48 buys (126.17 τ) / 163 sells (144.15 τ) · net -17.98 τ

## News

- 2026-10-16 · commit · [Merge pull request #283 from Umi-BitSign/codex/authenticated-package-…](https://github.com/Umi-BitSign/umi/commit/c087facb3dfd2c20adbe27cd94ea7bffdc49fb86) — Umi-BitSign/umi
- 2026-10-16 · commit · [Reuse verified semantics for authenticated sealed package copies](https://github.com/Umi-BitSign/umi/commit/f5558407397a4faa457a767327ece7025817c90f) — Umi-BitSign/umi
- 2026-10-15 · commit · [Merge pull request #282 from Umi-BitSign/codex/transfer-canonical-pro…](https://github.com/Umi-BitSign/umi/commit/7db34e016be456ff9b593a20943b9e6fbf364690) — Umi-BitSign/umi
- 2026-10-15 · commit · [Reuse bounded transfer serialization and report actual child progress](https://github.com/Umi-BitSign/umi/commit/1616a810533dd61397063f9e8ec63841687e698e) — Umi-BitSign/umi
- 2026-10-15 · commit · [Merge pull request #281 from Umi-BitSign/codex/history-transfer-physi…](https://github.com/Umi-BitSign/umi/commit/655256d98e1cfba5a699956946c19081878ad42c) — Umi-BitSign/umi
- 2026-10-15 · commit · [Bound stopped history transfer and qualify interrupted rollback recovery](https://github.com/Umi-BitSign/umi/commit/39f9bb66bb95a5858c9242ad5a1b08b6f676fd7b) — Umi-BitSign/umi
- 2026-10-15 · commit · [Merge pull request #280 from Umi-BitSign/codex/history-transfer-names…](https://github.com/Umi-BitSign/umi/commit/ac2b72e8d983602ba5307a90796acc14a0dde7a1) — Umi-BitSign/umi
- 2026-10-15 · commit · [Prepare history transfer namespace before heartbeat threads](https://github.com/Umi-BitSign/umi/commit/a91d9ed86ffe11d5a925d3f5158079bb4e704c7e) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
