# subnets

One mod per Bittensor subnet, regenerated once a day.

```
orbit/subnets/
  mod.py      generator: sync / ls / get / status / install_cron
  base.py     class Subnet — what every subnet mod inherits
  sn0 … sn128 generated mods (config.json, README.md, data.json, mod.py)
```

**Data flow.** `sync` makes one `bt_screener` call plus one local `bt_news` and
`bt_trades` per subnet against `orbit/bt` (:50280). bt already runs the chain
indexer, trade tape and news scraper, so this module adds no new network
dependencies. Running all 129 subnets takes about 2 minutes.

**Daily cron.** `python3 mod.py install_cron` adds one tagged line
(`# subnets daily mod sync`, 05:41 UTC, override with `SUBNETS_CRON`) and is
idempotent. A `flock` stops two syncs from overlapping. Output goes to
`/tmp/subnets-daily.log`. If bt is down the run exits 1 and leaves yesterday's
mods as they were.

**Subnet mods.** `m subnets.sn64/info`, `/price`, `/news`, `/trades`, `/daily`,
`/history`, `/validators`, `/neurons`, `/links`, `/snapshot`, `/refresh`.
These read live from bt and fall back to `data.json` with `stale: true`.

**Rules the generator keeps:**
- `config.json`, `README.md` and `data.json` are rewritten each day, and only when something besides the timestamp changed.
- `mod.py` is written once. Delete its `# GENERATED` first line and the file becomes yours; sync never touches it again.
- If a netuid drops out of the screener, its mod is kept and marked `active: false`. If a netuid is re-registered to a new owner, the old identity is pushed onto `previous`.
- `related` links hand-built fleet mods (`chutes`, `lium`, `targon`, ...). A mod is linked when its config has a `netuid` field or its dir name matches the subnet name.
