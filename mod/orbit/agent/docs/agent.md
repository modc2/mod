# The agent protocol

**An agent is a box of five swappable parts. A run is a step trace. Everything
else in this module reads or writes one of those two shapes.**

This page is the root of the docs: it explains the box, the run, the events a
run streams, and who may do what — and points at the sibling page for every
subsystem that grew big enough to need its own. If you read only one file to
integrate against the module, read this one.

```
GET  /parts          what is in the box right now
POST /run            run the box, blocking
POST /run/stream     run the box, every step live (SSE)
GET  /tasks          every run the server remembers
GET  /agents         the registry the box is picked from
```

The API listens on `:50117` and rides the gateway at `/agent/api`. The same
surface is spoken as MCP on `POST /mcp` (see `mcp.md`), and every route is
also a `Mod.forward(action)` call — REST, MCP and the CLI are three doors into
one dispatch.

## The box

An agent is not a subclass; it is a composition. Five parts, each with its own
registry, each swappable without touching the others:

| part | what it is | where it comes from |
|---|---|---|
| **prompt** | the goal the model is aimed with | the agent's own `goal`, a library prompt, or a per-run override |
| **model** | the intelligence | a provider (`openrouter`, `venice`, `liquidai`, `liquidai-cloud`, `browser`) and a model id on its catalog |
| **toolbox** | which tools the model is *offered* | named bundles, snapped on and off |
| **tools** | the registry the toolbox draws from | 26 shipped tools + custom shell tools + every fleet module |
| **memory** | what persists between runs | a memory module: `default` (persists) or `ephemeral` (retrieves, writes nothing) |

`GET /parts` answers "what is in the box" component by component — which
memory module is attached, which tools are active and whether they are
filtered, which boxes are snapped, which model will be called — and each entry
names the sub-registry it came from, so a UI can offer the swap. The box also
publishes `requires`: the integrations an agent template must have wired in
(prompt, model, toolbox, memory) before it runs.

Three of the shipped tools point back into the box rather than out at the
world:

- `recall` — retrieve from the agent's own memory,
- `remember` — write a fact future runs will find,
- `toolbox` — list the bundles and snap one on mid-run,

so an agent that discovers it needs version control asks for those tools
instead of failing and being re-run with a bigger loadout. A sandboxed run
cannot pull the fleet in this way.

The fleet deserves its own sentence: every mod-protocol module on the host is
a potential tool, addressed as `mod.<name>`. Hundreds of modules would drown a
prompt, so they sit in the registry unloaded until switched on — the loadout
(`POST /tools/select`, toolbox snaps) decides what the model is *offered*;
`run_plan` will still execute any tool name the model emits.

## The registry

Agents live as files — `src/agents/<name>/mod.py` — and as records behind one
CRUD surface:

```
GET    /agents            every agent, with owner, default pick, harness flag
GET    /agents/{name}     one agent's config
POST   /agents            create (signed-in)
PUT    /agents/{name}     update in place (owner of the item, or the host)
DELETE /agents/{name}     remove (same standing)
POST   /agents/import     install a shared agent by CID
```

**Ownership** is per item and defaults upward: the owner of an agent (or a
prompt, a note, a toolbox, a graph) is the address that created it; an item
with *no* recorded owner belongs to the **host** — the module owner — which is
how the shipped agents and seeded prompts answer to somebody. Everyone manages
their own; the host manages everything.

**The default agent** — the one an unnamed run lands on — is the caller's own
choice, asked for once in the console rather than decided quietly.
`POST /agents/default` files it per address in `~/.mod/agent/prefs.json`
(off-tree, like all state that names a person); signed out, the browser holds
it. A pick that can no longer run — a harness whose CLI is gone, a deleted
agent — falls through to the module's answer instead of failing every run.

**Harness agents** carry a `harness` attribute (`claude`, `codex`, …) and run
a real CLI on the host's own shell instead of the LLM loop. The CLI's event
stream is translated into the same step vocabulary below, so a harness run
renders like a native one — but starting one is gated to the module owner and
the addresses that harness's own console vouches for (`GET /whoami` returns
the list, `GET /harnesses` what is installed).

## A run

```json
POST /run/stream
{"query": "read config.json and fix the port",
 "agent_type": "default",
 "provider": "openrouter", "model": "anthropic/claude-sonnet-4.5",
 "toolbox": "code",
 "session": "b3f2…",
 "key": "<mod-protocol token>"}
```

Everything except `query` is optional. The fields worth knowing:

| field | meaning |
|---|---|
| `agent_type` | which agent off the registry; unset = the caller's default pick |
| `provider`, `model` | override the agent's own; unset resolves local-first |
| `toolbox` / `toolboxes` | snap bundles on for this run only |
| `tools` | pin the exact tool list instead |
| `prompt` | system-prompt override — a library prompt or free text |
| `memory` | memory module for this run (`default`, `ephemeral`, dotted path) |
| `memory_ids`, `tool_ids` | library notes / installed tool docs injected as context |
| `steps` | tool-step budget (default 10) |
| `free` | free mode — the run may only use models that cost nothing |
| `session` | conversation id — makes the run a remembered exchange (see `memory.md`) |
| `images` | pasted images as data-URLs, for a vision model |
| `key` | the caller's token — identity, policy, billing |

