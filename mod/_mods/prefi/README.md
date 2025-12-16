# Mod Framework

A powerful Python framework for building, deploying, and managing modular applications with built-in support for Docker, cryptography, and AI integration.

## Requirements

- **Python 3.11+**
- **Docker and docker-compose**

### Optional
- VSCode
- Git

## Installation

```bash
git clone <repository-url>
cd ~/mod
pip install -e ./
```

## Quick Start - CLI Commands

The framework provides a CLI tool `m` or `c` for common operations:

```bash
# Server Management
m serve api              # Serve API on port 8000
m kill api              # Stop server
m killall               # Stop all servers
m servers               # List running servers
m namespace             # Show module → URL mapping

# Module Information
m dp api                # Get directory path
m code api              # Get class code
m code api/function     # Get function code
m schema api/function   # Get function schema
m content api           # Get full module content
m info api              # Get complete module info
m mods                  # List all modules

# Module Operations
m addmod <path>         # Add module from path/GitHub
m rmmod <name>          # Remove module
m cpmod from to         # Copy module
m clone <url>           # Clone from GitHub

# Development
m app                   # Deploy application
m test <mod>            # Run tests
m push "message"        # Git commit and push

# AI Integration
m ask "question"        # Ask AI (OpenRouter)
m help mod "question"   # Get help about module
m about mod "query"     # Ask about module
```

## Core Features

### 1. Module Management

```python
import mod as m

# List and discover modules
modules = m.mods()                    # All modules
core_mods = m.core_mods()            # Core modules only
local_mods = m.local_mods()          # Local modules only

# Module info
info = m.info('module_name')         # Complete module info
schema = m.schema('module_name')     # Function signatures
code = m.code('module_name')         # Source code
content = m.content('module_name')   # All files (file2content) where the files are relative to the module dirpath
cid = m.cid('module_name')          # Content hash

# Check existence
exists = m.mod_exists('module_name')
is_file = m.is_mod_file('module_name')
```

### 2. Function Execution

```python
# Get and call functions
fn = m.fn('module/function')
result = fn(param='value')

# Alternative syntax
result = m.fn('module/').forward()   # Calls module.forward()
result = m.fn('/function')           # Calls mod.function()

# Check if function exists
if m.isfn('module/function'):
    result = m.fn('module/function')()

# Get function schema
schema = m.fnschema('module/function')
# Returns: {'input': {...}, 'output': {...}, 'docs': '...', ...}
```

### 3. Server Management

```python
# Serve modules
m.serve('api', port=8000)
m.serve('model.openrouter', remote=True)

# Server info
servers = m.servers()                 # List active servers
namespace = m.namespace()             # Module → URL mapping
exists = m.server_exists('api')

# Control servers
m.kill('api')                        # Stop specific server
m.kill_all()                         # Stop all servers
```

### 4. File Operations

```python
# Read/write files
content = m.text('/path/to/file')
m.put_text('/path/to/file', 'content')

# JSON operations
m.put_json('config', {'key': 'value'})
data = m.get_json('config', default={})

# File listing
files = m.files('./path', search='*.py', depth=4)
dirs = m.ls('./path')
all_files = m.glob('./path/**/*.py')

# Path operations
abs_path = m.abspath('~/relative/path')
rel_path = m.relpath('/absolute/path')
dirpath = m.dirpath('module_name')
```

### 5. Cryptography & Keys

```python
# Key management
key = m.get_key('my_key')
address = key.address
keys = m.keys()

# Sign and verify
signature = m.sign({'data': 'value'}, key='my_key')
is_valid = m.verify(
    data={'data': 'value'}, 
    signature=signature, 
    address=address
)

# Encrypt/decrypt
encrypted = m.encrypt('secret', key='my_key', password='pwd')
decrypted = m.decrypt(encrypted, key='my_key', password='pwd')

# Generate mnemonic
mnemonic = m.mnemonic(words=24)
```

### 6. Storage & Caching

```python
# Store with optional encryption
m.put('key', {'data': 'value'}, encrypt=True, password='pwd')

# Retrieve with max age (seconds)
data = m.get('key', default={}, max_age=3600)

# Storage paths
storage_dir = m.storage_dir('module_name')  # ~/.mod/module_name
path = m.get_path('my_data')               # Auto-resolve to storage
```

### 7. AI Integration

```python
# Ask questions
answer = m.ask("How does this work?", stream=True)
answer = m.ask("Explain", mod='api', context=True)

# Module-specific help
help_text = m.help('module', 'what does this do?')
about = m.about('module', 'explain this feature')

# Code analysis
how = m.how('module', 'how does function X work?')
```

