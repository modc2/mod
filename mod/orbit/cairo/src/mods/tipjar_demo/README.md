# tipjar_demo

make a tip jar called tipjar_demo: anyone can tip (records an amount per tipper and a total), the owner set in the const

A Cairo contract mod (submod of `cairo`), addressed `cairo.tipjar_demo`. A self-contained Scarb project: `src/lib.cairo` is the contract, `tests/` the snforge tests.

```sh
m cairo.tipjar_demo/build
m cairo.tipjar_demo/test
m cairo.tipjar_demo/vibe prompt="..."
```
