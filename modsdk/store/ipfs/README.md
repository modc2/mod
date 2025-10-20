# IPFS Module

This module provides IPFS (InterPlanetary File System) integration.

## Contents

- `docker-compose.yml` - Docker Compose configuration for running IPFS node
- `ipfs/ipfs.py` - Python implementation for IPFS operations

## Quick Start

### Using Docker Compose

```bash
docker-compose up -d
```

This will start an IPFS node using the configuration in `docker-compose.yml`.

### Using Python Module

```python
from ipfs.ipfs import *

# Your IPFS operations here
```

## Features

- Easy IPFS node deployment via Docker
- Python interface for IPFS operations
- Decentralized file storage and retrieval

## Requirements

- Docker and Docker Compose (for containerized deployment)
- Python 3.x (for Python module usage)

## Documentation

For more information about IPFS, visit [https://ipfs.io](https://ipfs.io)

## License

See project root for license information.
