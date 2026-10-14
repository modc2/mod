# sn94 — Cathedral ᚄ

CPU sandboxes powering reinforcement learning on Bittensor.

Bittensor subnet **94** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/cathedralai/cathedral-sandbox) · [url](https://cathedral.computer/) · [discord](https://discord.com/channels/799672011265015819/1526241812589711571)

Fleet mods for this subnet: `cathedral`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004869 | +1.10% | +7.74% | +2.05% | 12,773 | 2,388 | 899.77 |

## Last 24h flow

116 trades by 50 coldkeys · 63 buys (493.67 τ) / 53 sells (404.60 τ) · net 89.07 τ

## News

- 2026-10-12 · commit · [Merge pull request #254 from skyrocket2026/feat/verifier-release-next](https://github.com/cathedralai/cathedral-sandbox/commit/30bee005c62ed65595027e27a2447dbcef163bc5) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [Merge pull request #253 from skyrocket2026/fix/verifier-review-followups](https://github.com/cathedralai/cathedral-sandbox/commit/5a57fb50ee5dabe012b7859d307cb5870c269a70) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [release(tdx-verifier): expect the Go 1.26.9 build of the next release](https://github.com/cathedralai/cathedral-sandbox/commit/78f631209fc632d10f1e8d1bf73ad557f5c14830) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [release(tdx-verifier): expect the digest of #253's final verifier source](https://github.com/cathedralai/cathedral-sandbox/commit/eb5bf07d6a9d5f459531a0188f938b0b95ea97a2) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [release(tdx-verifier): expect the digest of main after #267 plus #253](https://github.com/cathedralai/cathedral-sandbox/commit/52fdf7ff6deeab8b99837445ad3a6e5cc421f0f5) — cathedralai/cathedral-sandbox
- 2026-10-05 · commit · [docs: mission-first miner README and one canonical operating guide (#…](https://github.com/cathedralai/cathedral-sandbox/commit/6e38541d434bdbb378052e308e636a537cc59607) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Merge pull request #267 from cathedralai/fix/tdx-image-identity-v2](https://github.com/cathedralai/cathedral-sandbox/commit/a22fb1df124ed3e4414335f109f30624152ff548) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Check the verdict's v1 and v2 audit values against the quote in admis…](https://github.com/cathedralai/cathedral-sandbox/commit/c47a6f733ebe8a8a6b09bfac70209d24f8178e57) — cathedralai/cathedral-sandbox

## Use

```bash
m subnets.sn94/info        # live identity + market (snapshot if bt is down)
m subnets.sn94/news        # scraped news
m subnets.sn94/trades      # 24h alpha tape
m subnets.sn94/daily       # daily candles
python3 orbit/subnets/sn94/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
