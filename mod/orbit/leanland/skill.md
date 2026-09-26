# leanland

Read papers with an LLM and leave behind a **lean, cited library of definitions**
that is lowered into Python notebooks, a Rust backend and a Next.js surface.

The library (`lib/*.lean`) is the source of truth. `out/` is generated from it and
never edited. The model drafts definitions; the parser, typechecker and the
source's own numbers decide whether they are kept.

API + console `:50540` (loopback).

## When to reach for it

- "I want the formula from this paper as something I can actually run"
- "the same calculation exists in my notebook, my backend and my frontend and I
  don't trust that they agree" — this is the problem it is for
- keeping *why* you believe a formula: which paper, which equation, which
  assumptions are false in practice
- turning a reading session into artifacts: a notebook per result, a service, a page
- checking that a change to a formula propagated everywhere (`parity`, `drift`)

Not for: general LLM chat (`agent`, `dev`), numerical work needing recursion,
matrices, or state — the language is deliberately first-order and pure.

## The loop

```bash
m leanland/arxiv 1706.03762            # paper -> lit/<key>.md
m leanland/read <key>                  # draft the reading note
m leanland/discuss "what does eq 3 actually let me compute?" paper=<key>
m leanland/elaborate "the thing you want" paper=<key>   # drafts, typechecks, files
m leanland/verify                      # every #example, against the reference
m leanland/parity                      # ...and against every generated target
m leanland/build                       # regenerate out/
```

`elaborate` retries with the compiler's own error text and writes nothing that
does not parse, typecheck, and reproduce the numbers its `#example`s claim. Pass
`write=0` to see the proposal without filing it.

## Writing a definition by hand

```lean
/-- one line saying what it is -/
@[source kelly1956, eq 1]
def kelly (p : Real) (b : Real) : Real :=
  (p * b - (1 - p)) / b
#example kelly 0.6 1 ≈ 0.2 tol 1e-12
```

- types: `Real Int Nat Bool Vec Real Vec Int`; `Nat < Int < Real` widen; `div` and
  `pow` always give `Real`
- forms: `let` (head of body only), `if/then/else`, `sum i in a..b, e`, `v[i]`
  (no space before `[`), application by juxtaposition (`sqrt x`, `max a b`)
- a constant is a def with no parameters: `def pi : Real := 3.14159…`
- no recursion, no state, no I/O
- cite with `@[source <lit key>, eq <n>]`, or mark `@[convention]` when there is
  nothing to cite
- the expected value in an `#example` should be a number **the source states**,
  not one your implementation produced
- special functions (erf, gamma…) are ordinary defs, not primitives — see
  `lib/special.lean`

`m leanland/add "<source>"` runs the same gate as the model does.

## Reading the output

- `verify` — typecheck + examples, plus what nothing is checking (`untested`,
  `unsourced`, `missing_lit`)
- `parity` — `worst_delta` is the largest disagreement between the reference
  interpreter and any generated target. Anything above the example's tolerance is
  a lowering bug, not a rounding one. A target with no toolchain shows in
  `skipped`, never as a pass.
- `drift` — `edited` means someone changed generated code; `stale` means the
  library moved on. Both are fixed by `build`.

## Gotchas

- Application is juxtaposition, so a call ends at the end of its line unless the
  next line is indented further. `let n := len v` followed by an unindented `(…)`
  is two expressions, not a call.
- `[1, 2, 3]` is always `Vec Real`.
- A model that answers with its own info dict is treated as a provider miss.
  There is no key of its own: it borrows the fleet's OpenRouter key.
- The API has no auth and binds to loopback. `route` is `false` in config.json.
