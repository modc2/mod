# tipjar_demo

A tip jar: anyone can tip (an amount is recorded per tipper and in a running total); the owner set in the constructor can reset the total.

A Cairo contract mod (submod of `zkworld`), addressed `zkworld.tipjar_demo`. A self-contained Scarb project: `src/lib.cairo` is the contract, `tests/` the snforge tests.

```sh
m zkworld.tipjar_demo/build
m zkworld.tipjar_demo/test
m zkworld.tipjar_demo/vibe prompt="..."
```
