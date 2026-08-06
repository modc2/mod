# examples

Two js artifacts, and where to find wasm ones.

| file | what it shows |
| --- | --- |
| `sort_sum.js` | the whole js contract: `run(input, ctx)`, text in, text out |
| `montecarlo.js` | randomness and a clock, and a result that still replays exactly |

`montecarlo.js` is the one to run twice. It is built out of the two things that
normally make a result uncheckable, and its receipt is stable anyway — because
the seed *is* the randomness and the clock counts calls rather than time.

```bash
m wasmland/publish path=examples/montecarlo.js title="Monte Carlo pi" price=0
m wasmland/run listing=<id> input=200000 seed=1     # claimed
m wasmland/verify <run id>                          # verified — the replay agrees
m wasmland/run listing=<id> input=200000 seed=2     # a different receipt, on purpose
```

## wasm

Any wasm module works, including ones never written for this marketplace — a
WASI command runs and prints, a module exporting `run(ptr,len) -> i64` answers
with text, and a module exporting the game ABI is a game.

The reference pack lives in the arena module, already compiled:

```bash
ls ../arena/src/examples/wasm/          # hello, rps, ttt, nim, markov, mlp, bots
m wasmland/publish path=../arena/src/examples/wasm/hello.wasm title=hello
m wasmland/publish path=../arena/src/examples/wasm/nim.wasm title=Nim
m wasmland/to_arena listing=<the nim listing>   # → orbit/nim, its own mod
```

Their sources (`../arena/src/examples/*.rs`) are the reference for writing your
own: `alloc(len) -> ptr`, and anything returned is one i64 packed as
`(ptr << 32) | len`.
