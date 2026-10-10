# registree

A module registry for the mod orbit. Reads each sibling module's `config.json` and returns its metadata.

## Structure

```
registree/
├── registree/
│   └── mod.py    # Mod class — registry logic
├── config.json
└── README.md
```

## Usage

```python
import mod as m

reg = m.mod('registree')()

# List all modules
modules = reg.forward()
# [{"name": "agent", "description": "...", "version": "0.1.0", "port": null}, ...]

# List only modules that expose a service port
services = reg.forward(port_only=True)
# [{"name": "agent", "description": "...", "version": "0.1.0", "port": 50119}, ...]

# Search by keyword (case-insensitive, matches name or description)
results = reg.forward(q="chain")
# [{"name": "chain", ...}, {"name": "webchain", ...}, ...]

# Compose with port_only
results = reg.forward(q="chain", port_only=True)

# Get one module by name
info = reg.forward(name="registree")
# {"name": "registree", "description": "...", "version": "0.1.0", "port": null}
```

```bash
# CLI — list all
m registree forward

# CLI — only modules with a port
m registree forward port_only=true

# CLI — search by keyword
m registree forward q=chain

# CLI — single module
m registree forward name=agent
```

## Response shape

Each entry contains:

| Field | Source |
|-------|--------|
| `name` | `config.json` → `name` |
| `description` | `config.json` → `description` |
| `version` | `config.json` → `version` |
| `schema` | `config.json` → `schema` |
| `port` | `config.json` → `port` (`null` if absent) |
| `urls` | `config.json` → `urls` (`null` if absent) — object with keys like `api`, `app`, `gateway_api`, etc. |
| `icon` | `config.json` → `icon` (`""` if absent) — short display symbol |

Modules whose `config.json` is missing or malformed are silently skipped.

Pass `port_only=True` to `forward()` to restrict the list to entries where `port` is not `null`.

Pass `q=<keyword>` to `forward()` to filter entries by a case-insensitive substring match against `name` or `description`. Composes with `port_only`.

Pass `name=<module>` to `forward()` to look up a single module by name. Returns `None` when no module with that name exists.
