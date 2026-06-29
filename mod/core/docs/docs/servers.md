# Server Management

Any module can be served as an HTTP API. The server system uses PM2 for process management and includes service discovery through a built-in registry.

## Quick Start

```python
import mod as m

# Start a server for any module
m.serve('api', port=8000)

# List running servers
m.servers()  # ['api']

# Stop a server
m.kill('api')
```

From the CLI:
```bash
m serve api port=8000
m servers
m kill api
```

## How It Works

When you serve a module:

1. The framework loads the module and inspects its public methods
2. A Flask app is created with each method as a `POST /{method_name}` endpoint
3. The server is started via PM2 (or Docker)
4. The server registers itself in the service registry
5. Methods are accessible at `POST http://localhost:{port}/{method_name}`

### Endpoint Format

Each public method becomes a POST endpoint that accepts JSON:

```bash
# Module method: bridge.health() → POST /health
curl -X POST http://localhost:8840/health \
  -H "Content-Type: application/json" -d '{}'

# Module method: bridge.in_snapshot(address="5H...") → POST /in_snapshot
curl -X POST http://localhost:8840/in_snapshot \
  -H "Content-Type: application/json" \
  -d '{"address": "5HMfXz..."}'
```

Response format: `{"result": <return_value>}`

### Serve Suffixes: `.app` and `.api`

Use suffixes to control what gets served:

```bash
# API only (default) — wraps module class as Flask endpoints
m serve bridge
m serve bridge.api    # explicit, same as above

# API + Next.js frontend — also starts the app/ Next.js if present
m serve bridge.app
```

| Suffix | API Server | Next.js App | Use case |
|--------|-----------|-------------|----------|
| (none) | Yes | No | Pure API deployments |
| `.api` | Yes | No | Explicit API-only |
| `.app` | Yes | Yes | Full-stack module with frontend |

The API always runs on the module's `port` from config.json. The Next.js app runs on `app_port` (or `port + 1` if not set).

## Server Options

```python
m.serve(
    mod='bridge',        # Module name (with optional .app/.api suffix)
    port=8840,           # Port (auto-read from config.json if not specified)
    remote=False,        # Enable remote access
    pm='pm2',            # Process manager ('pm2' or 'docker')
    fns=None,            # Specific functions to expose (None = all)
    run_mode='flask',    # Run mode
    paywall=None,        # Enable payment gating
)
```

## Port Management

```python
# Get a free port
port = m.free_port()

# Get multiple free ports
ports = m.get_ports(3)  # [8001, 8002, 8003]

# List used ports
m.used_ports()

# Check if port is in use
from mod.core.utils import port_used
port_used(8000)  # True/False
```

## Service Registry

The server system maintains a registry for service discovery.

```python
server = m.mod('server')()

# Get all registered servers
server.namespace()
# {'api': 'http://localhost:8000', 'agent': 'http://localhost:8001'}

# Get server URLs
server.urls()

# Get server names
server.servers()

# Check if server exists
server.exists('api')  # True

# Get module info from running servers
server.mods()  # Fetches /info from each server
```

## Server Lifecycle

```python
server = m.mod('server')()

# Start
server.serve('mymod', port=8080)

# Wait for it to be ready
server.wait_for_server('mymod', max_time=30)

# View logs
server.logs('mymod')

# Stop one server
server.kill('mymod')

# Stop all servers
server.kill_all()
```

## PM2 Integration

Servers are managed through PM2, which provides:
- Process monitoring and auto-restart
- Log management
- Resource monitoring
- Startup scripts

```bash
# View PM2 status directly
pm2 status

# View server logs
pm2 logs api

# Monitor resources
pm2 monit
```
