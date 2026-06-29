# CLI Reference

Mod provides two CLI commands: `m` (primary) and `c` (alias). Both work identically.

## Basic Syntax

```bash
m <module>/<function> [args...] [key=value...] [--init_key=value...]
```

### Examples

```bash
# Call a module's default function (forward)
m agent

# Call a specific function
m agent/forward query="build something"

# Positional arguments
m hash "hello world" mode=md5

# Multiple kwargs
m serve api port=8000 remote=True

# Init params (passed to module constructor)
m api/mods --key=mykey search=agent
```

## Argument Parsing

### Positional Arguments
Arguments without `=` are treated as positional:
```bash
m agent/forward "my query" 10
# → agent.forward("my query", 10)
```

### Keyword Arguments
Arguments with `=` are treated as kwargs:
```bash
m agent/forward query="my query" steps=10
# → agent.forward(query="my query", steps=10)
```

### Init Parameters
Arguments prefixed with `--` are passed to the module constructor:
```bash
m api/mods --key=mykey --store=ipfs search=agent
# → Api(key='mykey', store='ipfs').mods(search='agent')
```

### Type Coercion
The CLI auto-converts argument types:

| Input | Python Type | Example |
|-------|------------|---------|
| `123` | `int` | `port=8000` |
| `3.14` | `float` | `threshold=0.5` |
| `true` / `false` | `bool` | `verbose=true` |
| `None` | `None` | `default=None` |
| `[1,2,3]` | `list` | `items=[1,2,3]` |
| `{"a":1}` | `dict` | `config={"a":1}` |
| `py(expr)` | eval'd | `fn=py(lambda x: x+1)` |
| anything else | `str` | `name=hello` |

## Function Path Resolution

The CLI resolves function paths in this order:

```bash
m mod/fn          # → mod.fn()
m mod/submod/fn   # → mod.submod.fn()
m mod/            # → mod.forward()
m /fn             # → default_mod.fn()
m fn              # → default_mod.fn() (if fn exists, else mod.forward("fn"))
```

## Common Commands

### Module Discovery
```bash
m mods                    # List all modules
m mods search=agent       # Search modules
m tree                    # Module tree (name → path)
m tree orbit=core         # Core modules only
m search agent            # Fuzzy search
m info agent              # Module info + schema
m fns agent               # List functions
m schema agent            # Function signatures
m code agent              # View source code
```

### Storage
```bash
m put mykey '{"data": "value"}'
m get mykey
m put secret '{"key": "sk-..."}' encrypt=true password=mypass
m get secret password=mypass
```

### Keys
```bash
m get_key main                          # Get/create ecdsa key
m get_key sub crypto_type=sr25519       # Substrate key
m sign '{"msg": "hi"}' key=main        # Sign data
m verify '{"msg": "hi"}' signature=... address=0x...
```

### Servers
```bash
m serve api port=8000     # Start server
m servers                 # List servers
m kill api                # Stop server
```

### Git
```bash
m push "commit message"   # Add, commit, push
m repos                   # List git repos
m clone mod_name          # Clone a module
```

### System
```bash
m free_port               # Get available port
m used_ports              # List used ports
m hash "data" mode=sha256 # Hash data
m time                    # Current timestamp
```

## Output

Function results are printed to stdout with green coloring. Generators stream output line by line. Execution time is shown after completion.

```bash
$ m mods search=agent
['agent', 'agent.memory', 'gitagent']
 elapsed: 0.12s
```
