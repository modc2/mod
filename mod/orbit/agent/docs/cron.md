# cron — run an agent every N minutes

Two timers live in this console:

| timer | what it does | who changes it |
|---|---|---|
| **builder** (`/grow`, see [grow.md](grow.md)) | builds a new tool + agent every N minutes | owner only |
| **scheduled runs** (`/cron`) | runs *an agent you pick* with *a prompt you write* every N minutes | owner, and addresses the owner granted `cron` |

Both are in the console: the AGENTS pane shows the builder timer, every job
you may see and this host's compute. Each agent's editor has a
**schedule · compute** section for that one agent.

## Who can schedule

- **Owner**: can schedule any agent and sees and manages everyone's jobs.
- **Granted address**: the owner runs `POST /grant {"address": "0x…", "actions": ["cron"]}`
  (`"*"` also counts; the default `run`/`tool_run` grant does **not**).
  A granted address sees and manages its own jobs only. Its runs are
  sandboxed to its portal dir and billed to its credits, exactly like `/run`.
  A job with `free: true` uses zero-cost models.
- **Anyone else** sees job counts and the compute card. Prompts are not public.

Access is re-checked on **every run**. If the owner revokes a grant, that
address's jobs pause themselves on their next run and the pause reason is shown.

## Guards

- `every` is clamped to 1 minute … 1 week.
- `daily_cap`: runs per job per UTC day (default 96; 0 = no cap). A manual **run** ignores it.
- `max_fails`: a job pauses itself after this many failures in a row (default 3). Resume clears the count.
- At most 20 jobs per granted address (200 for the owner), and at most 2 runs in flight across all jobs.
- A job that is still running is never started again.
- A job whose agent was deleted pauses itself.

Every run is recorded on its job (status, answer, time taken, cost, and the
host and model it ran on). It also shows up in the console's **TASKS** list as
`⏱ <prompt>`.

## Compute

`GET /compute?agent=<name>` (MCP `agent_cron op=compute`) answers two questions:

- **host**: where the agent loop and its tools run. That is this box: hostname,
  CPU, cores, load, memory, disk, GPUs (via `nvidia-smi` if present), OS,
  container or not. It is read from `/proc`; no cloud API is called.
- **inference**: where the model runs. One of: `local` (liquidai/hermes weights on
  this host), `tab` (the visitor's browser), `fleet` (a `mod:<name>` worker),
  `cli` (a harness such as claude/codex on this host) or `remote` (a hosted API
  such as OpenRouter/Venice).

## API

```bash
curl -s localhost:50117/cron?key=$TOKEN                       # your jobs + scheduler + compute
curl -s localhost:50117/compute?agent=agi                     # where agi runs
curl -s -XPOST localhost:50117/cron/add -H 'content-type: application/json' \
     -d '{"key":"'$TOKEN'","agent":"py-todo-hunter","prompt":"scan src/ for new TODOs","every":60}'
curl -s -XPOST localhost:50117/cron/update -d '{"key":"…","id":"<id>","enabled":false}' -H 'content-type: application/json'
curl -s -XPOST localhost:50117/cron/run    -d '{"key":"…","id":"<id>"}' -H 'content-type: application/json'
curl -s -XPOST localhost:50117/cron/rm     -d '{"key":"…","id":"<id>"}' -H 'content-type: application/json'
```

MCP: `agent_cron` with `op=status|compute|add|update|job|rm|run`.

## Files

- `src/cron/mod.py`: `Cron` (jobs, guards, one run), `Scheduler` (a 15 s due-check thread), and `ModHost` (the adapter onto Mod; it runs as a `Standing(address)` built server-side, never parsed off the wire).
- `src/cron/compute.py`: the host/inference probe (stdlib only).
- `src/app/app/components/SchedulePanel.tsx`: builder timer, jobs and the compute card.
- State lives outside the repo, in `~/.mod/agent/cron/jobs.json`. `CRON_SCHEDULER=0` keeps the thread down, and it also stays down under pytest.
- Tests: `tests/test_cron.py`.
