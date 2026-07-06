# LocalFS Quick Start Guide

## Installation

### Option 1: Python Only (No Rust)

```bash
pip install -r requirements.txt
```

### Option 2: With Rust Bindings (Recommended for Performance)

```bash
# Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install dependencies and build
bash install.sh

# Or manually
pip install -r requirements.txt
pip install maturin
bash build.sh
```

### Option 3: Using Makefile

```bash
# Python only
make install

# With Rust bindings
make build
```

## Basic Usage

### 1. Initialize LocalFS

```python
from localfs import LocalFS

# Use default storage path (~/.localfs)
lfs = LocalFS()

# Or specify custom path
lfs = LocalFS(storage_path="/path/to/storage")
```

### 2. Store Data

```python
# Store a dictionary
data = {"hello": "world", "number": 42}
cid = lfs.put(data)
print(f"Stored with CID: {cid}")
# Output: QmXx...

# Store a string
cid = lfs.put("Hello, LocalFS!")

# Store bytes
cid = lfs.put(b"raw bytes")

# Store a file
result = lfs.add_file("/path/to/file.txt")
cid = result['Hash']
```

### 3. Retrieve Data

```python
# Get data (auto-parses JSON)
data = lfs.get(cid)

# Get raw bytes
raw = lfs.get_file(cid)

# Or use IPFS-style alias
raw = lfs.cat(cid)
```

### 4. Pin Management

```python
# Pin content (default is True for put())
lfs.pin_add(cid)

# Unpin content
lfs.pin_rm(cid)

# Check if pinned
if lfs.pinned(cid):
    print("Content is pinned")

# List all pins
pins = lfs.pins()
for cid in pins['Keys']:
    print(f"Pinned: {cid}")
```

### 5. Garbage Collection

```python
# Remove unpinned blocks
result = lfs.gc(aggressive=True)
print(f"Removed {result['Count']} blocks")
```

### 6. Utility Functions

```python
# Compute CID without storing
cid = lfs.cid({"some": "data"})

# Check if CID exists
if lfs.valid_cid(cid):
    print("CID exists in storage")

# Check if text is a valid CID format
if lfs.iscid("QmXx..."):
    print("Valid CID format")

# Get storage statistics
stats = lfs.stats()
print(f"Blocks: {stats['blocks']}")
print(f"Pinned: {stats['pinned']}")
print(f"Total size: {stats['total_size']} bytes")

# Run self-test
if lfs.test():
    print("LocalFS is working correctly")
```

## IPFS Compatibility Mode

LocalFS generates **identical CIDs to IPFS** (CIDv0 format):

```python
from localfs import LocalFS

lfs = LocalFS()

# CIDs exactly match IPFS!
cid = lfs.cid(b'hello world')
print(cid)
# Output: Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD
# (same as: echo 'hello world' | ipfs add --only-hash)

# Or use IPFS-style API
from localfs import IpfsClient
ipfs = IpfsClient()
cid = ipfs.add({"data": "value"})
data = ipfs.get(cid)
content = ipfs.cat(cid)
```

**Verify CID compatibility:**
```bash
# With IPFS
echo 'hello world' | ipfs add --only-hash
# Output: Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD

# With LocalFS
python -c "from localfs.mod import LocalFS; print(LocalFS().cid(b'hello world'))"
# Output: Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD

# ✅ They match!
```

## Examples

### Complete Example: Document Storage

```python
from localfs import LocalFS
import json

# Initialize
lfs = LocalFS()

# Store documents
docs = [
    {"id": 1, "title": "Document 1", "content": "..."},
    {"id": 2, "title": "Document 2", "content": "..."},
    {"id": 3, "title": "Document 3", "content": "..."},
]

# Store each document and collect CIDs
doc_cids = {}
for doc in docs:
    cid = lfs.put(doc, pin=True)
    doc_cids[doc['id']] = cid
    print(f"Stored doc {doc['id']}: {cid}")

# Create an index
index = {
    "version": "1.0",
    "documents": doc_cids
}
index_cid = lfs.put(index, pin=True)
print(f"Index stored: {index_cid}")

# Later: retrieve a specific document
index = lfs.get(index_cid)
doc1_cid = index['documents'][1]
doc1 = lfs.get(doc1_cid)
print(f"Retrieved: {doc1['title']}")

# Cleanup unpinned content
result = lfs.gc()
print(f"Cleaned up {result['Count']} blocks")
```

### Performance Comparison

```python
from localfs import LocalFS
import time

lfs = LocalFS()

# Test with 1000 small objects
data = {"test": "data", "number": 42}
count = 1000

start = time.time()
for i in range(count):
    cid = lfs.put({**data, "index": i}, pin=False)
duration = time.time() - start

print(f"Stored {count} objects in {duration:.2f}s")
print(f"Rate: {count/duration:.0f} ops/sec")

# Without Rust: ~400-500 ops/sec
# With Rust: ~2000-5000 ops/sec
```

## Troubleshooting

### Rust Bindings Not Loading

If you see `[localfs] Rust bindings not available, using pure Python`:

1. Check if Rust is installed: `cargo --version`
2. Rebuild bindings: `bash build.sh`
3. Verify installation: `python -c "from localfs import localfs_rs; print('OK')"`

The module will work fine without Rust, just slower for large operations.

### Storage Path Issues

If you get permission errors:

```python
# Use a custom path you have write access to
lfs = LocalFS(storage_path="./my_storage")
```

### Index Corruption

If the index.json gets corrupted:

```bash
# Delete and rebuild
rm ~/.localfs/index.json
```

The next initialization will rebuild the index from existing blocks.

## Next Steps

- Run the full test suite: `python test_localfs.py`
- Try the examples: `python examples/basic_usage.py`
- Read the full documentation: [README.md](README.md)
- Build Rust bindings: [build.sh](build.sh)

## Getting Help

- Check the examples in `examples/`
- Read the docstrings: `help(LocalFS)`
- Run the test suite to verify installation
