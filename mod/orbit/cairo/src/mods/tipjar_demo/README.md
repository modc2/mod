# tipjar_demo

A tip jar: anyone can tip (an amount is recorded per tipper and in a running total); the owner set in the constructor can reset the total.

A Cairo contract mod (submod of `cairo`), addressed `cairo.tipjar_demo`. A self-contained Scarb project: `src/lib.cairo` is the contract, `tests/` the snforge tests.

```sh
m cairo.tipjar_demo/build
m cairo.tipjar_demo/test
m cairo.tipjar_demo/vibe prompt="..."
```
