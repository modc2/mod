# agent

**An autonomous coding agent, and a console that shows you what it actually did.**

The interesting part of an agent run is not the paragraph at the end. It is the
twelve tool calls underneath it — what it grepped for, what it read, what it
tried to write and failed to. This module's whole shape follows from taking
that seriously: the loop is a step trace, the console is a reader for that
trace, and the arena scores agents on the trace rather than on how the answer
sounded.

## Three places

```
CHAT    the console — you, an agent, and every call it makes
HUB     everything you keep: AGENTS · LIBRARY · TASKS
ARENA   every agent on the same tasks, one ranked board
```

That is the whole top bar. It used to be five, and the extra two were the
problem: `AGENTS`, `LIBRARY` and `TASKS` are things you keep, not things you do,
and they were sitting in the one row competing with the reason anyone opens the
module. They are now three shelves behind one door, and the hub reopens on the
shelf you were last on.

## The calls are in the conversation

Each tool call the run makes is a row in the transcript, right where it
happened:

```
⚙ ▶ grep   traceBody
⚙ ▶ read   .../app/app/page.tsx
⚙ ▶ bash   npx tsc --noEmit -p tsconfig.json
⚙ ▶ edit   .../app/app/page.tsx                              err
  4 calls · errors · full trace →
```

The name of the tool, the one argument worth reading next to it — a search is
its pattern, a shell call its command, and only a tool with neither falls back
to the path it touched — and an `err` flag when it failed. Open a row and the
arguments it went out with and the result that came back drop in underneath,
without leaving the chat.

The `TOOLS` tab is still there and is still the full read: the same steps end to
end, numbered, without the conversation in between. The difference is that you
no longer have to go there to find out that the agent spent four calls reading
the wrong file.

## What the call cost, and that it happened at all

Under the same message, one line says what that call to the agent cost on
the provider key:

```
$0.00412 · 6 model calls · 41.2k tok · charged $0.00433 · anthropic/claude-opus-5
```

It counts up while the run is going — each model call is priced from the
provider's own catalog and pushed down the run's stream the moment it
resolves — and hovering it breaks the total out call by call. Every run has
a price whether or not anyone is billed for it: an owner's run costs real
money too, it just isn't charged to a ledger. A model the catalog doesn't
price says *unpriced model* rather than `$0.00`, which would read as free.

The other half of getting a call's worth: a run cannot end on a promise.
A model that answers *"sure, I'll read the config and fix the port"* and
stops has done nothing, and the caller reads a plan believing it was the
work. When a run is about to finish with no tool ever having run and a
sign-off written in the future tense, the loop says so once and sends it
back to work. Both halves have to be true — an answer that legitimately
needed no tools is left alone, and a run that actually did something may
close by naming what comes next.

## What an agent is

A box with five swappable parts — prompt, model, toolbox, tool registry, memory
module — all visible in one place (the console's agent box, and `/parts`).
Wire one on the hub's `AGENTS` canvas, or pick one from the rail.

Which one you talk to is a choice made in the chat itself. The agent named in
the console toolbar opens a list of every agent and library prompt on the
host; picking one switches the next message onto it, `★` makes it the agent
your runs land on when you haven't picked, `⧉` opens it as a new agent of your
own, and `✎` edits the ones you may change. **A default is asked for once**,
before the first run, rather than decided quietly: signed in it is filed
against your address (`POST /agents/default`, kept off-tree in
`~/.mod/agent/prefs.json`) and follows the wallet across browsers; signed out
the browser holds it. A pick that can no longer run — a harness whose CLI is
gone, an agent deleted — falls back to the module's own answer instead of
failing every unnamed run.

Editing an agent has two destinations, because most agents you want to change
are not yours to change. **save** writes back to the agent (its owner, or the
host); **save as new** keeps it as it is and files what is on screen under a
name of your own. Open a built-in, or anyone else's agent, and the editor
starts in that second mode — the form is never read-only, it just lands
somewhere you own.

On the canvas the agent is **one node**. Its template requires four
integrations wired into it — a prompt, a model, a toolbox and a memory — and
the node has one input port for each. A port that has nothing on it reads
`required`, and the agent will not save until every port is wired; the list
comes from the agent template itself (`requires` in `src/agents/mod.py`,
reported by `GET /agents/{name}` and `/parts`), not from the console.

The tool registry holds three kinds at once: the 26 tools shipped here, custom
shell tools you describe and parameterise from the console, and every
mod-protocol module on the host as `mod.<name>`. The fleet is potential rather
than loaded — hundreds of modules would drown a prompt, so they sit in the
registry until switched on, and a sandboxed run can never reach one.

Three shipped tools point back at the box rather than out at the world:
`recall` retrieves from the agent's own memory, `remember` writes a fact future
runs will find, and `toolbox` snaps a bundle on mid-run — so an agent that
discovers it needs version control asks for those tools instead of failing and
being re-run with a bigger loadout.

## Scored on what it did

The arena runs every agent over the same tasks, in the same seeded scratch
directory, under the same step budget, and grades the files it left behind and
the steps it took with deterministic scorers. Never an LLM judge.

```
0.7 correctness  the task's own checks
0.2 reliability  no errored steps, and it finished
0.1 efficiency   the step budget it left unspent
```

Agents are then rated pairwise per task with Elo, so the board is a ranking and
not a pile of percentages. It qualifies a new agent within a minute of it
appearing and replays the field daily, on free models by default — a board that
runs itself on a timer should never quietly spend the host's credits. A match
the provider failed is replayed and then voided, not scored as a loss.

See `docs/arena.md`.

## Ports

| | |
|---|---|
| API + MCP | `:50117` (`POST /mcp`) |
| console | `:3117`, base path `/agent` |
| memory | `:50119`, its own process |

## More

- `docs/arena.md` — scoring, Elo, the openarena bridge, the models board
- `docs/credits.md` — metering, margin, MetaMask top-ups (USDC/USDT/ETH on Base or Ethereum), the treasury panel
- `docs/mcp.md` — the 20 MCP tools, same handlers as the REST routes
- `docs/memory.md` — the working/episodic/dialogue/semantic layers
- `docs/models.md` — free models, including WebGPU runs in the visitor's tab
- `docs/privacy.md` — module sealing
- `docs/uploads.md` — the file format the upload panel accepts
