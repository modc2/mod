# recurse

A tiny, safe playground for **recursion**.

`recurse` computes a few classic recursive functions — factorial, Fibonacci,
and greatest common divisor — each protected by an explicit recursion-depth
guard so a single call can never run away or blow the stack. It is pure
stdlib, stateless, and has no network, no accounts, and no keys.

## What it deliberately does *not* do

The name is about the **shape of the algorithms**, not about self-replication.
This module does not:

- spawn processes or fork,
- call itself over the network or re-invoke the agent,
- schedule, loop, or run anything on its own.

Every function is a pure computation over its arguments that returns a value.
There is nothing here that "runs itself."

## Usage

```
m recurse                    # null call → info()
m recurse/factorial n=10     # 10! computed recursively  → 3628800
m recurse/fibonacci n=20     # the 20th Fibonacci number → 6765
m recurse/gcd a=48 b=36      # Euclid's algorithm        → 12
m recurse/test               # offline tests
```

## Functions

| Function | Arguments | Returns |
| --- | --- | --- |
| `factorial` | `n`, `max_depth=1000` | `n!`, refusing `n > max_depth` |
| `fibonacci` | `n`, `max_depth=1000` | the n-th Fibonacci number (0-indexed) |
| `gcd` | `a`, `b`, `max_depth=1000` | greatest common divisor by recursive Euclid |

`fibonacci` uses linear accumulator recursion (depth exactly `n`), not the
exponential naive form. The `max_depth` guard stays well under CPython's own
recursion limit; a request that would exceed it is refused with a clear error
rather than being allowed to crash.

## Layout

- `mod.py` — the anchor file the orbit loader imports; the `Mod` class is the
  whole public surface.
- `config.json` — module manifest (port `51200`, routes, function list).
- `README.md` — this file.
