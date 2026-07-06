# Skillvill Examples

Demonstrates various use cases for the skillvill skill registry system.

## Examples

### 1. Register Mod Skills (`register_mod_skills.py`)

Register all mod orbit module functions as skills in skillvill.

```bash
python3 mod/orbit/skillvill/examples/register_mod_skills.py
```

This will:
- Discover all mod orbit modules
- Register each function as a skill
- Show module tree and health status

### 2. Custom Storage (`custom_storage.py`)

Use skillvill with a custom storage path for project-specific skill registries.

```bash
python3 mod/orbit/skillvill/examples/custom_storage.py
```

This demonstrates:
- Custom base path configuration
- Project-specific skill organization
- Build and deployment skill workflows

### 3. Import/Export (`import_export.py`)

Transfer skills between different skillvill registries.

```bash
python3 mod/orbit/skillvill/examples/import_export.py
```

This shows:
- Exporting skills to JSON files
- Importing skills from external sources
- Comparing and validating registries

## Quick Start

```python
import mod as m

# Initialize skillvill
sv = m.mod('skillvill')()

# Register a skill
skill_data = {
    'name': 'bash',
    'type': 'tool',
    'description': 'Execute bash commands'
}
sv.register('agent/bash', skill_data)

# List and search
skills = sv.list(module='agent')
results = sv.search('bash')

# Module tree
tree = sv.tree()
```

## Use Cases

1. **Module Discovery** — Maintain registry of all mod orbit functions
2. **Project Skills** — Organize project-specific build/deploy/test workflows
3. **Skill Sharing** — Export/import skills between systems or teams
4. **Documentation** — Auto-generate skill catalogs from registry
5. **Integration** — Connect skills to CI/CD, dashboards, or APIs

## Storage Structure

Default: `~/.mod/skills/`
```
~/.mod/skills/
├── index.json          # Registry index
└── modules/            # Module tree
    ├── agent/
    │   ├── bash.json
    │   └── read.json
    └── uniswap/
        └── pools.json
```

Custom: Any path via `base_path` parameter
```python
sv = m.mod('skillvill')(base_path='/custom/path')
```
