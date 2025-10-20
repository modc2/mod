# ModSDK - IPFS Store

## Overview
This module provides IPFS (InterPlanetary File System) storage capabilities for ModSDK.

## Components

### Docker Compose Setup
- **Location**: `docker-compose.yml`
- Provides containerized IPFS node deployment

### IPFS Module
- **Location**: `modsdk/store/ipfs/ipfs.py`
- Core IPFS integration and functionality
- Handles file storage and retrieval operations

### Store Module
- **Location**: `modsdk/store/store.py`
- Main storage interface
- Abstracts storage operations across different backends

## Getting Started

### Prerequisites
- Docker and Docker Compose (for containerized deployment)
- Python 3.x

### Installation
```bash
# Clone the repository
git clone <repository-url>
cd modsdk

# Install dependencies
pip install -r requirements.txt
```

### Running IPFS Node
```bash
# Start IPFS node using Docker Compose
cd modsdk/store/ipfs
docker-compose up -d
```

## Usage

```python
from modsdk.store.ipfs import IPFS
from modsdk.store import Store

# Initialize store
store = Store()

# Use IPFS storage operations
# Add your usage examples here
```

## Architecture

```
modsdk/
└── store/
    ├── store.py          # Main storage interface
    └── ipfs/
        ├── docker-compose.yml  # IPFS node configuration
        └── ipfs/
            └── ipfs.py   # IPFS implementation
```

## Features
- Decentralized file storage via IPFS
- Docker-based deployment
- Modular storage architecture
- Easy integration with ModSDK ecosystem

## Contributing
Contributions are welcome! Please submit pull requests or open issues for any improvements.

## License
See LICENSE file for details.

---
*Built with passion by the ModSDK team*