#!/usr/bin/env python3
"""
Test suite for LocalFS
"""

import os
import json
import tempfile
from pathlib import Path
from localfs import LocalFS


def test_basic_operations():
    """Test basic put/get operations."""
    print("🧪 Testing basic operations...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Test put/get with dict
        data = {"hello": "world", "number": 42}
        cid = lfs.put(data)
        print(f"  ✓ Stored data with CID: {cid}")

        retrieved = lfs.get(cid)
        assert retrieved == data, "Retrieved data doesn't match"
        print(f"  ✓ Retrieved data matches")

        # Test put/get with string
        text = "Hello, LocalFS!"
        cid2 = lfs.put(text)
        retrieved2 = lfs.get(cid2)
        assert retrieved2 == text, "Retrieved text doesn't match"
        print(f"  ✓ String storage works")

    print("✅ Basic operations passed\n")


def test_pinning():
    """Test pinning functionality."""
    print("🧪 Testing pinning...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Add with pin
        data = {"test": "pinning"}
        cid = lfs.put(data, pin=True)
        assert lfs.pinned(cid), "Content should be pinned"
        print(f"  ✓ Content pinned: {cid}")

        # List pins
        pins = lfs.pins()
        assert cid in pins['Keys'], "CID should be in pins list"
        print(f"  ✓ Pin listed correctly")

        # Unpin
        lfs.pin_rm(cid)
        assert not lfs.pinned(cid), "Content should be unpinned"
        print(f"  ✓ Content unpinned")

    print("✅ Pinning tests passed\n")


def test_file_operations():
    """Test file add/get operations."""
    print("🧪 Testing file operations...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Create a test file
        test_file = Path(tmpdir) / "test.txt"
        test_content = b"This is a test file content"
        test_file.write_bytes(test_content)

        # Add file
        result = lfs.add_file(str(test_file))
        cid = result['Hash']
        print(f"  ✓ File added with CID: {cid}")

        # Get file content
        retrieved = lfs.get_file(cid)
        assert retrieved == test_content, "File content doesn't match"
        print(f"  ✓ File content matches")

    print("✅ File operations passed\n")


def test_cid_computation():
    """Test CID computation without storage."""
    print("🧪 Testing CID computation...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Compute CID without storing
        data = {"test": "cid"}
        cid1 = lfs.cid(data)
        print(f"  ✓ CID computed: {cid1}")

        # Verify it's not stored
        assert not lfs.valid_cid(cid1), "CID shouldn't exist in storage"
        print(f"  ✓ CID not stored (as expected)")

        # Now actually store it
        cid2 = lfs.put(data)
        assert cid1 == cid2, "CIDs should match"
        assert lfs.valid_cid(cid2), "CID should exist now"
        print(f"  ✓ CID matches when stored")

    print("✅ CID computation passed\n")


def test_garbage_collection():
    """Test garbage collection of unpinned blocks."""
    print("🧪 Testing garbage collection...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Add pinned content
        pinned_data = {"pinned": True}
        pinned_cid = lfs.put(pinned_data, pin=True)

        # Add unpinned content
        unpinned_data = {"pinned": False}
        unpinned_cid = lfs.put(unpinned_data, pin=False)

        stats_before = lfs.stats()
        print(f"  ✓ Before GC: {stats_before['blocks']} blocks, {stats_before['pinned']} pinned")

        # Run garbage collection
        result = lfs.gc(aggressive=True)
        print(f"  ✓ GC removed {result['Count']} blocks")

        # Check that pinned content still exists
        assert lfs.valid_cid(pinned_cid), "Pinned content should still exist"
        print(f"  ✓ Pinned content preserved")

        # Check that unpinned content is removed
        assert not lfs.valid_cid(unpinned_cid), "Unpinned content should be removed"
        print(f"  ✓ Unpinned content removed")

    print("✅ Garbage collection passed\n")


def test_stats():
    """Test statistics reporting."""
    print("🧪 Testing statistics...")

    with tempfile.TemporaryDirectory() as tmpdir:
        lfs = LocalFS(storage_path=tmpdir)

        # Add some content
        for i in range(5):
            lfs.put({"index": i}, pin=(i % 2 == 0))

        stats = lfs.stats()
        print(f"  ✓ Stats: {json.dumps(stats, indent=2)}")

        assert stats['blocks'] == 5, "Should have 5 blocks"
        assert stats['pinned'] == 3, "Should have 3 pinned (0, 2, 4)"
        assert stats['total_size'] > 0, "Should have non-zero size"

    print("✅ Statistics passed\n")


def test_persistence():
    """Test that data persists across instances."""
    print("🧪 Testing persistence...")

    with tempfile.TemporaryDirectory() as tmpdir:
        # First instance
        lfs1 = LocalFS(storage_path=tmpdir)
        data = {"persist": "me"}
        cid = lfs1.put(data, pin=True)
        print(f"  ✓ Stored with CID: {cid}")

        # Second instance (same storage path)
        lfs2 = LocalFS(storage_path=tmpdir)
        retrieved = lfs2.get(cid)
        assert retrieved == data, "Data should persist"
        assert lfs2.pinned(cid), "Pin status should persist"
        print(f"  ✓ Data and pins persisted")

    print("✅ Persistence passed\n")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("LocalFS Test Suite")
    print("=" * 60 + "\n")

    try:
        test_basic_operations()
        test_pinning()
        test_file_operations()
        test_cid_computation()
        test_garbage_collection()
        test_stats()
        test_persistence()

        print("=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
