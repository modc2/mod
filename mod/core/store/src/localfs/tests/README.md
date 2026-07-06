# LocalFS CID Compatibility Tests

This directory contains comprehensive tests to ensure that LocalFS CID (Content Identifier) hashing mimics IPFS behavior exactly.

## Test Files

### `test_cid_compatibility.py`
Unit tests that verify LocalFS CID generation without requiring IPFS:
- **CID Format Tests**: Validates CIDv0 format (Qm..., base58, 46 chars)
- **Determinism Tests**: Same data always produces same CID
- **UnixFS Encoding Tests**: Verifies protobuf encoding matches IPFS spec
- **Data Type Tests**: Tests bytes, strings, JSON objects, unicode, large files
- **Interface Compatibility**: Ensures methods match IPFS client interface

### `test_ipfs_integration.py`
Integration tests that compare LocalFS with actual IPFS node:
- **Direct CID Comparison**: Compares CIDs produced by LocalFS vs IPFS
- **Cross-Platform Storage**: Verifies data can be exchanged between systems
- **Drop-in Replacement**: Tests LocalFS as IPFS replacement

**Note**: These tests require a running IPFS daemon and will be skipped if not available.

## Running Tests

### Quick Test (No IPFS Required)
```bash
# Run unit tests only
pytest test_cid_compatibility.py -v

# Or using Python
python test_cid_compatibility.py
```

### Full Test Suite (Requires IPFS)
```bash
# Start IPFS daemon first
ipfs daemon

# Run all tests including integration
pytest test_cid_compatibility.py test_ipfs_integration.py -v

# Or run integration tests only
pytest test_ipfs_integration.py -v
```

### Using Mod Framework
```bash
# Start IPFS using mod
python -c "import mod as m; m.mod('ipfs')().start_node()"

# Run tests
pytest tests/ -v
```

## Test Coverage

### CID Format Validation
✅ CIDs start with 'Qm' (CIDv0)
✅ CIDs are 46 characters long
✅ CIDs use base58 encoding
✅ Multihash prefix is correct (0x12 0x20 for SHA-256)

### Determinism
✅ Same data always produces same CID
✅ Different data produces different CIDs
✅ JSON serialization is consistent
✅ String and bytes produce same result

### IPFS Compatibility
✅ Empty file produces correct CID
✅ Simple text matches IPFS
✅ Known "hello world" CID matches
✅ JSON objects match IPFS
✅ Unicode data matches IPFS
✅ Large files match IPFS

### UnixFS/DAG-PB Encoding
✅ UnixFS Data protobuf structure
✅ DAG-PB node protobuf structure
✅ Varint encoding for lengths

### Data Types
✅ Raw bytes
✅ Strings
✅ JSON objects (dicts)
✅ JSON arrays (lists)
✅ Unicode/UTF-8
✅ Large files (>1MB)

## Known IPFS CIDs for Testing

Here are some known IPFS CIDs that can be used for verification:

```python
# Empty file
"QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH"

# "hello world" (with UnixFS wrapping)
"Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD"

# "hello world" (raw, no UnixFS)
"QmT78zSuBmuS4z925WZfrqQ1qHaJ56DQaTfyMUF7F8ff5o"
```

## Understanding IPFS CIDs

### CIDv0 Format
- Prefix: `Qm` (indicates CIDv0)
- Encoding: Base58
- Length: 46 characters
- Hash: SHA-256 (32 bytes)

### Multihash Structure
```
[hash-type][hash-length][hash-digest]
  0x12       0x20      [32 bytes]
```
- `0x12`: SHA-256 hash function
- `0x20`: 32 bytes (length of hash)
- Hash digest: Actual SHA-256 hash

### UnixFS Wrapping
IPFS wraps file data in UnixFS protobuf format:
1. **UnixFS Data**: Protobuf with Type=File, Data=content, filesize
2. **DAG-PB Node**: Protobuf wrapping the UnixFS data
3. **Hash**: SHA-256 of the DAG-PB node
4. **CID**: Base58(multihash(hash))

## Debugging Failed Tests

### If CIDs don't match IPFS:

1. **Check UnixFS encoding**:
```python
localfs = LocalFS()
data = b"test"
unixfs_data = localfs._encode_unixfs_data(data)
print(unixfs_data.hex())
```

2. **Compare with IPFS block**:
```bash
# Get raw block from IPFS
ipfs block get QmXXX | xxd

# Compare with LocalFS encoding
python -c "from localfs.mod import LocalFS; print(LocalFS()._encode_dagpb_node(LocalFS()._encode_unixfs_data(b'test')).hex())"
```

3. **Verify protobuf structure**:
```python
# UnixFS Data should look like:
# 08 02          - Type = 2 (File)
# 12 [len] [data] - Data field
# 18 [len]       - filesize field
```

## Contributing

When adding new tests:
1. Add unit tests to `test_cid_compatibility.py` for logic verification
2. Add integration tests to `test_ipfs_integration.py` for actual IPFS comparison
3. Update this README with test coverage information
4. Ensure tests are deterministic and can run in any order
