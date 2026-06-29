# Module System

The module system is the backbone of mod. Everything is a module — from AI agents to IPFS storage to blockchain bridges. Modules live in orbits and are loaded dynamically by name.

## Loading Modules

### Python API

```python
import mod as m

# Load a module class
Agent = m.mod('agent')

# Instantiate
agent = Agent()

# Call a function
result = agent.forward(query="hello")

# One-liner
result = m.mod('agent')().forward(query="hello")
```

### Function Shorthand

```python
# Load a specific function directly
forward = m.fn('agent/forward')
result = forward(query="hello")

# Nested modules
result = m.fn('agent.memory/get')(key='context')
```

### CLI

```bash
m agent/forward query="hello"
m agent.memory/get key=context
```

## Module Structure

Every module has an **anchor file** — the entry point the framework loads. The anchor file must contain a class (usually named after the module or `Mod`).

### Anchor File Priority

The framework searches for anchor files in this order:

1. `mod.py`
2. `agent.py`
3. `block.py`
4. `{module_name}.py`

### Minimal Module

```
mod/orbit/mymod/
└── mymod/
    └── mod.py
```

```python
# mod/orbit/mymod/mymod/mod.py

class Mod:
    description = "My awesome module"

    def forward(self, x=1, y=2):
        """Default function called when no function is specified."""
        return x + y

    def greet(self, name="world"):
        """Say hello."""
        return f"hello {name}"
```

Usage:
```bash
m mymod                    # calls forward() → 3
m mymod/forward x=5 y=10   # → 15
m mymod/greet name=mod      # → "hello mod"
```

## Orbits

Modules are organized into orbits (namespaces). The framework searches all orbits when resolving a module name.

| Orbit | Location | Purpose |
|-------|----------|---------|
| `core` | `mod/core/` | Framework internals (api, server, store, key, chain, app) |
| `inner` | `mod/orbit/` | Primary module ecosystem (140+ modules) |
| `outer` | Configurable | Community / external modules |
| `local` | Current directory | Project-local modules |

### Search Order

When you call `m.mod('name')`, the framework searches orbits in order until it finds a match. Core modules take priority, then inner, then outer, then local.

## Module Discovery

### List Modules

```python
# All modules
m.mods()  # ['agent', 'api', 'bridge', 'cache', ...]

# Search
m.mods(search='ip')  # ['ipfs', 'ip']

# Filter
m.mods(startswith='agent')  # ['agent', 'agent.memory']

# Core only
m.core_mods()  # ['api', 'app', 'chain', 'key', 'server', 'store']

# Local only
m.local_mods()
```

### Module Tree

```python
# Get name → path mapping
tree = m.tree()
# {'agent': '/Users/you/mod/mod/orbit/agent/agent',
#  'api': '/Users/you/mod/mod/core/api/api',
#  ...}

# Filter by orbit
tree = m.tree(orbit='core')

# Search
tree = m.tree(search='agent')
```

### Search (Fuzzy)

```python
results = m.search('agnt')  # fuzzy matches 'agent'
```

## Introspection

### Function List

```python
m.fns('agent')
# ['forward', 'get_plan', 'run_plan', 'init_memory', ...]
```

### Schema

```python
schema = m.schema('agent')
# {
#   'forward': {
#     'input': {'query': 'str', 'steps': 'int', ...},
#     'output': 'dict',
#     'docs': 'Main agent loop...'
#   },
#   ...
# }
```

### Source Code

```python
code = m.code('agent')           # Full module source
code = m.code('agent/forward')   # Single function source
```

### Module Info

```python
info = m.info('agent')
# {
#   'name': 'agent',
#   'path': '/Users/you/mod/mod/orbit/agent/agent',
#   'schema': {...},
#   'fns': ['forward', ...],
#   'description': '...'
# }
```

### File Contents

```python
# Get all files in a module as {path: content}
contents = m.content('agent')
```

## Module Caching

Loaded modules are cached in memory for performance:

- `_mod_cache` — module class cache
- `fnscache` / `modscache` — function cache
- `tree_cache` — module tree cache (persists to `~/.mod/tree/`)

Use `update=True` to force reload:
```python
m.mod('agent', update=True)
m.tree(update=True)
```

## Creating a New Module

1. Create the directory structure:
```bash
mkdir -p mod/orbit/mymod/mymod
```

2. Create the anchor file:
```python
# mod/orbit/mymod/mymod/mod.py

class Mod:
    description = "What this module does"

    def __init__(self, config=None):
        """Optional initialization."""
        self.config = config or {}

    def forward(self, **kwargs):
        """Default entry point."""
        return {"status": "ok"}

    def custom_function(self, x, y=0):
        """Additional functions are automatically exposed."""
        return x + y
```

3. Test it:
```bash
m mymod                          # calls forward()
m mymod/custom_function x=5 y=3  # → 8
m info mymod                     # verify it shows up
```

The module is automatically discovered — no registration needed.

## Serving Modules

Any module can be served as an HTTP API. The core server auto-wraps public methods as `POST /{method_name}` endpoints.

```bash
# API only (default) — wraps module class as Flask endpoints
m serve mymod
m serve mymod.api    # explicit, same as above

# API + Next.js frontend (if app/ exists in the module)
m serve mymod.app
```

| Suffix | API Server | Next.js App | Use case |
|--------|-----------|-------------|----------|
| (none) | Yes | No | Pure API deployments |
| `.api` | Yes | No | Explicit API-only |
| `.app` | Yes | Yes | Full-stack module with frontend |

Each public method becomes a POST endpoint that accepts JSON and returns `{"result": <value>}`:

```bash
curl -X POST http://localhost:8840/health \
  -H "Content-Type: application/json" -d '{}'

curl -X POST http://localhost:8840/in_snapshot \
  -H "Content-Type: application/json" \
  -d '{"address": "5HMfXz..."}'
```

Ports are read from the module's `config.json`. The API runs on `port`, the Next.js app on `app_port`.
