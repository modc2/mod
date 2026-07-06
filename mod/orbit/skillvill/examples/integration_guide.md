# Skillvill Integration Guide

How to integrate skillvill with the mod ecosystem for skill discovery, registration, and management.

## Use Case 1: Auto-Register All Mod Functions

Create a comprehensive registry of all mod orbit module functions:

```python
import mod as m

sv = m.mod('skillvill')()

# Get all modules
modules = m.mods()

for mod_name in modules:
    # Get functions for each module
    fns = m.fns(mod_name)

    for fn in fns:
        skill_name = f"{mod_name}/{fn}"

        # Get schema if available
        try:
            schema = m.schema(f"{mod_name}/{fn}")
        except:
            schema = None

        # Register skill
        sv.register(skill_name, {
            'name': fn,
            'module': mod_name,
            'type': 'mod_function',
            'schema': schema,
            'description': f'{mod_name} module function'
        }, overwrite=True)

print(f"Registered {len(sv.list())} mod functions")
```

## Use Case 2: Project-Specific Build System

Organize build/deploy/test workflows:

```python
import mod as m

# Use project-specific registry
sv = m.mod('skillvill')(base_path='./.skillvill')

# Register build skills
build_skills = {
    'build/compile': {
        'name': 'compile',
        'type': 'build',
        'command': 'tsc',
        'watch': False
    },
    'build/test': {
        'name': 'test',
        'type': 'test',
        'command': 'jest',
        'coverage': True
    },
    'deploy/staging': {
        'name': 'staging',
        'type': 'deploy',
        'environment': 'staging',
        'pre_steps': ['build/compile', 'build/test']
    }
}

for name, data in build_skills.items():
    sv.register(name, data)

# Discover build pipeline
build_tree = sv.tree(module='build')
deploy_tree = sv.tree(module='deploy')
```

## Use Case 3: Claude Code Skills Registry

Register Claude Code tool capabilities:

```python
import mod as m

sv = m.mod('skillvill')()

# Claude Code tools
claude_tools = {
    'claude/bash': {
        'name': 'Bash',
        'type': 'tool',
        'description': 'Execute bash commands',
        'parameters': {
            'command': {'type': 'string', 'required': True},
            'timeout': {'type': 'number', 'default': 120000}
        }
    },
    'claude/read': {
        'name': 'Read',
        'type': 'tool',
        'description': 'Read file contents',
        'parameters': {
            'file_path': {'type': 'string', 'required': True},
            'limit': {'type': 'number', 'optional': True}
        }
    },
    'claude/write': {
        'name': 'Write',
        'type': 'tool',
        'description': 'Write file contents',
        'parameters': {
            'file_path': {'type': 'string', 'required': True},
            'content': {'type': 'string', 'required': True}
        }
    },
    'claude/edit': {
        'name': 'Edit',
        'type': 'tool',
        'description': 'Edit file with exact string replacement',
        'parameters': {
            'file_path': {'type': 'string', 'required': True},
            'old_string': {'type': 'string', 'required': True},
            'new_string': {'type': 'string', 'required': True}
        }
    }
}

for name, data in claude_tools.items():
    sv.register(name, data)

# Search for file tools
file_tools = sv.search('file')
```

## Use Case 4: API Endpoint Registry

Document and organize API endpoints:

```python
import mod as m

sv = m.mod('skillvill')()

# Register API endpoints
endpoints = {
    'api/users/list': {
        'method': 'GET',
        'path': '/api/users',
        'description': 'List all users',
        'auth': True
    },
    'api/users/create': {
        'method': 'POST',
        'path': '/api/users',
        'description': 'Create new user',
        'auth': True
    },
    'api/posts/list': {
        'method': 'GET',
        'path': '/api/posts',
        'description': 'List all posts',
        'auth': False
    }
}

for name, data in endpoints.items():
    sv.register(name, data)

# Generate API documentation
tree = sv.tree()
for module, module_data in tree.items():
    print(f"\n## {module.upper()}")
    for skill in module_data['skills']:
        skill_data = sv.get(skill['name'])
        print(f"- {skill_data['method']} {skill_data['path']}")
        print(f"  {skill_data['description']}")
```

