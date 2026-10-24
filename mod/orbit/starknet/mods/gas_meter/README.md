# starknet.gas_meter — Gas meter

Starknet gas prices right now, read from the latest block: L2 gas, L1 gas and L1 data gas, in STRK and gwei.

An app on Starknet, made 2026-10-14 with `starknet/new_app`.

| file | what it is |
|---|---|
| `config.json` | title, icon, tag, pitch (the Apps card) and `fns` — which fns are callable |
| `mod.py` | `class Mod` — `facts()` feeds the card, every other fn is your API |
| `app/index.html` | the app itself, plain HTML, served at `/starknet/apps/gas_meter/` |

Add a fn: write it in `mod.py`, add its name to `fns`, call it from the page
with `api('my_fn', {arg: 1})`. No build and no restart.

    curl localhost:51020/starknet/_api/apps/gas_meter/hello?who=you
    m starknet.gas_meter/head
