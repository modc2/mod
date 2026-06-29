# Storage

Mod provides a layered storage system: a simple key-value store built into the core framework, a full-featured `Store` class, and decentralized storage via IPFS.

## Quick Storage (m.put / m.get)

The fastest way to persist data. Stores JSON files at `~/.mod/`.

```python
import mod as m

# Store anything
m.put('config', {'theme': 'dark', 'lang': 'en'})
# → saves to ~/.mod/config.json

# Retrieve it
config = m.get('config')
# → {'theme': 'dark', 'lang': 'en'}

# With a default
config = m.get('missing_key', default={'fallback': True})

# With encryption
m.put('secrets', {'api_key': 'sk-...'}, encrypt=True, password='pass123')
data = m.get('secrets', password='pass123')

# With cache expiry (seconds)
data = m.get('cache_key', max_age=3600)  # None if older than 1 hour
```

### How It Works

- Data is stored as JSON at `~/.mod/{key}.json`
- Each entry includes a timestamp for cache invalidation
- Encrypted entries use AES-256
- Nested keys create subdirectories: `m.put('a/b/c', data)` → `~/.mod/a/b/c.json`

## Store Class

The `Store` class (`mod/core/store/`) provides a richer interface with folder management, bulk operations, and encryption controls.

```python
from mod.core.store.store.store import Store

store = Store(path='~/.mod/myapp', password='optional')

# Basic ops
store.put('users/alice', {'role': 'admin'})
data = store.get('users/alice')

# List keys
store.keys()              # ['users/alice', ...]
store.keys(search='alice') # Filter

# Check existence
store.exists('users/alice')  # True

# Delete
store.rm('users/alice')

# Stats
store.stats()  # DataFrame with path, age, size, encrypted status
```

### Encryption

```python
# Encrypt a specific file
store.encrypt('users/alice', password='secret')

# Decrypt it
store.decrypt('users/alice', password='secret')

# Check encryption status
store.is_encrypted('users/alice')  # True

# Encrypt everything
store.encrypt_all(password='master_pass')

# Decrypt everything
store.decrypt_all(password='master_pass')

# Private mode (auto-encrypts all new entries)
private_store = Store(path='~/.mod/vault', private=True, password='pass')
private_store.put('data', {'sensitive': True})  # auto-encrypted
```

### Folder Encryption

Encrypt an entire folder into a single encrypted file:

```python
# Encrypt folder (removes originals, creates single encrypted file)
store.encrypt_folder('projects/secret', password='pass')

# Decrypt (restores original file structure)
store.decrypt_folder('projects/secret', password='pass')
```

### Cache Invalidation

```python
# Get data only if younger than max_age seconds
data = store.get('cache/prices', max_age=300)  # 5 minutes

# Force update (re-fetch even if cached)
data = store.get('cache/prices', update=True)

# Check age
age = store.get_age('cache/prices')  # seconds since last write
```

### Listing and Discovery

```python
# All keys
store.keys()

# Search keys
store.keys(search='user')

# Exclude patterns
store.keys(avoid=['temp', 'cache'])

# Get all values
store.values()

# Get items as dict
store.items()  # {'key1': data1, 'key2': data2}

# List directory contents
store.ls('users/')

# Get file paths
store.paths()
```

## IPFS Storage

For decentralized storage, use the `ipfs` orbit module. Data is content-addressed — you get a CID (Content Identifier) back that permanently references your data.

```python
ipfs = m.mod('ipfs')()

# Store JSON data → returns CID
cid = ipfs.put({'hello': 'world'})
# → 'QmXyz...'

# Retrieve by CID
data = ipfs.get(cid)
# → {'hello': 'world'}

# Store files
cid = ipfs.add_file('/path/to/file.txt')

# Retrieve files
ipfs.get_file(cid)

# Pin management
ipfs.pin_add(cid)    # Keep data persistent
ipfs.pin_rm(cid)     # Allow garbage collection
ipfs.pins()          # List pinned CIDs
```

The IPFS module auto-manages a local Kubo daemon:
- Installs Kubo v0.40.1 if missing
- Starts the daemon automatically
- Health checks every 30 seconds
- Auto-reconnects on failure
- Daemon PID tracked at `~/.ipfs/daemon.pid`

### CID Integration with m.get

The core `m.get()` supports CID lookups directly:

```python
# If the key looks like a CID, fetches from IPFS
data = m.get('QmXyz...')
```

## Storage Locations

| Location | Purpose |
|----------|---------|
| `~/.mod/` | Default storage root |
| `~/.mod/{key}.json` | Individual stored values |
| `~/.mod/key/` | Cryptographic keys |
| `~/.mod/store/` | Store class data |
| `~/.mod/tree/` | Module tree cache |
| `~/.mod/api/` | API server data |
| `~/.mod/server/` | Server registry |
| `~/.ipfs/` | IPFS daemon data |
