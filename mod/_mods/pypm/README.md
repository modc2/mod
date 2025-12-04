# PyPM - Native Python Process Manager

A lightweight process manager similar to PM2, built with pure Python. No Docker required!

## Features

- **Pure Python**: No Docker dependencies, uses only native Python and psutil
- **Process Management**: Start, stop, restart, and delete processes
- **Process Monitoring**: CPU, memory, and uptime tracking
- **Log Management**: Automatic log capture and viewing
- **Persistence**: Save and resurrect process configurations
- **PM2-like Interface**: Familiar commands for PM2 users

## Installation

```bash
pip install psutil
```

## Usage

### As a Python Module

```python
from pypm import PyPM

# Initialize
pm = PyPM()

# Start a process
pm.start('myapp', 'app.py', interpreter='python3')

# List all processes
processes = pm.list()
for proc in processes:
    print(f"{proc['name']}: {proc['status']}")

# View logs
logs = pm.logs('myapp', lines=50)
print(logs)

# Stop a process
pm.stop('myapp')

# Restart a process
pm.restart('myapp')

# Delete a process
pm.delete('myapp')

# Get detailed info
info = pm.describe('myapp')
print(info)
```

### Command Line Interface

```bash
# Start a process
python pypm.py start --name myapp --script app.py --interpreter python3

# List all processes
python pypm.py list

# View logs
python pypm.py logs --name myapp --lines 100

# Follow logs (like tail -f)
python pypm.py logs --name myapp --follow

# Stop a process
python pypm.py stop --name myapp

# Restart a process
python pypm.py restart --name myapp

# Delete a process
python pypm.py delete --name myapp

# Get process details
python pypm.py describe --name myapp

# Stop all processes
python pypm.py kill-all

# Save current state
python pypm.py save

# Resurrect saved processes
python pypm.py resurrect
```

## API Reference

### PyPM Class

#### `__init__(storage_path="~/.pypm")`
Initialize PyPM with optional storage path.

#### `start(name, script, cwd=None, env=None, interpreter="python3", args=None)`
Start a new process.
- **name**: Process identifier
- **script**: Script or command to run
- **cwd**: Working directory (optional)
- **env**: Environment variables dict (optional)
- **interpreter**: Interpreter to use (python3, node, bash, etc.)
- **args**: Additional arguments list (optional)

#### `stop(name)`
Stop a running process gracefully.

#### `restart(name)`
Restart a process (stop + start).

#### `delete(name)`
Remove a process from registry.

#### `list()`
Get list of all processes with status and stats.

#### `logs(name, lines=100, follow=False, stderr=False)`
Get process logs.
- **lines**: Number of lines to retrieve
- **follow**: Follow logs in real-time
- **stderr**: Show stderr instead of stdout

#### `describe(name)`
Get detailed information about a process.

#### `kill_all()`
Stop all running processes.

#### `save()`
Save current process registry.

#### `resurrect()`
Restart all previously running processes.

## Process Storage

PyPM stores process information in `~/.pypm/`:
- `processes.json`: Process registry
- `logs/`: Process logs (stdout and stderr)
- `pids/`: Process ID files

## Comparison with PM2

| Feature | PM2 | PyPM |
|---------|-----|------|
| Language | Node.js | Python |
| Dependencies | Node.js required | Python + psutil |
| Process Management | ✓ | ✓ |
| Log Management | ✓ | ✓ |
| Monitoring | ✓ | ✓ |
| Clustering | ✓ | ✗ |
| Load Balancing | ✓ | ✗ |
| Docker Support | ✓ | N/A |
| Native Python | ✗ | ✓ |

## Examples

### Running a Python Web Server

```python
pm = PyPM()
pm.start(
    name='webserver',
    script='python -m http.server 8000',
    interpreter='bash',
    cwd='/var/www'
)
```

### Running Multiple Workers

```python
pm = PyPM()

for i in range(4):
    pm.start(
        name=f'worker-{i}',
        script='worker.py',
        env={'WORKER_ID': str(i)}
    )
```

### Auto-restart on Boot

```python
# Save current processes
pm.save()

# Later, after reboot
pm.resurrect()
```

## License

MIT License

## Contributing

Contributions welcome! This is a lightweight alternative to PM2 for Python projects.