## Use Case 5: Skill Discovery Dashboard

Build a skill discovery interface:

```python
import mod as m

sv = m.mod('skillvill')()

def skill_dashboard():
    """Interactive skill dashboard."""
    tree = sv.tree()

    print("=== SKILL REGISTRY DASHBOARD ===\n")

    # Module summary
    print(f"Modules: {len(tree)}")
    total_skills = sum(len(m['skills']) for m in tree.values())
    print(f"Total Skills: {total_skills}\n")

    # Module details
    for module, data in sorted(tree.items()):
        print(f"\n📦 {module} ({len(data['skills'])} skills)")
        print(f"   Path: {data['path']}")

        for skill in data['skills']:
            skill_data = sv.get(skill['name'])
            skill_type = skill_data.get('type', 'unknown')
            print(f"   - {skill['skill']} ({skill_type})")

    # Health status
    health = sv.health()
    print(f"\n{'✓' if health['status'] == 'healthy' else '✗'} Health: {health['status']}")
    print(f"  Valid: {health['valid_skills']}/{health['total_skills']}")

skill_dashboard()
```

## Use Case 6: Skill Export/Import Workflow

Share skills across teams or systems:

```bash
# Export all skills
python3 -c "
import mod as m
import os

sv = m.mod('skillvill')()
export_dir = './skill_exports'
os.makedirs(export_dir, exist_ok=True)

for skill in sv.list():
    name = skill['name']
    filename = name.replace('/', '_') + '.json'
    sv.export_skill(name, f'{export_dir}/{filename}')
    print(f'Exported: {name}')
"

# Import on another system
python3 -c "
import mod as m
import os
import glob

sv = m.mod('skillvill')()
export_dir = './skill_exports'

for filepath in glob.glob(f'{export_dir}/*.json'):
    # Extract skill name from filename
    filename = os.path.basename(filepath)
    name = filename.replace('_', '/').replace('.json', '')

    sv.import_skill(filepath, name=name)
    print(f'Imported: {name}')
"
```

## Use Case 7: Integration with Agent Module

Provide skills to autonomous agents:

```python
import mod as m

sv = m.mod('skillvill')()
agent = m.mod('agent')()

# Register agent capabilities
agent_skills = {
    'agent/plan': {
        'name': 'plan',
        'type': 'capability',
        'description': 'Create implementation plans'
    },
    'agent/execute': {
        'name': 'execute',
        'type': 'capability',
        'description': 'Execute planned tasks'
    },
    'agent/review': {
        'name': 'review',
        'type': 'capability',
        'description': 'Review code changes'
    }
}

for name, data in agent_skills.items():
    sv.register(name, data)

# Agent can discover available skills
available_skills = sv.list(module='agent')
```

## Best Practices

1. **Consistent Naming**: Use `module/skill` format (e.g., `agent/bash`, `uniswap/pools`)
2. **Metadata**: Include type, description, parameters in skill data
3. **Validation**: Regularly run `sv.health()` to check registry integrity
4. **Organization**: Group related skills under common modules
5. **Documentation**: Use descriptive names and complete metadata
6. **Versioning**: Consider adding version field to skill metadata
7. **Custom Paths**: Use project-specific registries for isolation

## CLI Integration

All operations available via CLI:

```bash
# List skills
m skillvill/list

# Filter by module
m skillvill/list module=agent

# Search
m skillvill/search query=bash

# Get skill
m skillvill/get name=agent/bash

# Tree view
m skillvill/tree

# Health check
m skillvill/health

# Register (from Python recommended for complex data)
python3 -c "import mod as m; m.mod('skillvill')().register('test/skill', {'name': 'skill'})"
```

## Storage Locations

- **Default**: `~/.mod/skills/` - Global skill registry
- **Project**: `./.skillvill/` - Project-specific skills
- **Custom**: Any path via `base_path` parameter

```python
# Global registry
sv_global = m.mod('skillvill')()

# Project registry
sv_project = m.mod('skillvill')(base_path='./.skillvill')

# Custom location
sv_custom = m.mod('skillvill')(base_path='/custom/path/skills')
```
