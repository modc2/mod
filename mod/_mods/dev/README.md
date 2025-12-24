# Dev Module 🚀

A powerful AI-powered code generation and editing toolkit. Write code with natural language.

## Installation

```bash
pip install mod
```

## Quick Start

```python
import mod as c

# Initialize
dev = c.mod('dev')()

# Generate code from natural language
dev.forward("Create a REST API with FastAPI")

# Edit existing code
dev.forward("Add error handling", to="./app.py")
```

## Features

| Feature | Description |
|---------|-------------|
| 🎯 **Code Generation** | Create code from natural language descriptions |
| ✏️ **Code Editing** | Modify existing files with simple instructions |
| 🔍 **Smart File Selection** | Find relevant files semantically |
| 🧠 **Memory Management** | Maintain context across sessions |
| 🔧 **Function Calling** | Dynamically incorporate function results |

## Core Commands

### Generate New Code
```python
dev.forward("Create a Python class for user authentication")
```

### Edit Existing Files
```python
dev.forward("Add input validation", to="./utils.py")
```

### Find Relevant Files
```python
select = c.mod('tool.select')()
files = select.forward(options=c.files("./"), query="auth related")
```

### Use Memory
```python
memory = c.mod('tool.memory')()
memory.add_short_term("context", {"project": "api"})
dev.set_memory(memory)
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model` | `anthropic/claude-3.7-sonnet` | LLM model |
| `temperature` | `0.7` | Creativity (0-1) |
| `max_tokens` | `4096` | Response limit |
| `verbose` | `True` | Detailed output |

## Available Tools

- `cmd` - Execute shell commands
- `create_file` - Create new files
- `delete_file` - Remove files
- `insert_text` - Insert content at anchors
- `select_files` - Find files by query
- `summarize` - Generate summaries
- `web_scraper` - Search the web

## Examples

### Create a REST API
```python
dev.forward(
    "Create a REST API with user CRUD endpoints",
    to="./api"
)
```

### Add Tests
```python
dev.forward(
    "Write unit tests for this module",
    to="./utils.py"
)
```

### Document Code
```python
dev.forward("Add docstrings to all functions: @/get_text ./helpers.py")
```

## Help & Guides

```python
toolbox = c.mod('tool.toolbox')()
toolbox.help()           # General help
toolbox.quick_start()    # Quick start guide
toolbox.example("dev")   # Examples
```

## License

MIT License
