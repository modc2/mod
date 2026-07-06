# Skillvill

Skill registry and management system. Register, discover, and organize skills in local filesystem with module tree structure.

## Capabilities

- **Local Storage** — Skills stored in `~/.mod/skills` with JSON index
- **Module Tree** — Organize skills by module (e.g., `agent/bash`, `uniswap/pools`)
- **Registration** — Register skills with metadata and path tracking
- **Discovery** — List, search, and filter skills by module or pattern
- **Import/Export** — Move skills between locations or systems
- **Validation** — Verify skill integrity and metadata
- **Health Checks** — Monitor registry status and detect issues

## Usage

### Python

```python
import mod as m

# Initialize skillvill
sv = m.mod('skillvill')()

# Register a skill
skill_data = {
    'name': 'bash',
    'description': 'Execute bash commands',
    'type': 'tool',
    'parameters': {
        'command': {'type': 'string', 'required': True}
    }
}
result = sv.register('agent/bash', skill_data)

# Register with custom path
sv.register(
    'uniswap/pools',
    skill_data,
    module_path='/custom/path/pools.json'
)

# List all skills
skills = sv.list()

# List by module
agent_skills = sv.list(module='agent')

# Search skills
results = sv.search('bash')

# Get specific skill
skill = sv.get('agent/bash')

# Get module tree
tree = sv.tree()
tree_agent = sv.tree(module='agent')

# Import from file
sv.import_skill('/path/to/skill.json', name='custom/skill')

# Export skill
sv.export_skill('agent/bash', '/export/path/bash.json')

# Validate skill
validation = sv.validate('agent/bash')

# Check health
health = sv.health()

# Unregister skill
sv.unregister('agent/bash', delete_file=True)
```

### CLI

```bash
# List all skills
m skillvill/list

# List by module
m skillvill/list module=agent

# Search skills
m skillvill/search query=bash

# Register a skill
m skillvill/register name=agent/bash skill_data='{"name":"bash","type":"tool"}'

# Get specific skill
m skillvill/get name=agent/bash

# Get module tree
m skillvill/tree

# Import skill
m skillvill/import_skill source_path=/path/skill.json name=custom/skill

# Export skill
m skillvill/export_skill name=agent/bash dest_path=/export/bash.json

# Validate skill
m skillvill/validate name=agent/bash

# Check health
m skillvill/health

# Unregister skill
m skillvill/unregister name=agent/bash delete_file=true
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/skills` | List all skills (module, pattern filters) |
| POST | `/skills/register` | Register new skill |
| DELETE | `/skills/{name}` | Unregister skill |
| GET | `/skills/{name}` | Get skill details |
| GET | `/skills/search` | Search skills by query |
| GET | `/skills/tree` | Get module tree structure |
| POST | `/skills/import` | Import skill from file |
| POST | `/skills/{name}/export` | Export skill to file |
| GET | `/skills/{name}/validate` | Validate skill integrity |
| GET | `/health` | Health check |

## Structure

```
skillvill/
├── skillvill/
│   └── mod.py          # Core Mod class with registry logic
├── config.json         # Module metadata
├── skill.md            # This file
└── README.md           # Basic overview

~/.mod/skills/          # Local skill storage
├── index.json          # Skill registry index
└── modules/            # Module tree structure
    ├── agent/          # Agent module skills
    │   ├── bash.json
    │   ├── read.json
    │   └── write.json
    ├── uniswap/        # Uniswap module skills
    │   ├── pools.json
    │   └── swaps.json
    └── default/        # Default module for simple names
        └── custom.json
```

## Skill Data Format

Skills are stored as JSON with metadata:

```json
{
  "name": "bash",
  "description": "Execute bash commands",
  "type": "tool",
  "parameters": {
    "command": {
      "type": "string",
      "required": true,
      "description": "Command to execute"
    }
  },
  "_meta": {
    "name": "agent/bash",
    "module": "agent",
    "skill": "bash",
    "registered_at": "2026-04-18T12:00:00.000Z",
    "path": "/Users/user/.mod/skills/modules/agent/bash.json"
  }
}
```

## Environment

No environment variables required. Storage defaults to `~/.mod/skills`.

Override with custom base path:

```python
sv = m.mod('skillvill')(base_path='/custom/path')
```

## Mod Protocol

- **Module**: `skillvill` (orbit module)
- **Class**: `Mod` in `skillvill/mod.py`
- **Config**: `config.json` with ports 50140 (API), 3140 (app)
- **Storage**: `~/.mod/skills/` (index.json, modules/)
- **Inheritance**: Direct Mod class (no base class)
- **Dependencies**: Python stdlib only (pathlib, json, datetime)

### Integration with Mod Ecosystem

```python
import mod as m

# Register mod skills in skillvill
sv = m.mod('skillvill')()

# Register agent skills
agent_fns = m.fns('agent')
for fn in agent_fns:
    skill_data = {
        'name': fn,
        'module': 'agent',
        'type': 'function',
        'schema': m.schema(f'agent/{fn}')
    }
    sv.register(f'agent/{fn}', skill_data)

# Discover all mod modules and register their functions
for mod_name in m.mods():
    for fn in m.fns(mod_name):
        skill_data = {
            'name': fn,
            'module': mod_name,
            'type': 'mod_function',
            'schema': m.schema(f'{mod_name}/{fn}')
        }
        sv.register(f'{mod_name}/{fn}', skill_data)
```

## Examples

### Register Claude Code Skills

```python
import mod as m

sv = m.mod('skillvill')()

# Register bash skill
bash_skill = {
    'name': 'bash',
    'type': 'tool',
    'description': 'Execute bash commands with timeout',
    'parameters': {
        'command': {'type': 'string', 'required': True},
        'timeout': {'type': 'number', 'default': 120000}
    }
}
sv.register('claude/bash', bash_skill)

# Register read skill
read_skill = {
    'name': 'read',
    'type': 'tool',
    'description': 'Read file contents with line limits',
    'parameters': {
        'file_path': {'type': 'string', 'required': True},
        'limit': {'type': 'number', 'optional': True},
        'offset': {'type': 'number', 'optional': True}
    }
}
sv.register('claude/read', read_skill)
```

### Organize Skills by Category

```python
# Agent skills
sv.register('agent/plan', {...})
sv.register('agent/execute', {...})
sv.register('agent/review', {...})

# Blockchain skills
sv.register('chain/deploy', {...})
sv.register('chain/call', {...})
sv.register('chain/sign', {...})

# Data skills
sv.register('data/fetch', {...})
sv.register('data/transform', {...})
sv.register('data/store', {...})

# View organized tree
tree = sv.tree()
# {
#   'agent': {'skills': [...]},
#   'chain': {'skills': [...]},
#   'data': {'skills': [...]}
# }
```

### Import Skills from Another System

```bash
# Export skills from one system
m skillvill/export_skill name=agent/bash dest_path=/tmp/bash.json

# Import on another system
m skillvill/import_skill source_path=/tmp/bash.json name=agent/bash
```

### Health Monitoring

```python
health = sv.health()
if health['status'] != 'healthy':
    print(f"Found {len(health['invalid_skills'])} invalid skills:")
    for skill in health['invalid_skills']:
        print(f"  - {skill['name']}: {skill['error']}")
```