`POST /run` is the same request blocking; it returns the final plan plus a
`task_id`. Prefer the stream: the interesting part of a run is not the
paragraph at the end.

### The step

The model's loop speaks one shape. Each turn it emits

```
<STEP>{"tool": "grep", "params": {"pattern": "port", "path": "config.json"}}</STEP>
```

and the executed step is recorded as `{tool, params, result}` — or
`{tool, params, error}` when the tool failed, or `{"tool": "error"}` when the
*model call itself* failed (that distinction is what the arena scores on).
Prose between steps becomes a `response` step. The run ends on

```
<STEP>{"tool": "finish", "params": {"summary": "the answer, written to the user"}}</STEP>
```

— note that a `finish` step carries its answer in `params.summary`, not in
`result`; anything reading a trace must look there. One rule guards the
finish: **a run cannot end on a promise.** A model about to finish with no
tool ever having run and a sign-off in the future tense ("I'll read the config
and fix the port") is told so once and sent back to work. An answer that
legitimately needed no tools is left alone.

### The stream

`POST /run/stream` is Server-Sent Events. Three of the event types are
ephemeral — they show the run *happening* and are never recorded — the rest
are the record itself:

| event | meaning |
|---|---|
| `token` | the model's raw output as it streams off the provider (ephemeral) |
| `model_start` | a model call just went out — "thinking" starts when the model does (ephemeral) |
| `tool_start` | a tool call is about to execute; its `step` lands when it returns (ephemeral) |
| `step` | a tool step just executed — the recorded `{tool, params, result}` |
| `usage` | what that model call cost, priced off the provider's catalog (see `credits.md`) |
| `chain_step` | a chain stage is starting |
| `model_request` | provider `browser` only — the tab is asked to generate (see `models.md`) |
| `done` | finished; carries `result` and the `task_id` |
| `error` | the run failed; a `code: 403` means standing, not breakage |

The connection is kept alive with `: ping` comments every 15 s, and a run
survives the page closing — which is what the task registry is for.

### Tasks

Every run registers server-side: `GET /tasks` lists them running-first with
live step count, current tool and the path it is on right now; `GET
/tasks/{id}` adds the step trace. This is the module's memory of *work*, as
opposed to the memory module's memory of *facts and conversation* — a
conversation is saved separately, per address, under `/conversations`.

## Standing — who may do what

Identity is a **mod-protocol token**: a base64url JSON envelope
`{data: {scope: "agent"}, time, key, signature}` where `signature` is an
EIP-191 `personal_sign` over the compact `{"data":…,"time":…}` by the address
in `key`, verified server-side by the fleet's shared auth module and expiring
after 24 h. Pass it as `key` in a body or query; `GET /whoami` tells you who
the server thinks you are and what that is worth. A bare address is a claim,
not proof, and is honored only on a local/dev box with no verifier configured.

The gradient, coarsest first:

- **anyone** — reads: the registry, the library, the arena boards, `/parts`,
  the docs, the module audit surface (`privacy.md`).
- **run policy** — a run spends model calls, so `/run` answers to the host's
  access and credit policy (`credits.md`): the host runs on the house key,
  a signed-in caller on their prepaid credits or their own pasted key.
- **signed-in** — creating things that carry your name: agents, prompts,
  notes, custom tasks, graphs, conversations.
- **item owner or host** — editing and deleting those things.
- **owner (host)** — harness runs, the provider keys, ACL grants, sealing,
  the gauntlet: anything that is the host's own shell or wallet.

## Sharing

Everything worth keeping is pinned to localfs and addressed by CID — prompts,
memory notes, toolboxes, agents, graphs, finished conversations. A CID is
minted from the content alone, so identical prompts share one and an edit
re-mints. Each collection has an `/import` route that installs from a CID, and
the console renders CIDs as QR codes — sharing an agent is showing someone a
square. `uploads.md` documents the one file format the upload door accepts.

## The rest of the protocol

| page | subsystem |
|---|---|
| `graph.md` | **FLOW** — the graph protocol that *connects* agents (gates, judges, routers, joins, loops); a graph never builds an agent |
| `arena.md` | agents scored on their traces — deterministic scorers, pairwise Elo, the models board, writing tasks |
| `memory.md` | the memory subsystem — working / episodic / dialogue / semantic layers, one retrieval ranking, its own process on `:50119` |
| `models.md` | providers and free models, including a model running in the visitor's own tab over WebGPU |
| `credits.md` | metering, margin, prepaid top-ups from MetaMask (USDC/USDT/ETH on Base or Ethereum) |
| `mcp.md` | the same API spoken as Model Context Protocol — one endpoint, 25 tools |
| `privacy.md` | the fleet audit surface, and sealing a module so a public push carries ciphertext |
| `uploads.md` | the file format for bringing your own prompt, tool doc, note or agent |

All of these are served live: `GET /docs/pages` lists them, `GET /docs/{name}`
returns one — the file you are reading is `GET /docs/agent`. (`GET /docs`
itself is the interactive route explorer.)
