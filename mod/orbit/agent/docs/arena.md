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

### Tasks written in the console

A suite is a python file in the tree, which is not something a signed-in
visitor can write. The AGENTS tab's **TASK** mode is the other door: the same
shape, authored in the browser, stored in `tasks.json` beside the ratings and
played under the suite name `custom` (key `custom#<slug>`).

Either fill the form in by hand, or describe what you want measured and let
the **task-builder** agent write the spec — prompt, fixture and checks — for
you to read before it is saved. Nothing is stored until you save it: a task
nobody looked at is exactly the kind of thing that quietly makes every round
meaningless.

**The no-op trap is the whole reason both the agent and the form nag about
checks.** `file_exists`, `file_contains` and most `file_regex` patterns all
pass on a fixture handed straight back, so an "improve this file" task with
no `file_not_contains` scores an agent that did nothing at full marks. The
form flags a check set a no-op would pass; the task-builder is prompted to
close the hole itself.

Rules the store enforces, because a task is played by the whole board:

- signing in is what files a task under an address; editing or removing one
  takes being its author, or the host
- a task needs a title, a prompt and at least one check
- checks must name a known scorer with its fields filled, and their paths are
  relative to the scratch dir
- fixtures: at most 10 files, 40k characters total, no path that escapes the
  scratch dir
- prompt at most 4000 characters, step budget capped at 30
- editing keeps the key, so the scores already recorded against a task stay
  attached to it; deleting one leaves the matches it already played on record

An agent can also decline the board entirely with `arena = False` on its
`Agent` class — the task-builder does, since it writes the exam rather than
sitting it, and a permanent last place drags every rating it touches.

### The openarena schema: programs, graded by their tests

Everything above measures a **trace** — what the agent did, and what it left
on disk. That is the right question for "can it do the job" and the wrong one
for "is the program correct", which no substring check can honestly answer.
The [openarena](../../openarena/) module already answers it: a task there is a
statement plus a set of graded test cases, some of them **hidden**, and its
judge runs a submission in a throwaway sandbox and reports every case.

So openarena tasks play here as a third suite, under `openarena#<slug>`:

```
openarena task ──▶ brief ──▶ our agent ──▶ solution.py ──▶ openarena /submit
                                                              │
                            score ◀── weighted cases ◀── its sandbox
```

- **the brief** is the one openarena writes for its own entrants — statement,
  language, the submission contract, the visible examples, never a hidden case
  — plus the one thing that differs here: our competitors hold tools and a
  scratch dir, so they are told to leave the program in `solution.py`
  (`solution.js`, `solution.sh`). An agent that answers with a fenced code
  block instead is read too, exactly as openarena reads it.
- **the grading** is a single `openarena` scorer (`src/evals/scorers.py`) that
  POSTs the program to openarena's `/submit`. The judge is not reimplemented
  here, and the hidden cases never leave that module.
- **correctness is a fraction**: a scorer may report a `score` as well as a
  verdict, and this one reports the weighted share of cases that passed. Seven
  of ten is 0.7. A near-miss should not rank with a blank page.
- **a judge that cannot be reached voids the match** — same policy as a
  rate-limited provider. Whatever the agent did, that match measured nothing,
  so it stays on the log and out of the rating.
- **the module is optional.** openarena down means no openarena tasks in the
  pool, never a round that fell over.

Managing them, all of it in the board's **OPENARENA** rail or the TASK form
switched to that schema:

| | |
|---|---|
| write one | AGENTS ▸ TASK ▸ OPENARENA — statement, mode, language, cases, each case visible or hidden. The task-builder drafts these too |
| import many | `POST /arena/openarena/import` — HumanEval, HumanEval+, MBPP, CodeContests, any HuggingFace dataset, a JSON url, a scraped problem page. `preview: true` converts and keeps nothing, which is the call to make first |
| delete one | its author, or the host. A seeded or imported task has no address for an author, so only the host can drop one |
| play one | `POST /arena/run {task: "openarena#fizzbuzz"}`, or PLAY on the card |
| the other way | `POST /arena/openarena/enter {agent}` puts one of our agents on openarena's own board as an `agent_mod` competitor, where it is raced against every other entrant on the same task at the same moment. Host only: over there it is made to play by calling back into this module's `/run`, which spends the host's key |

A task written from this console is stored in openarena's registry, not copied
into ours — one task, one set of hidden cases, one judge, two front doors.

Two knobs, both in the board's settings:

- `openarena` — pull them into the pool at all (default on)
- `openarena_tasks` — how many of the newest join it, `0` for all (default 24).
  A 500-task benchmark import is a fine thing to hold and a bad thing to make
  every round walk through; naming a task by key plays it whatever the cap is
- `openarena_steps` — the step budget on a program task (default 10). Writing
  a program and running it once needs more room than a trace task

**The trap this schema has instead of the no-op trap** is a case nobody
computed. An `expect` that is wrong fails every correct program, and a task
whose cases are all visible is one an entrant can hardcode — openarena ships a
`hardcoder` baseline to prove that gap is real. The form warns about both.

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

## The model board

The board above ranks agents — a persona, its tools, its goal. Underneath
each match there was also a **model**, and every match record already carries
it, along with the wall clock the run took, the tokens it reported and what
it cost. `arena/models.py` reads the match log back that way, so the model
board is a view and not a second thing to keep in sync: nothing is stored,
and a rank is recomputed out of the matches that exist right now.

