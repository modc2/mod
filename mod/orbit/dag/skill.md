# dag

Run a DAG over the mods. Steps are fleet calls — any MCP tool the hub carries,
a mod fn, an HTTP route — wired to each other by `${...}` references and run in
parallel wherever they are independent.

## When to reach for this

* a question needs three or four tools and the answer of one feeds the next
* the same tool has to run once per item in a list somebody else returned
* a composition is worth keeping, naming and running again with new inputs
* you want to know what a multi-call job will cost, and whether it will work,
  before spending any of it

## The model in five sentences

1. A graph is JSON: `{name, inputs, steps, output}`.
2. A step is one call — `tool: "server__tool"` unless it says otherwise.
3. Steps reference each other with `${...}`, and those references are the
   edges; nothing declares a dependency and anything independent runs at once.
4. `foreach` runs one step once per item of a list, in parallel, results in
   order; `expr` steps reshape data between two tools without leaving the fleet.
5. A step that fails skips what depended on it and nothing else.

## The shape of a step

```json
{"id": "risk", "tool": "solana__sol_token", "args": {"mint": "${top.mint}"},
 "foreach": "${top}", "pick": "risk", "retries": 1, "continue_on_error": true}
```

`tool` · `server` · `url` · `call` (`"<mod>/<fn>"`) · `value` · `graph` pick the
kind. `args` · `foreach` · `if` / `unless` · `needs` · `pick` · `where` ·
`sort_by` / `desc` · `limit` · `retries` · `retry_delay` · `timeout` ·
`concurrency` · `continue_on_error` apply to any of them.

References: `${step.field}` · `${inputs.name}` · `${item}` `${index}` ·
`${x.y?}` (missing → null) · `${list.field}` (that field over every element).

## Order of operations

```sh
m dag/tools portfolio            # 1. find the tools — there are hundreds
m dag/plan  graph.json wallet=…  # 2. check names + required args, price it
m dag/run   graph.json wallet=… dry_run=1   # 3. see the resolved arguments
m dag/run   graph.json wallet=…  # 4. spend the calls
m dag/save  report graph.json    # 5. keep it; run by name after this
```

Do not skip `plan`. It reads the live hub, offers the closest real name for a
tool that does not exist, and costs nothing.

## Commands

```sh
m dag                       # the spec and a worked example
m dag/tools <q> server=<s>  # search every MCP tool in the fleet
m dag/servers               # which servers are answering
m dag/plan <graph> k=v      # validate + price, calling nothing
m dag/run <graph> k=v       # run it; extra kwargs are the graph's inputs
m dag/draw <graph>          # execution order as text
m dag/save <name> <graph>   # save (a graph that will not parse is not saved)
m dag/graphs [name]         # saved graphs
m dag/runs / m dag/show <id>  # history, and one run in full
m dag/serve                 # REST + console + MCP on :50810
m dag/health                # is the hub answering
```

`<graph>` is a saved name, a path to a `.json`, or the spec itself.

## MCP

Nine tools: `dag_run` `dag_plan` `dag_tools` `dag_servers` `dag_save`
`dag_graphs` `dag_delete` `dag_runs` `dag_info`. Reachable through the hub as
`dag__dag_run` and so on. `dag_info` carries the full spec and an example —
read it before writing a first graph.

## Traps

* Running is loopback-only until `~/.mod/dag/server.secret` exists — a graph
  can call any tool in the fleet, including the ones that sign.
* Tool names are `server__tool`; the hub's own native tools (`web_search`,
  `web_fetch`, `hub_servers`) have **no** prefix.
* `pick` and `limit` on a `foreach` step apply to the whole list of results,
  which is what makes `pick` a map.
* An upstream tool returning `null` for a field is data, not a bug — use
  `${x.y?}` when a field is genuinely optional.
