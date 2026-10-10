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
# [{"name": "agent", "description": "...", "version": "0.1.0"}, ...]

# Get one module by name
info = reg.forward(name="registree")
# {"name": "registree", "description": "...", "version": "0.1.0"}
```

```bash
# CLI — list all
m registree forward

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

Modules whose `config.json` is missing or malformed are silently skipped.
