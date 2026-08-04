# The arena: comparing agentic intelligence

Every agent this module can run is a different bet about how to be useful —
a different system prompt, tool loadout, model, sometimes a whole external
CLI. The arena is how those bets are compared: the same tasks, the same
scratch directory, the same step budget, scored on what the run actually
did rather than on how well it described itself afterwards.

It keeps itself current. A new agent is scored within a minute of coming
online, and the whole field replays once a day. Nobody has to press
anything.

## A match

One agent, one task, one disposable directory.

```
task fixture ──▶ ~/.mod/agent/arena/work/<match>/   (seeded, per match)
                          │
        agent runs, sandboxed to that directory, N steps max
                          │
        trace + files ──▶ scorers ──▶ score ──▶ Elo
                          │
                 scratch directory deleted
```

The task's prompt has `{workdir}` replaced with that directory's absolute
path — tools resolve relative paths against the API's own working
directory, so a task that just said "write notes.txt" would land somewhere
nobody scores. The run is sandboxed to it: an arena match cannot write to
the host.

**The trace is the whole run.** `Agent.run()` returns only its last plan, so
the arena collects steps as they execute (`on_step`) — a file written in
step two still counts when the agent finishes in step five.

## The score

```
score = 0.7 × correctness + 0.2 × reliability + 0.1 × efficiency
```

- **correctness** — the fraction of the task's own checks that passed.
- **reliability** — no errored steps, and it actually finished. Half marks
  for each.
- **efficiency** — the share of the step budget left unspent, and zero if
  the run never finished. Stopping early by falling over is not thrift.

A run that never reached the model at all (no API key, provider down) is
recorded as a **forfeit**: score 0, with the error kept on the match.

## Tasks

Tasks come from the eval registry (`src/evals/`), one arena task per eval
task, keyed `suite#index`. An eval that only declared substring `checks`
scores here unchanged — they become `contains` scorers. A task can also
carry:

- `scorers` — explicit scorer specs (`src/evals/scorers.py`): `contains`,
  `regex`, `tool_used`, `tool_not_used`, `no_errors`, `finished`,
  `max_steps`, `file_exists`, `file_contains`, `file_regex`. File paths are
  relative to the match's scratch directory.
- `setup.files` — `{relative path: contents}` seeded into the scratch
  directory before the run, so every agent faces the identical fixture.
- `steps` — a per-task step budget, overriding the global one.
- `agents` (on the eval, not the task) — restrict which subjects it applies to.

`agentic/files` and `agentic/tools` are the suites written for this: they
cannot be answered from the prompt, only by looking at the directory and
changing it. One of them passes only if the agent wrote *nothing*.

## The rating

Agents are compared pairwise on each task with Elo (start 1200, K 32 split
across the field, scores within 0.02 count as a draw). A round rates every
agent against every other on the same task; comparing raw percentages
across tasks of different difficulty would not.

A **qualifier** is how a newcomer gets on the board without re-running
everyone: it plays the tasks the field has the most records on, and each
incumbent's last score on that same task stands in as their side of the
match. Same prompt, same budget, same scorers — a real comparison, and both
ratings move.

## The background process

`Scheduler` is one daemon thread the API starts at boot. Every tick
(`poll_seconds`, default 60):

1. any agent that appeared in the registry since the last tick is qualified
   immediately;
2. if `period_hours` (default 24) has elapsed since the last round, the full
   round runs.

A round takes a rotating slice of the pool (`tasks_per_round`, default 3)
rather than the whole catalogue, so every task comes around without any one
night running dozens of matches. `max_matches` is the hard ceiling; a round
that hits it is flagged `capped`.

Matches run **one at a time**. The point is a fair comparison, and eight
agents hammering one provider key in parallel is not one.

Off switch: `ARENA_SCHEDULER=0` in the API's environment, or `enabled:
false` in the config.

## Cost

Rounds run on **free models by default** (`free: true`) — a board that runs
itself on a timer must not quietly spend the host's provider credits. Each
match still reads its own meter, so the real cost is on the record either
way, and the leaderboard's `spent` column is the truth about what a field
of agents costs to rank. Set `free: false` (owner) to rank agents on paid
models.

Harness agents (Claude Code, Codex) hand the run to a CLI on the host with
its approval prompts off, so they sit out unless the host sets
`harnesses: true`.

## State

Private, off-tree, under `~/.mod/agent/arena/`:

- `config.json` — the knobs below
- `state.json` — ratings, per-task records, seen agents, season, round log
- `matches.jsonl` — every match, one line each (pruned to the last 5000)
- `work/` — live scratch directories; anything older than an hour is debris
  from a killed process and is cleaned on startup

## API

```
GET  /arena                  the ranked board + what the scheduler is doing
GET  /arena/tasks            the pool, and this season's slice of it
GET  /arena/matches?limit=&agent=&task=
GET  /arena/agents/{name}    one agent's record
POST /arena/run              admin: {agent?, task?} — a match, or a round
POST /arena/config           admin: the knobs, plus scheduler: true/false
```

Config knobs: `enabled`, `free`, `model`, `steps`, `period_hours`,
`poll_seconds`, `tasks_per_round`, `max_matches`, `harnesses`, `agents`,
`suites`.

## CLI

```bash
m agent/arena                          # the board
m agent/arena_tasks
m agent/arena_run agent=builder task=agentic/files#0
m agent/arena_run                      # a whole round
m agent/arena_qualify agent=mynewagent
m agent/arena_config period_hours=6
m agent/arena_scheduler on=false
```
