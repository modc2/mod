# selfimp

Coder/reviewer rounds over the agent protocol. Round 1 the coder does the task, the reviewer assesses the result and gives feedback, the coder revises; repeat until the reviewer says `VERDICT: APPROVED` or the round limit is hit.

Pick the reviewer (and coder) freely: each seat is `{agent, url, model, prompt}` and can be any server speaking the agent protocol (`GET /agents`, `POST /run`). Defaults to claudecode on `127.0.0.1:51260`. A custom `prompt` on the reviewer seat changes what it looks for.

    m selfimp/roster
    m selfimp/run query="add tests for X" path=/path rounds=3 reviewer='{"agent":"claude","model":"opus"}'
    m selfimp/list
    m selfimp/get id=<id>
    m selfimp/serve      # agent-protocol server on :51450 (GET /agents, POST /run)
    m selfimp/test

Runs are stored in `~/.mod/selfimp/runs/` (override with `SELFIMP_DIR`). Not routed by default (`route: false`).
