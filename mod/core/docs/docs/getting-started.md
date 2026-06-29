# Getting Started

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for the frontend and smart contracts)
- **PM2** (`npm install -g pm2`) — for server management
- **Git**

Optional:
- **IPFS / Kubo** — auto-installed by the `ipfs` module if missing
- **MetaMask** — for frontend wallet interaction
- **Hardhat** — for smart contract development (`npm install` in `mod/core/chain/`)

## Installation

```bash
# Clone the repo
git clone https://github.com/your-org/mod.git
cd mod

# Install in editable mode
pip install -e .

# Verify installation
m mods
```

This registers the `m` and `c` CLI commands globally.

## First Commands

```bash
# List all available modules
m mods

# Search for a module
m search agent

# Get the module tree (names → paths)
m tree

# Get info about a module
m info agent

# See a module's functions
m fns agent

# See a function's schema (args, types, docs)
m schema agent
```

## Basic Patterns

### Load and Call a Module

```python
import mod as m

# Load a module (returns the class)
Agent = m.mod('agent')

# Instantiate and call
agent = Agent()
result = agent.forward(query="hello world")

# Or use the shorthand
result = m.fn('agent/forward')(query="hello world")
```

From the CLI:
```bash
m agent/forward query="hello world"
```

### Store Data

```python
# Store a value
m.put('config', {'theme': 'dark', 'lang': 'en'})

# Retrieve it
config = m.get('config')

# Store encrypted
m.put('secret', {'api_key': 'sk-...'}, encrypt=True, password='mypass')

# Retrieve encrypted
secret = m.get('secret', password='mypass')
```

### Manage Keys

```python
# Get or create a key (defaults to ecdsa/Ethereum)
key = m.get_key('main')
print(key.address)       # 0x...
print(key.private_key)   # 0x...

# Sign data
sig = m.sign({'action': 'transfer', 'amount': 100}, key='main')

# Verify
valid = m.verify({'action': 'transfer', 'amount': 100}, signature=sig, address=key.address)
```

### Start a Server

```python
# Serve a module (exposes all functions as POST endpoints)
m.serve('api', port=8000)

# List running servers
m.servers()

# Stop a server
m.kill('api')
```

From the CLI:
```bash
m serve api port=8000
m servers
m kill api
```

### Use IPFS

```python
ipfs = m.mod('ipfs')()

# Store data (returns CID)
cid = ipfs.put({'hello': 'world'})

# Retrieve data
data = ipfs.get(cid)
```

## Project Structure

```
~/.mod/                    # Runtime data directory
├── key/                   # Cryptographic keys
├── store/                 # Persistent storage
├── tree/                  # Module tree cache
├── api/                   # API server data
├── server/                # Server registry
└── {key}.json             # Individual stored values

~/mod/                     # Source code
├── mod/core/              # Framework core
├── mod/orbit/             # Module ecosystem
├── docs/                  # Documentation
└── setup.py               # Package definition
```

## Next Steps

- [CLI Reference](cli.md) — master the command line
- [Modules](modules.md) — understand the module system
- [Orbit Modules](orbit.md) — browse 140+ available modules
- [Smart Contracts](contracts.md) — explore the BlocTime Protocol
