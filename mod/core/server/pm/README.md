# Process Manager (PM) Module

A comprehensive process and container management system for mod, providing multiple backends for different use cases.

## Overview

The PM module offers three different process management backends:

1. **Docker** - Container-based process management
2. **PM2** - Node.js process manager integration
3. **PyPM** - Native Python process manager

## Features

- **Multi-backend Support**: Choose between Docker containers, PM2, or native Python processes
- **Unified Interface**: Consistent API across all backends
- **Process Lifecycle Management**: Start, stop, restart, delete processes/containers
- **Resource Monitoring**: CPU, memory, and uptime tracking
- **Log Management**: Automatic log capture and viewing
- **Configuration Persistence**: Save and restore process configurations
- **Advanced Options**: GPU support, volume mapping, environment variables, and more

## Quick Start

```python
import mod as m

# Using Docker backend
docker = m.mod('pm')()
docker.start('my_container', 'python:3.8', 
             cmd='python -m http.server',
             ports={'8000': 8000})

# Using PyPM backend
from pm.pypm import PyPM
pm = PyPM()
pm.start('myapp', 'app.py', interpreter='python3')

# List running processes/containers
containers = docker.list()
processes = pm.list()
```

## Backends

### 1. Docker Backend

Manage Docker containers with a PM2-like interface.

**Key Features:**
- Container lifecycle management
- GPU configuration support
- Volume and port mapping
- Network configuration
- Resource monitoring

**Usage:**
```python
import mod as m
docker = m.mod('pm')()

# Start container with advanced options
docker.start('gpu_app', 'nvidia/cuda:11.0',
             cmd='python train.py',
             gpus='all',
             volumes={'/data': '/container/data'},
             env={'MODEL': 'resnet50'})

# Monitor resources
stats = docker.monitor()

# Execute commands
result = docker.exec('gpu_app', 'nvidia-smi')
```

See [Docker README](pm/docker/README.md) for detailed documentation.

### 2. PyPM Backend

Lightweight native Python process manager - no Docker required!

**Key Features:**
- Pure Python implementation
- Virtual environment (venv) support
- Process monitoring with psutil
- Automatic log management
- PM2-like CLI and API

**Usage:**
```python
from pm.pypm import PyPM

pm = PyPM()

# Start process with venv
pm.start('myapp', 'app.py', 
         python_env='/path/to/myenv')

# View logs
logs = pm.logs('myapp', lines=50)

# Monitor processes
processes = pm.list()
for proc in processes:
    print(f"{proc['name']}: CPU {proc['cpu']}% MEM {proc['memory']}%")
```

See [PyPM README](pm/pypm/README.md) for detailed documentation.

### 3. PM2 Backend

Integration with the popular Node.js process manager.

**Key Features:**
- Full PM2 feature set
- Clustering support
- Load balancing
- Production-ready process management

See [PM2 README](pm/pm2/README.md) for detailed documentation.

## Common API

All backends share a common interface. At the top level, `serve`/`start` and `kill`/`stop` are interchangeable aliases:

```python
# Start a process/container
backend.start(name, script/image, **options)

# Stop
backend.stop(name)

# Restart
backend.restart(name)

# Delete
backend.delete(name)

# List all
backend.list()

# View logs
backend.logs(name, lines=100)

# Monitor resources
backend.monitor()

# Save configuration
backend.save(config_name)

# Load configuration
backend.load(config_name)
```

## Choosing a Backend

**Use Docker when:**
- You need containerization and isolation
- Working with complex dependencies
- Requiring GPU support
- Deploying microservices

**Use PyPM when:**
- You want a lightweight solution
- No Docker installation available
- Managing Python applications
- Working with virtual environments

**Use PM2 when:**
- Managing Node.js applications
- Need clustering and load balancing
- Require production-grade features

## Installation

```bash
# For Docker backend
# Ensure Docker is installed

# For PyPM backend
pip install psutil

# For PM2 backend
npm install -g pm2
```

## Examples

### Running a Web Application

```python
# With Docker
docker.start('webapp', 'python:3.9',
             cmd='python app.py',
             ports={'5000': 5000},
             volumes={'/app': '/code'})

# With PyPM
pm.start('webapp', 'app.py',
         cwd='/app',
         env={'PORT': '5000'})
```

