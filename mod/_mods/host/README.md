# API Module

## Overview

A decentralized API system built on IPFS for registering, versioning, and managing Python modules with cryptographic signatures.

## Features

- **IPFS Storage**: Decentralized content-addressed storage for modules
- **Cryptographic Signatures**: Verify module authenticity with key-based signatures
- **Version History**: Track changes and rollback to previous versions
- **Registry System**: User-based module registration and discovery
- **Schema Management**: Store and retrieve module schemas

## Core Components

### Api Class

Main interface for interacting with the decentralized module registry.

#### Key Methods

**Registration & Management**
- `reg(mod, key, comment, update)` - Register or update a module
- `dereg(mod, key)` - Deregister a module
- `regall(mods, key, comment, update)` - Batch register modules

**Query & Discovery**
- `mod(mod, key, schema, content)` - Get module information
- `mods(search, key)` - List all registered modules
- `names(search)` - Get module names
- `registry(key, search, update)` - Access the registry

**Version Control**
- `history(mod, features, key, df)` - View module history
- `diff(mod, update)` - Compare versions
- `setback(mod, content, key)` - Rollback to previous version

**User Management**
- `users(search)` - List all users
- `user_info(key)` - Get user information
- `user_mods(key)` - List user's modules

**Content & Schema**
- `content(mod, expand)` - Get module content
- `schema(mod)` - Get module schema
- `verify(mod_info)` - Verify cryptographic signature

## Usage Examples

```python
import mod as m

# Initialize API
api = m.mod('api.api')()

# Register a module
info = api.reg('mymodule', key='mykey', comment='Initial release')

# List all modules
mods = api.mods()

# Get module info
mod_info = api.mod('mymodule', schema=True, content=True)

# View history
history = api.history('mymodule', df=True)

# Get diff between versions
diff = api.diff('mymodule')

# Rollback to previous version
api.setback('mymodule', content=3)
```

## Configuration

Configuration stored in `api/config.json`:

```json
{
    "port": 8000
}
```

## Registry Structure

Registry stored at `~/.mod/api/registry.json`:

```json
{
    "user_address": {
        "module_name": "ipfs_cid"
    }
}
```

## Module Info Structure

```json
{
    "content": "content_cid",
    "schema": "schema_cid",
    "prev": "previous_version_cid",
    "name": "module_name",
    "created": 1234567890,
    "updated": 1234567890,
    "key": "user_address",
    "url": "module_url",
    "signature": "cryptographic_signature",
    "cid": "module_info_cid"
}
```

## Dependencies

- `mod` - Core module framework
- `requests` - HTTP requests
- IPFS storage backend

## Security

- All modules are cryptographically signed
- Signatures verified using `m.verify()`
- Key-based authentication for registration
- Content-addressed storage prevents tampering

## API Endpoints

Available endpoints: `mods`, `names`, `reg`, `mod`, `users`, `user_info`, `n`

## Utilities

`api/utils.py` provides helper functions:
- `expanduser(path)` - Expand user paths
- `load_json(file_path)` - Load JSON files
- `save_json(file_path, data)` - Save JSON files
- `logs(name)` - Access logs

## License

Open source - Free world, free code.

---

*Built with the spirit of Leonardo da Vinci, the precision of Mr. Robot, and the excellence of Cristiano Ronaldo.*