#!/usr/bin/env python3
"""
Basic usage examples for LocalFS
"""

from localfs import LocalFS
import json


def example_basic_storage():
    """Example: Basic put/get operations"""
    print("=" * 60)
    print("Example: Basic Storage")
    print("=" * 60)

    # Initialize LocalFS
    lfs = LocalFS()

    # Store a dictionary
    data = {
        "name": "LocalFS",
        "version": "0.1.0",
        "features": ["content-addressable", "pinning", "rust-bindings"]
    }

    cid = lfs.put(data)
    print(f"✓ Stored data with CID: {cid}")

    # Retrieve the data
    retrieved = lfs.get(cid)
    print(f"✓ Retrieved: {json.dumps(retrieved, indent=2)}")

    # Get stats
    stats = lfs.stats()
    print(f"✓ Storage stats: {json.dumps(stats, indent=2)}")

    print()


def example_file_storage():
    """Example: Store and retrieve files"""
    print("=" * 60)
    print("Example: File Storage")
    print("=" * 60)

    lfs = LocalFS()

    # Create a temporary file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Hello from LocalFS!\nThis is a test file.")
        temp_path = f.name

    try:
        # Add the file
        result = lfs.add_file(temp_path)
        cid = result['Hash']
        print(f"✓ Added file: {result['Name']}")
        print(f"  CID: {cid}")
        print(f"  Size: {result['Size']} bytes")

        # Retrieve the file content
        content = lfs.get_file(cid)
        print(f"✓ Retrieved content:")
        print(f"  {content.decode('utf-8')}")

    finally:
        import os
        os.unlink(temp_path)

    print()


def example_pinning():
    """Example: Pin management"""
    print("=" * 60)
    print("Example: Pinning")
    print("=" * 60)

    lfs = LocalFS()

    # Add content with pinning
    data1 = {"important": "data"}
    cid1 = lfs.put(data1, pin=True)
    print(f"✓ Added pinned content: {cid1}")

    # Add content without pinning
    data2 = {"temporary": "data"}
    cid2 = lfs.put(data2, pin=False)
    print(f"✓ Added unpinned content: {cid2}")

    # Check pin status
    print(f"✓ CID1 pinned: {lfs.pinned(cid1)}")
    print(f"✓ CID2 pinned: {lfs.pinned(cid2)}")

    # List all pins
    pins = lfs.pins()
    print(f"✓ Total pinned: {len(pins['Keys'])}")

    # Run garbage collection
    result = lfs.gc(aggressive=True)
    print(f"✓ Garbage collection removed {result['Count']} blocks")

    # Verify pinned content still exists
    print(f"✓ CID1 still exists: {lfs.valid_cid(cid1)}")
    print(f"✓ CID2 was removed: {not lfs.valid_cid(cid2)}")

    print()


def example_cid_computation():
    """Example: Compute CID without storing"""
    print("=" * 60)
    print("Example: CID Computation")
    print("=" * 60)

    lfs = LocalFS()

    data = {"test": "data"}

    # Compute CID without storing
    cid1 = lfs.cid(data)
    print(f"✓ Computed CID: {cid1}")
    print(f"  Exists in storage: {lfs.valid_cid(cid1)}")

    # Store the data
    cid2 = lfs.put(data)
    print(f"✓ Stored with CID: {cid2}")
    print(f"  CIDs match: {cid1 == cid2}")
    print(f"  Exists in storage: {lfs.valid_cid(cid2)}")

    print()


def example_ipfs_compatibility():
    """Example: IPFS-compatible API"""
    print("=" * 60)
    print("Example: IPFS Compatibility")
    print("=" * 60)

    # Can use IpfsClient alias for compatibility
    from localfs import IpfsClient

    ipfs = IpfsClient()
    print("✓ Created IpfsClient (LocalFS alias)")

    # Use IPFS-style methods
    data = {"using": "ipfs-style API"}
    cid = ipfs.add(data)
    print(f"✓ ipfs.add() -> {cid}")

    retrieved = ipfs.get(cid)
    print(f"✓ ipfs.get() -> {retrieved}")

    content = ipfs.cat(cid)
    print(f"✓ ipfs.cat() -> {content[:50]}...")

    pins = ipfs.pins()
    print(f"✓ ipfs.pins() -> {len(pins['Keys'])} pinned")

    print()


def main():
    """Run all examples"""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 18 + "LocalFS Examples" + " " * 24 + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    example_basic_storage()
    example_file_storage()
    example_pinning()
    example_cid_computation()
    example_ipfs_compatibility()

    print("=" * 60)
    print("✅ All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