### Multi-Worker Setup

```python
from pm.pypm import PyPM
pm = PyPM()

for i in range(4):
    pm.start(f'worker-{i}', 'worker.py',
             env={'WORKER_ID': str(i)})
```

### GPU Training Job

```python
docker.start('training', 'pytorch/pytorch:latest',
             cmd='python train.py',
             gpus='all',
             volumes={'/data': '/workspace/data'},
             shm_size='16g')
```

## Directory Structure

```
pm/
├── README.md           # This file
├── pm.py              # Main PM module
├── docker/            # Docker backend
│   └── docker.py
├── pypm/              # PyPM backend
│   ├── README.md
│   ├── pypm.py
│   └── tests/
└── pm2/               # PM2 backend
    ├── pm2.py
    └── test_pm2.py
```

## The mod protocol sandbox

One container that can run **any** mod in the tree, and a hand-off to the build
agent for the ones that aren't ready yet.

The image (`Dockerfile` at the repo root) is a **toolchain**, not a snapshot:
python 3.12, node 20 + pm2, rust, caddy. The module tree is bind-mounted at
`/root/mod`, so `m serve <mod>` inside the container runs the same code you are
editing on the host — nothing to rebuild after an edit.

```bash
m docker/boot                  # build the image if needed, start the container, prove `import mod` works inside
m docker/status                # image built? container up? tree mounted? what's serving?
m docker/enter mod             # a shell inside
m docker/shell "m mods | head" # one command inside
m docker/serve polymarket      # run any mod inside → {'url': 'http://localhost:50950'}
m docker/unserve polymarket
m docker/down mod              # stop the sandbox
```

State (`~/.mod`) is a **named volume, not the host's**. The container gets its
own keys and registry, so a fleet started inside cannot reap the host's servers
— `stop.sh` calls `m server/killall`, which walks that shared registry. Point
the volume at `${HOME}/.mod` in `docker-compose.yml` only if you actually want
the sandbox to share the host's identity.

Ports: the host already runs a gateway on `:3000`, so the sandbox publishes its
gateway on `:3010` and its app on `:3011`. Mods run inside are allocated a port
from the published band `50950-50969`, which is why `m docker/serve` hands back a
`localhost:` URL that works straight from the host.

### Is it ready?

`ready` answers without starting anything, and never raises:

```bash
m docker/ready polymarket
```

```python
{'mod': 'polymarket',
 'ready': True,
 'checks': [{'name': 'docker_daemon', 'ok': True, ...},
            {'name': 'sandbox_image', 'ok': True, ...},
            {'name': 'resolves',      'ok': True, ...},
            {'name': 'config',        'ok': True, ...},
            {'name': 'entrypoint',    'ok': True, ...},
            {'name': 'importable',    'ok': True, ...}],
 'notes':   [...],          # things to know, not reasons to call the mod broken
 'missing': [],
 'fix': None}               # -> 'm docker/modify <mod>' when something failed
```

`checks` gate; `notes` don't. A mod declaring a port outside the published band
is a note, not a failure — `serve` just runs it on a band port instead.

### When it isn't: `modify`

`modify` turns the readiness report into a brief and puts the build agent on it.

```bash
m docker/modify polymarket
m docker/modify polymarket query="also add a healthcheck to the Dockerfile"
m docker/modify polymarket dry_run=1     # show me the brief, submit nothing
```

The brief names the module path, every failing check with its suggested fix, the
operator's extra instruction, and what *done* means (`m docker/ready <mod>` clean
and `m docker/serve <mod>` answering). Dispatch order is `build` (background job
on the Rust job server, watch with `m build/jobs`) → `modify` (in-process agent).
If neither is reachable you get the brief back rather than a silent no-op.

Already ready and no `query`? `modify` says so and submits nothing.

## Contributing

Contributions welcome! Each backend is modular and can be extended independently.

## License

MIT License

## Support

For issues and questions:
- Docker backend: See Docker documentation
- PyPM backend: Pure Python, minimal dependencies
- PM2 backend: See PM2 official docs