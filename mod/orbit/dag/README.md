# dag — run a DAG over the mods

The fleet is three hundred modules and, through the MCP hub, close to seven
hundred tools. Every one of them is a single call. Nothing composes them: to
answer a question that needs four tools you write a script, and that script is
not something the fleet can see, save, check, price, or run again.

This module makes it a document.

```json
{
  "name": "wallet-report",
  "inputs": {"wallet": {"required": true}},
  "steps": [
    {"id": "what", "tool": "solana__sol_account",   "args": {"address": "${inputs.wallet}"}},
    {"id": "held", "tool": "solana__sol_portfolio", "args": {"address": "${inputs.wallet}"}},
    {"id": "top",  "use": "expr", "value": "${held.tokens}",
                   "sort_by": "value_usd", "desc": true, "limit": 3},
    {"id": "risk", "foreach": "${top}", "tool": "solana__sol_token",
                   "args": {"mint": "${item.mint}"}, "pick": "risk"}
  ],
  "output": {"usd": "${held.total_usd}", "top": "${top.symbol}", "risk": "${risk}"}
}
```

```
- what  [mcp] solana__sol_account
- held  [mcp] solana__sol_portfolio
    - top  [expr]            <- held
      * risk  [mcp] solana__sol_token   <- top
```

`what` and `held` do not need each other, so they leave together. `top` cuts
the holdings down without leaving the fleet. `risk` fans out over what is left
— one call per token, in parallel — and the run costs about as long as its
slowest call rather than the sum of five.

## The references are the edges

No step declares a dependency. `${held.tokens}` is read off the argument, and
that is the edge. A graph is therefore always consistent with what its steps
actually use, which is the one thing a hand-wired graph is reliably not.

| reference | what it is |
|---|---|
| `${step.field}` | another step's output — `${price.prices[0].usd}` |
| `${inputs.name}` | a run parameter |
| `${item}` `${index}` | the current element, inside a `foreach` |
| `${x.y?}` | missing is `null` instead of an error |
| `${list.field}` | that field over **every** element of a list |
| `${env.NAME}` | one environment variable (anything that reads like a credential is not visible) |

A string that is *exactly* one reference keeps its type — `"${item}"` hands the
next tool a dict, not the word `dict`. A reference inside a longer string
interpolates.

## Five kinds of step

| kind | written as | what it calls |
|---|---|---|
| `mcp` | `tool: "server__tool"` | any MCP tool in the fleet, through the hub — **the default** |
| `mod` | `call: "shelf/space"` | a mod fn, in this process |
| `http` | `url: "…"` | any URL, including a module's own REST route |
| `expr` | `value: …` | nothing — it reshapes what upstream already returned |
| `graph` | `graph: "saved-name"` | another saved graph, as one step |

`expr` exists so a composition never has to leave the fleet to do something
small. It is declarative — `value`, then `where`, `sort_by`/`desc`, `limit`,
`pick` — and there is no `eval` anywhere in this module. Those same five
shaping fields work on **any** step, so `pick: "risk"` on a tool call stores
the one field you wanted instead of the whole document.

Per step: `foreach`, `if`/`unless`, `needs`, `retries`, `retry_delay`,
`timeout`, `concurrency`, `continue_on_error`.

## Plan before you run

```sh
m dag/plan examples/wallet.json wallet=9WzDXwB…
```

`plan` parses the graph, finds cycles, infers the edges, and then checks every
tool name and every required argument against the fleet **as it is right now**:

```
error · held — no tool named 'solana__sol_portfoli' in the fleet —
        did you mean solana__sol_portfolio, solana__sol_price, solana__sol_program?
error · risk — solana__sol_token requires 'mint' (the mint address)
```

It prices the run — `3 call(s) in 3 wave(s); risk fans out, so the real count
depends on what upstream returns` — and calls nothing. With hundreds of tools
in the catalogue, a tool name that does not exist is the normal failure, and
finding it at step 7 of 9 costs six calls and whatever they already changed.

`dry_run` is the other half: it walks the graph for real, resolving every
`${...}` against actual upstream shapes, and reports what each step *would*
have called with which arguments. Still no calls.

## Failure is local

