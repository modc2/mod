# sn25 — UR א

The Privacy Protocol

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005792 | -0.15% | +6.76% | -9.28% | 35,905 | 12,357 | 4,175 |

## Last 24h flow

251 trades by 90 coldkeys · 156 buys (2,285 τ) / 95 sells (1,878 τ) · net 407.22 τ

## News

- 2026-10-17 · release · [v2026.10.16-1074037020](https://github.com/urfoundation/sn/releases/tag/v2026.10.16-1074037020) — urfoundation/sn
- 2026-10-17 · commit · [Merge remote-tracking branch 'origin/main'](https://github.com/urfoundation/sn/commit/6608298ead6b69206cddc3a28259043651eefd97) — urfoundation/sn
- 2026-10-17 · commit · [feat(api): document competition automation control plane](https://github.com/urfoundation/sn/commit/b6bb975ffd211a5533261d66afa6e5b8d7eb1cf3) — urfoundation/sn
- 2026-10-16 · release · [v2026.10.16-1073891640](https://github.com/urfoundation/sn/releases/tag/v2026.10.16-1073891640) — urfoundation/sn
- 2026-10-16 · commit · [Merge deterministic owned export cleanup regression](https://github.com/urfoundation/sn/commit/3c56670c9b8f76b8aad955cca0a8e7271608aa04) — urfoundation/sn
- 2026-10-16 · commit · [Inject owned export removal failure independently of privilege](https://github.com/urfoundation/sn/commit/670c15c19d12db02e928808bd2f894194ef900bd) — urfoundation/sn
- 2026-10-16 · commit · [Keep CLI release outputs out of source provenance](https://github.com/urfoundation/sn/commit/df027a4e7cbfd719e5e44e209646bddb808f35ed) — urfoundation/sn
- 2026-10-16 · release · [v2026.10.16-1073843780](https://github.com/urfoundation/sn/releases/tag/v2026.10.16-1073843780) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
