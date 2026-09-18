# The graph protocol

**A graph connects agents. It does not build one.**

That sentence is the whole design, and everything below follows from it. An
agent is a prompt, a model, a toolbox and a memory module; those four belong to
the agent and are settled where the agent lives — the registry, the rail's
editor, `POST /agents`, `src/agents/<name>/mod.py`. A graph starts after that.
It picks agents up whole and says the thing that has nowhere else to live:
what runs after what, what runs at the same time, and what is allowed through.

```
▷ input ─▶ ◆ architect ─▶ ⊘ gate ──pass──▶ ◆ builder ─▶ ⚖ judge ──pass──▶ ◎ output
                             │                             │
                             └──fail──▶ (stops)            └──fail──▶ ↻ loop ─▶ architect
```

The canvas used to draw the other thing: one AGENT node with a Prompt, a Model,
a Toolbox and a Memory wired into four fixed ports. That is a form drawn as a
graph — four fields that could only ever be wired one way — and it competed
with the two real forms beside it for the same job. A graph earns its shape
when the thing being drawn *is* a graph. The relationships between agents are;
the inside of one is not.

## Three nouns

| | |
|---|---|
| **node** | an agent, or one of the control kinds below |
| **edge** | where a message goes when it leaves a port |
| **port** | a named output a node can answer on — `pass`/`fail`, `out`/`err`, a route's own name |

A **message** is what crosses an edge:

```json
{"text": "what the sending node produced",
 "data": {"steps": 7},
 "from": "n3", "port": "out", "ok": true,
 "agent": "reviewer", "hops": 2}
```

`text` is the payload every node reads and writes. For an agent node it is the
run's **answer** — the finish summary, or the last thing it responded with —
never the step trace: handing the trace down an edge would make every
downstream agent read a log instead of the work.

## Node kinds

Read them live from `GET /graph/kinds`, which is what the console's palette is
generated from. The source of truth is `src/graph/protocol.py`.

| kind | ports | what it does |
|---|---|---|
| `input` ▷ | `out` | the entry — carries the caller's query |
| `agent` ◆ | `out`, `err` | runs one agent from the registry on the message |
| `gate` ⊘ | `pass`, `fail` | a fixed predicate on the message |
| `judge` ⚖ | `pass`, `fail` | an agent answers one yes/no question about it |
| `router` ⑂ | one per route, + `else` | a gate with more than two answers |
| `join` ⊕ | `out` | waits for the branches and merges them |
| `tool` ◇ | `out`, `err` | one tool call, no model in the loop |
| `loop` ↻ | `out`, `done` | sends it round again, up to a limit |
| `human` ✋ | `out` | parks the branch for a person |
| `output` ◎ | — | the graph's answer |

An **agent node** names an agent and nothing else about it, apart from three
per-position overrides: `prompt` (what this agent does *at this point* — it is
prepended to the incoming message, not a persona), `model` and `steps`. Leave
them empty and the agent runs exactly as it was built.

## Gates are predicates, not code

A gate tests a fixed vocabulary — `contains`, `not_contains`, `matches`,
`equals`, `starts_with`, `ends_with`, `longer_than`, `shorter_than`, `gt`,
`lt`, `empty`, `not_empty`, `ok`, `failed` — against a field of the message
(`text` by default, or `agent`, `port`, `from`, or `data.<key>`).

```json
{"op": "contains", "value": "SHIP IT", "field": "text"}
```

There is no expression to evaluate, and there never will be: a graph is a
document people share by CID, and a gate that took code would be a code path
anyone could smuggle anything through. Two consequences worth knowing:

- **An unknown op never passes.** A rule nobody implements must not be a rule
  that silently lets everything by.
- **No rules is an open gate.** An empty condition is not a condition, and a
  half-written gate that blocked everything would look exactly like a working
  graph that produced nothing.

For anything a string test cannot settle — *"is this review actionable?"* —
use a **judge**, which spends a model call to decide. A judge picks the port;
what travels on is still the work being judged, not the verdict, or the next
agent would review the review.

## Running one

Execution is a **message net run in waves**, not a pipeline with branches
bolted on. Nodes never call each other: they emit onto ports, and edges decide
everything else. Everything ready at the same moment runs at the same
moment — a fan-out of three agents is three agents running — and the wave ends
when they have all emitted.

```
POST /graphs/run          → {answer, outputs, parked, trail, nodes_run, ms, charged}
POST /graphs/run/stream   → the same, as SSE, node by node
```

Four rules are worth knowing before you draw one:

- **A join fires when nothing more can reach it.** `wait: all` means every
  inbound edge that can still deliver — a branch a gate blocked cannot, so a
  join never hangs on one.
- **A cycle must pass through a Loop node**, which counts its passes and cuts
  at `max`. Validation refuses any other cycle: that is the difference between
  a retry and a bill.
- **Two budgets stand between a wiring mistake and a spend**: `max_nodes`
  (total node firings, default 40) and a per-node visit cap.
- **A graph is several agent runs**, so it answers to exactly the policy one
  run does — owner, granted address, or signed-in with credits — and it is
  metered into one task in the run registry rather than a dozen orphans.

A **human** node never blocks a thread. It files what reached it and the branch
ends there, parked; the result says which branches are open. Approving one is a
separate act, not a timeout.

## Validation

`POST /graphs/validate` says what is wrong before anything runs, in words:

- **errors** stop a run — an agent node with no agent, an agent that is not in
  the registry, an edge to a node that isn't there, a cycle with no loop in it.
- **warnings** are shapes that work but rarely mean what was drawn — a node
  nothing reaches, a gate with no rules, a router with no routes.

## Storage and sharing

Graphs are private user state: off-tree in `~/.mod/agent/graphs.json`, owned by
the address that saved one, and pinned to localfs so a flow can be handed over
as a CID (`POST /graphs/import`). Editing a shipped starter **forks** it rather
than changing what everyone else opens. Only the fields the protocol declares
are stored — a saved graph is a document other people run, so nothing rides
along in it uninspected.

The three starters are the old chain presets (`src/agents/chains.json`), which
were already pipelines — a list of agents each feeding the next — and are the
honest floor of a graph language rather than a separate feature.

## Surfaces

| | |
|---|---|
| `GET /graph/kinds` | the protocol: kinds, ports, fields, gate ops |
| `GET /graphs` · `GET /graphs/{id}` | what is saved |
| `POST /graphs` · `DELETE /graphs/{id}` | save (sign-in) / delete (yours) |
| `POST /graphs/validate` | what is wrong with one |
| `POST /graphs/run` · `/run/stream` | run a saved one, or one off the canvas |
| MCP | `agent_graphs`, `agent_graph_save`, `agent_graph_run` |
| console | HUB → AGENTS → **FLOW** |
| code | `src/graph/protocol.py` · `runner.py` · `mod.py` · `tests/test_graph.py` |