### 8. Git Operations

```python
# Push changes
m.push("commit message", mod='module_name')
m.push("fix bug", "and update docs", safety=True)

# Repository info
is_repo = m.isrepo('module_name')
git_info = m.git_info(path='./repo')
repos = m.repos(search='commune')

# Clone repositories
m.clone('https://github.com/user/repo')
m.clone('user/repo')  # Auto-adds github.com
```

## Module Structure

Modules follow an "anchor file" pattern:

```
mods/
├── my_module/
│   ├── mod.py          # Anchor file (main class)
│   ├── config.json     # Configuration
│   ├── README.md       # Documentation
│   └── utils.py        # Helpers
```

**Anchor files** can be named: `mod.py`, `agent.py`, `block.py`, or match the module name.

## Configuration

### config.json
```json
{
  "name": "mod",
  "port_range": [8000, 9000],
  "expose": ["ask", "serve", "info"],
  "shortcuts": {
    "m": "mod",
    "api": "api.server"
  },
  "links": {
    "ipfs": "https://github.com/user/ipfs-service.git"
  }
}
```

## Advanced Features

### Module Linking

```python
# Link external modules
m.link('ipfs-service')          # From config.links
m.unlink('ipfs-service')
is_linked = m.islink('ipfs-service')
```

### Async Execution

```python
# Submit async tasks
future = m.submit('module/function', params={'key': 'val'})
result = future.result()

# Custom executor
executor = m.executor(mode='thread', max_workers=10)
# modes: 'thread', 'process', 'async'
```

### Testing

```python
# Run tests
results = m.test('module_name')
m.test()  # Test all modules
```

### Context & Documentation

```python
# Get README context
context = m.context(path='./modules')
readmes = m.readmes(path='./modules')
size = m.context_size()
```

### Utilities

```python
# Hash objects
hash_val = m.hash({'data': 'value'}, mode='sha256')

# Time operations
timestamp = m.time()
m.sleep(2)

# Environment variables
all_env = m.env()
api_key = m.env('API_KEY')

# Port management
ports = m.get_ports(n=3)
port_range = m.get_port_range()
```

## Python API Examples

### Basic Module Usage
```python
import mod as m

# Load and use a module
api = m.mod('api')()
result = api.some_function()

# Or directly call function
result = m.fn('api/some_function')(param='value')
```

### Server Deployment
```python
# Serve with auto port assignment
m.serve('api')

# Serve on specific port
m.serve('model.openrouter', port=8080, remote=True)

# Check and manage
if m.server_exists('api'):
    m.kill('api')
```

### Data Operations
```python
# Store encrypted data
m.put('secrets', {'api_key': 'xxx'}, encrypt=True)

# Retrieve with expiration
data = m.get('cache', max_age=3600, default={})

# File operations
files = m.files('./', search='.py', depth=3)
for file in files:
    content = m.text(file)
```

### Module Development
```python
# Create from template
m.fork(base='base_module', name='my_module')

# Copy module
m.cpmod('source_mod', 'dest_mod')

# Add from path
m.addpath('/path/to/module', name='my_mod')

# Remove
m.rmmod('old_module')
```

## Best Practices

1. **Module Naming**: Use dot notation (e.g., `model.openrouter`)
2. **Anchor Files**: Name main file `mod.py` or match module name
3. **Configuration**: Always include `config.json` with module metadata
4. **Documentation**: Add README.md to each module
5. **Security**: Use encryption for sensitive data
6. **Testing**: Write tests for critical functionality

## CLI Workflow Example

```bash
# 1. Create new module
m clone https://github.com/user/template
m cpmod template my_module

# 2. Develop
m code my_module          # View code
m serve my_module         # Test server
m test my_module          # Run tests

# 3. Deploy
m app my_module           # Deploy app
m servers                 # Check status

# 4. Update
m push "Added feature X" my_module
```

## Docker Integration

```bash
# Build and deploy
m up                      # Start containers
m enter mod               # Enter container
m logs api               # View logs
m build my_module        # Build module
```

## Security Features

- **Encryption**: AES encryption for sensitive data
- **Key Management**: Secure key generation and storage  
- **Signatures**: Cryptographic signing and verification
- **Access Control**: Module-level permissions
- **Password Protection**: Optional password-based encryption

## License

COPYLEFT

## Support

For issues and questions, please refer to the documentation or open an issue in the repository.