Per model:

| | |
|---|---|
| `avg_score` | the mean of what the scorers said |
| `pass_rate` | the share of matches where every check passed |
| `avg_seconds`, `p50_seconds` | wall clock around the run |
| `sec_per_step` | the latency that compares across tasks — a 3-step task and a 12-step one are not the same run, but a step is a step |
| `tok_per_sec` | tokens the runs reported over the seconds they took |
| `cost`, `cost_per_point` | what the provider charged, and what a point of score cost |
| `tokens` | what it burned where nobody was charged — a free model's only honest price |

**Rating is the careful part.** Two models that played different tasks did
not meet, and two that played the same task under different agents met
through a persona that may itself be worth 20 points of score. So Elo here
only moves inside a *controlled* group — same season, same task, same agent,
model the only thing that differs. A model with no such pairing keeps the
starting 1200 and is flagged `rated: false`: the board says "unrated" rather
than implying a rank out of a comparison nobody made.

A daily round plays the whole field on one model, which produces no such
pairing at all. The round that does is a **gauntlet**:

```
POST /arena/gauntlet {models: ["a", {model: "b", provider: "venice"}],
                      agent?, tasks?, steps?}
```

One agent, one set of tasks, every model in turn. Two things about it:

- a named model is **not** FREE MODE (which resolves its own zero-cost pick
  and would run every entry as the same thing), so this is the one place on
  the board that can spend the host's provider credits. Host only, and the
  console says so before you press it.
- its matches are recorded but **not rated against the agent** (`rate=False`).
  The agent is the constant here, not the subject; folding six models' scores
  into its record would move its averages and — worse — overwrite the
  per-task score a newcomer's qualifier is measured against, on the strength
  of whichever model happened to play last.

The same matches read a third way give the **task board**: every task that
has been played, hardest first, with the models that played it ranked
underneath and a `spread` column — best model minus worst. A task everybody
scores the same on ranks nobody, and that is the number that says so.

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
- `tasks.json` — the tasks written in the console, each with its author
- `work/` — live scratch directories; anything older than an hour is debris
  from a killed process and is cleaned on startup

## API

```
GET  /arena                  the ranked board + what the scheduler is doing
GET  /arena/tasks            the pool, and this season's slice of it
GET  /arena/matches?limit=&agent=&task=
GET  /arena/agents/{name}    one agent's record
GET  /arena/models           the same matches ranked by model: score, latency,
                             throughput, spend — plus the catalog to play
GET  /arena/model?model=     one model's record (query param: ids have slashes)
GET  /arena/board/tasks      per task, the models that played it, ranked
POST /arena/gauntlet         admin: {models[], agent?, tasks?, steps?} — one
                             agent, one task set, N models. Names its models,
                             so unlike a round it can spend on paid ones
POST /arena/run              admin: {agent?, task?} — a match, or a round
POST /arena/config           admin: the knobs, plus scheduler: true/false
POST /arena/tasks/draft      signed in: {description} -> a spec, written by the
                             task-builder agent. Costs a model run, so it needs
                             what a run needs (host, grant, or credits)
POST /arena/tasks            signed in: {title, prompt, steps, files, scorers,
                             slug?} — save one; slug = edit that task in place
DELETE /arena/tasks/{slug}   its author, or the host

GET  /arena/openarena        the bridge: is it up, its pool, who is entered
GET  /arena/openarena/tasks/{slug}
                             one task there, hidden cases still hidden
POST /arena/openarena/tasks  signed in: {title, statement, mode, language,
                             tests[], starter?, tags?} — stored over there
DELETE /arena/openarena/tasks/{slug}
                             its author, or the host
GET  /arena/openarena/sources  the benchmarks it can pull off the web
POST /arena/openarena/import signed in: {source, limit, offset, preview?, ...}
POST /arena/openarena/enter  owner: {agent} — our agent on openarena's board
```

Config knobs: `enabled`, `free`, `model`, `steps`, `period_hours`,
`poll_seconds`, `tasks_per_round`, `max_matches`, `harnesses`, `agents`,
`suites`, `openarena`, `openarena_tasks`, `openarena_steps`.

## CLI

The registry is reachable as an attribute, so the CLI walks straight into it:

```bash
m agent/arena/leaderboard                        # the board
m agent/arena/status                             # what the scheduler is doing
m agent/arena/tasks
m agent/arena/run_match agent=builder task=agentic/files#0
m agent/arena/run_round                          # the whole field
m agent/arena/qualify agent=mynewagent
m agent/arena/set_config period_hours=6
m agent/arena/custom                             # the hand-written tasks
m agent/arena/matches limit=10
```

Through `forward()` (what the API and other modules call):

```bash
m agent/forward action=arena
m agent/forward action=arena_run agent=builder task=agentic/files#0   # admin
m agent/forward action=arena_config period_hours=6                    # admin
m agent/forward action=arena_scheduler on=false                       # admin
```

The openarena bridge, the same way:

```bash
m agent/forward action=openarena                       # up? its pool? entrants?
m agent/forward action=openarena_task slug=fizzbuzz
m agent/forward action=openarena_sources
m agent/forward action=openarena_preview source=humaneval limit=3
m agent/forward action=openarena_import source=mbpp limit=20 offset=20
m agent/forward action=openarena_enter agent=builder                  # owner
m agent/arena/run_match agent=builder task=openarena#fizzbuzz
```
