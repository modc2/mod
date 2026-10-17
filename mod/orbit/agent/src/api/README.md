# agent api

A thin FastAPI gateway over `mod.forward()`. All real logic lives in `agent/mod.py` — this layer only validates input, dispatches, and shapes responses.

## Layout

| File | Purpose |
|---|---|
| `api.py` | The gateway. ~70 routes across agents, library, credits, tasks, discovery. |
| `crud_api.py` | Standalone FastAPI CRUD demo (in-memory items store). |
| `test_crud_api.py` | Tests for the CRUD demo. |
| `README_CRUD_API.md` | Docs for the CRUD demo. |
| `_serve_api.sh` | Launch script. |
| `simple_crud_api/venv` | Virtualenv for the demo. |
| `__init__.py` | Package marker. |

## Running

```bash
./_serve_api.sh
# or
uvicorn api:app --reload --port 8000
```

Interactive docs are served by FastAPI at `/docs` and `/redoc`.

## Endpoints

### Core
- `GET /health` — health check
- `GET /config` — effective configuration
- `GET /status` — module status
- `POST /forward` — generic dispatch into `mod.forward()`
- `GET /schema` — tool schemas for the LLM
- `GET /providers` — configured model providers
- `GET /params` — parameter introspection

### Keys & identity
- `GET /key`, `POST /key`, `DELETE /key`
- `POST /key/unlock`, `POST /key/lock`
- `GET /owner`, `GET /owners`, `POST /owners`
- `GET /whoami`
- `POST /grant`, `POST /revoke`, `GET /acl`

### Credits & balance
- `GET /balance`
- `GET /credits`, `POST /credits/deposit`, `GET /credits/price`
- `POST /credits/grant`, `GET /credits/treasury`
- `POST /credits/topup`, `POST /credits/topup/verify`
- `POST /credits/withdraw`, `POST /credits/config`

### Tasks
- `GET /tasks`, `GET /tasks/{task_id}`
- `DELETE /tasks`, `DELETE /tasks/{task_id}`
- `GET /tasks/{task_id}/images`

### Agents
- `GET /agents`, `GET /agents/{name}`
- `POST /agents`, `PUT /agents/{name}`, `DELETE /agents/{name}`
- `POST /agents/import` — install a shared agent by localfs CID
- `POST /agents/{name}/register`
- `GET /harnesses` — external agent CLIs (Claude Code, Codex) + availability
- `GET /chains` — chain presets

### Library
- `GET /library` — unified index of prompts, tool docs, memory, agents (`q`/`kind`/`tag` filters)
- `GET /library/formats` — accepted upload shapes
- `POST /library/upload`, `POST /library/upload/file` (multipart)
- `POST /library/import` — install anything from a shared localfs CID

### Prompts & memory
- `GET /prompts`, `POST /prompts`, `POST /prompts/import`, `DELETE /prompts/{prompt_id}`
- `GET /memory`, `POST /memory`, `POST /memory/import`, `DELETE /memory/{note_id}`

### Discovery & tools
- `GET /discover`, `GET /discover/sources`, `GET /discover/item`, `GET /discover/doc`
- `POST /discover/install`, `POST /discover/token`, `POST /discover/cache/clear`
- `GET /tools/installed`, `POST /tools/import`, `DELETE /tools/installed/{tool_id}`

### Modules
- `GET /modules`

## Notes

- Adding a route means adding a `forward()` case in `agent/mod.py`, not business logic here.
- Keep the module docstring at the top of `api.py` in sync with this file when routes change.
- The CRUD demo in `crud_api.py` is independent of the gateway — see `README_CRUD_API.md`.
