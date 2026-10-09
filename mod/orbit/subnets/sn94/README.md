# sn94 — Cathedral ᚄ

CPU sandboxes powering reinforcement learning on Bittensor.

Bittensor subnet **94** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/cathedralai/cathedral-sandbox) · [url](https://cathedral.computer/) · [discord](https://discord.com/channels/799672011265015819/1526241812589711571)

Fleet mods for this subnet: `cathedral`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004512 | -0.00% | -1.03% | -15.02% | 11,674 | 2,299 | 468.43 |

## Last 24h flow

97 trades by 47 coldkeys · 52 buys (228.30 τ) / 45 sells (238.64 τ) · net -10.34 τ

## News

- 2026-10-05 · commit · [docs: mission-first miner README and one canonical operating guide (#…](https://github.com/cathedralai/cathedral-sandbox/commit/6e38541d434bdbb378052e308e636a537cc59607) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Merge pull request #267 from cathedralai/fix/tdx-image-identity-v2](https://github.com/cathedralai/cathedral-sandbox/commit/a22fb1df124ed3e4414335f109f30624152ff548) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Check the verdict's v1 and v2 audit values against the quote in admis…](https://github.com/cathedralai/cathedral-sandbox/commit/c47a6f733ebe8a8a6b09bfac70209d24f8178e57) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Store the GCP quote fixtures as .bin; *.quote is ignored for live evi…](https://github.com/cathedralai/cathedral-sandbox/commit/e1d6f49c70eb6a92d2bc80c09af4ea042cae7418) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Merge remote-tracking branch 'origin/main' into fix/tdx-image-identit…](https://github.com/cathedralai/cathedral-sandbox/commit/9eb29b09542a4310a93ac7402f310ed2fe5958aa) — cathedralai/cathedral-sandbox
- 2026-10-02 · commit · [Add the v2 TDX image identity that leaves out host-set owner fields (…](https://github.com/cathedralai/cathedral-sandbox/commit/428a963a188fc6ed4bfe03da99bc37cc190527c8) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [snp friend probe: an unavailable AMD verifier is inconclusive, never …](https://github.com/cathedralai/cathedral-sandbox/commit/244f9f52eaf4b570886e6d6eab734cecd0b39d78) — cathedralai/cathedral-sandbox
- 2026-10-01 · commit · [Merge pull request #256 from cathedralai/fix/snp-umask-and-kds-thrott…](https://github.com/cathedralai/cathedral-sandbox/commit/de8a061e1641b8e65fc4b1c5f27ff0ae4066e502) — cathedralai/cathedral-sandbox

## Use

```bash
m subnets.sn94/info        # live identity + market (snapshot if bt is down)
m subnets.sn94/news        # scraped news
m subnets.sn94/trades      # 24h alpha tape
m subnets.sn94/daily       # daily candles
python3 orbit/subnets/sn94/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
