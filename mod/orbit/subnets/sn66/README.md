# sn66 — conjectures ض

Incentivizing breakthroughs on decades-old open mathematical conjectures

Bittensor subnet **66** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/conjectures-io/conjectures-validator) · [url](https://conjectures.io) · discord `wejh`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002598 | -0.00% | +4.51% | -11.21% | 15,690 | 6,278 | 1,039 |

## Last 24h flow

100 trades by 50 coldkeys · 59 buys (588.00 τ) / 41 sells (449.53 τ) · net 138.46 τ

## News

- 2026-10-19 · commit · [Merge pull request #134 from conjectures-io/hotfix/chat-log-no-omissions](https://github.com/conjectures-io/conjectures-validator/commit/8c202ab8a03efd5bdefa8530893d99ec731ceae2) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Publish no-omissions chat-log packaging guide](https://github.com/conjectures-io/conjectures-validator/commit/70f51fa904ce403fc529bc374b3acc5030bd9ce9) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Merge pull request #132 from conjectures-io/ci/dev-deploy-pipeline](https://github.com/conjectures-io/conjectures-validator/commit/8c2fce4a4bb62c274c0b32bc8e827b94ab711466) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Unlock the deploy account for key logins on servers without PAM](https://github.com/conjectures-io/conjectures-validator/commit/ef384de02b4cf252fde31fe2ec882c89384ac1a2) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Publish a release with generated notes for every production deploy, a…](https://github.com/conjectures-io/conjectures-validator/commit/e71d807458715de35ef87cbba40b065cefb7c8b5) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Deploy dev to the dev server from GitHub Actions after every merge](https://github.com/conjectures-io/conjectures-validator/commit/e8eb4f868a2ab8b11bf223c3ed57a312d60085c5) — conjectures-io/conjectures-validator
- 2026-10-19 · commit · [Document the deploy pipeline, the release process and the rules for a…](https://github.com/conjectures-io/conjectures-validator/commit/8a2613d8b5af38caaa05d146c2b97c7a856e547d) — conjectures-io/conjectures-validator
- 2026-10-18 · commit · [Merge pull request #130 from conjectures-io/codex/mathlib-headers-202…](https://github.com/conjectures-io/conjectures-validator/commit/c50b5d55b0025f7fc164a8e92c4be9beefd75f64) — conjectures-io/conjectures-validator

## Use

```bash
m subnets.sn66/info        # live identity + market (snapshot if bt is down)
m subnets.sn66/news        # scraped news
m subnets.sn66/trades      # 24h alpha tape
m subnets.sn66/daily       # daily candles
python3 orbit/subnets/sn66/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