```
held    ok       423ms
top     ok         2ms
risk    failed    69ms   solana__sol_token returned an error: mint must be base58
report  skipped    0ms   risk did not succeed
alerts  ok       110ms
```

A step that fails does not stop a branch that never depended on it. Everything
downstream is `skipped` and each skip names the step that caused it, so a
failed run reads as one cause and its consequences instead of nine unrelated
errors. `continue_on_error` on a step turns its failure into a value.

Retries are for a call that might work next time; a refusal that will refuse
again — an unknown tool, a bad argument, a missing fn — is not retried.

## Using it

```sh
m dag                                    # the spec, and a worked example
m dag/tools polymarket                   # what can be called, and with what
m dag/servers                            # which MCP servers are answering
m dag/plan examples/research.json q="…"   # check it and price it
m dag/run  examples/research.json q="…"   # spend the calls
m dag/save research examples/research.json
m dag/run  research q="…"                 # by name, from then on
m dag/runs                                # history
m dag/show <run-id>                       # one run, every step
m dag/serve                               # API, console and MCP on :50810
```

Over HTTP — the same nine operations, and the same answers:

```sh
curl -s localhost:50810/tools?q=portfolio
curl -s -XPOST localhost:50810/plan -d '{"graph":"research","inputs":{"q":"…"}}'
curl -s -XPOST localhost:50810/run  -d '{"graph":"research","inputs":{"q":"…"}}'
curl -s localhost:50810/runs/<id>
```

And as an MCP server, which is how an agent uses it: `dag_tools` to find the
tools, `dag_plan` to check the graph it wrote, `dag_run` to spend the calls,
`dag_save` to keep it. The hub aggregates this module like any other, so those
nine tools are reachable as `dag__dag_run` and friends from anything already
pointed at `:50360/mcp`. A graph may contain a step that runs a graph; the
depth travels with the request and stops at `DAG_MAX_DEPTH`.

## The console

`http://127.0.0.1:50810/dag` — the fleet's whole tool catalogue on the left,
searchable, click one to add a step with its required arguments stubbed in; the
graph in the middle with **plan**, **dry run** and **run**; the steps landing
one by one on the right, with saved graphs and history under them.

## Why it binds loopback

A graph step can call any tool in the fleet, and some of those tools sign
transactions. The MCP hub gates on the caller and trusts this box — so a dag
server on a public port is a way to spend that trust from outside it.

Running, saving and deleting therefore need a bearer token matching
`~/.mod/dag/server.secret`, or a caller on this box that is not being proxied
(an `X-Forwarded-*` header counts as remote). Reads — the catalogue, saved
graphs, run history, and `dry_run`, which spends nothing — are open. The config
sets `route: false`; reach it over an SSH tunnel, or set a secret and route it
behind that.

## State

```
~/.mod/dag/graphs/<name>.json    what to run
~/.mod/dag/runs/<id>.json        what happened, written as it happens
~/.mod/dag/server.secret         optional; presence turns the gate into a token
```

A run record is written after every step, so a long run can be followed with
`GET /runs/<id>` while it is still going, and a run that is killed halfway
still leaves an account of itself.

## Notes

* Steps are routed through the MCP hub at `:50360`, which is also what wakes a
  scaled-to-zero module mid-run. A step with an explicit `url=` speaks MCP
  directly and needs no hub.
* An MCP tool result is an envelope — content blocks, `isError`,
  `structuredContent`. It is opened here, once, so a downstream `${...}` indexes
  into the payload; `isError` becomes a failed step rather than a value that
  looks fine.
* This module's python package is `dagsrc`, not `src`. Three other modules in
  the fleet ship a top-level `src`, and whichever imports first wins for the
  whole process. A `mod` step also restores `sys.path` after the call, because
  modules routinely put their own directory at the front of it.
* `python3 -m dagsrc.mcp` runs the MCP server over stdio, for a client that
  wants it that way.
* Caps, all overridable: 200 steps, 200 `foreach` items (`DAG_MAX_FANOUT`),
  8 steps in flight (`max_parallel`), depth 3 (`DAG_MAX_DEPTH`), 500 run
  records (`DAG_KEEP_RUNS`).
