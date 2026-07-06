# Skillvill

Skill registry and management system. Register, discover, and organize skills in local filesystem with module tree structure.

## Quick Start

```python
import mod as m

# Initialize
sv = m.mod('skillvill')()

# Register a skill
skill_data = {
    'name': 'bash',
    'description': 'Execute bash commands',
    'type': 'tool',
    'parameters': {'command': {'type': 'string', 'required': True}}
}
sv.register('agent/bash', skill_data)

# List skills
skills = sv.list()
agent_skills = sv.list(module='agent')

# Get skill
skill = sv.get('agent/bash')

# Search
results = sv.search('bash')

# Module tree
tree = sv.tree()
```

## Features

- **Local Storage** — Skills in `~/.mod/skills` with JSON index
- **Module Tree** — Organize by module (e.g., `agent/bash`, `uniswap/pools`)
- **Discovery** — List, search, filter by module or pattern
- **Import/Export** — Move skills between systems
- **Validation** — Verify integrity and metadata
- **Health Checks** — Monitor registry status

## Storage Structure

```
~/.mod/skills/
├── index.json          # Registry index
└── modules/            # Module tree
    ├── agent/
    │   ├── bash.json
    │   └── read.json
    ├── uniswap/
    │   └── pools.json
    └── default/
        └── custom.json
```

## CLI

```bash
m skillvill/list
m skillvill/list module=agent
m skillvill/get name=agent/bash
m skillvill/search query=bash
m skillvill/tree
m skillvill/health
```

See `skill.md` for full documentation.
