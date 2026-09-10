# hermes ☿

NousResearch **Hermes** weights as a local agent — and as a first-class citizen
of the fleet's two agent protocols.

Inference runs on **this box only**: the `llama_cpp` package in this process, or
a `llama-server` / `ollama` on localhost. There is deliberately no hosted
fallback. A module that says local-only and quietly answers from somebody's
cloud key is a lie with a latency profile.

```
API   http://127.0.0.1:50920          pm2: hermes-api
CLI   m hermes info | health | agents | run query=… | chat message=…
```

## The two integrations

**orbit/build** — mount it as an agent backend, then jobs dispatch here:

```bash
curl -X POST http://127.0.0.1:8890/agents/mods \
  -H "Authorization: Bearer <owner>" -d '{"mod":"orbit/hermes"}'
```

Build probes `GET /agents` for `{"agents": [...]}` (activator knock first) and
then POSTs `/run/stream`. Both are implemented here in the vocabulary build's
renderer already knows — `model_start`, `token`, `tool_start`, `step`, `done` —
so a hermes job draws like every other job. Mounted agents show in
ACCOUNT ▸ AGENT with their mod ref and CID.

**orbit/agent** — provider `hermes`, beside the three LFM ones:

```bash
curl -X POST http://127.0.0.1:50117/run \
  -d '{"query":"…","provider":"hermes","agent":"hermes"}'
```

Keyless, free, never billed, never gated on a credit balance — it is this box's
own CPU. The `hermes` persona there pins `provider = "hermes"`, which agent
configs gained for this: a model id alone is ambiguous across providers, and
`hermes-3-8b` handed to OpenRouter is a leftover setting that gets swapped out.

## Routes

| | |
|---|---|
| `GET /agents` | the roster — **the mount probe**, open by design |
| `GET /agents/{id}` | one persona in full, prompt included |
| `POST /run/stream` | SSE run |
| `POST /run` | the same run, blocking |
| `POST /chat` | no tools, no loop — just the model |
| `GET /models` | the Hermes registry + whatever the backend already serves |
| `GET /health` | is a backend reachable, and if not, `why` |

Reads are open — an authenticated probe is a module nobody can mount. Running
is not: a run holds this machine for minutes and its tools write files and
execute shell. It takes the owner bearer (`~/.mod/hermes/server.secret`), a
loopback request that did not pass the gateway, or a mod-protocol token as
`key` in the body — the credential build's dispatcher already sends.

## Agents and tools

`hermes` (general), `hermes-coder` (writes code), `hermes-explain` (reads only —
sandbox is a property of that agent, not just of its caller).

Eight tools: `bash read write edit ls grep think finish`. Eight, not thirty: a
3B model reading a wall of schemas spends its context on the menu. A sandboxed
run gets the five that cannot write.

## Getting a backend

Nothing here generates until one of these exists:

```bash
pip install llama-cpp-python && m hermes download model=hermes-3-8b
llama-server -m ~/.mod/hermes/models/*.gguf     # then it is found on :8080
ollama serve && ollama pull hermes3             # found on :11434
```

`GET /health` names whichever one is missing. `HERMES_API_URL` points at a
server on another port and is taken on trust — a server still loading 5 GB
refuses for a minute and then works, so it is not probed away.

## Tests

```bash
python3 -m pytest tests/test_hermes.py -q      # 12 tests, no weights needed
```

`tests/fake_backend.py` is a scripted stand-in for llama-server/ollama, which is
what makes the contract testable on a box with no models on it — most boxes.
