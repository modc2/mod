"""
Integration tests comparing LocalFS CID generation with actual IPFS node.

These tests require a running IPFS daemon and will be skipped if IPFS is not available.
Run with: pytest test_ipfs_integration.py -v
"""

import pytest
import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from localfs.mod import LocalFS

# Try to import IPFS client
try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'ipfs'))
    from ipfs.ipfs import IpfsClient
    IPFS_AVAILABLE = True
except ImportError:
    IPFS_AVAILABLE = False
    IpfsClient = None


def check_ipfs_running():
    """Check if IPFS daemon is running and accessible."""
    if not IPFS_AVAILABLE:
        return False
    try:
        client = IpfsClient()
        client.id()
        return True
    except Exception:
        return False


# Skip all tests if IPFS is not available
pytestmark = pytest.mark.skipif(
    not check_ipfs_running(),
    reason="IPFS daemon not running or not available"
)


class TestIPFSCIDComparison:
    """Compare LocalFS CID generation with actual IPFS."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_ipfs')
        self.ipfs = IpfsClient()

    def teardown_method(self):
        """Clean up test data."""
        import shutil
        if Path('/tmp/test_localfs_ipfs').exists():
            shutil.rmtree('/tmp/test_localfs_ipfs')

    def test_simple_text_matches_ipfs(self):
        """Simple text (via file) should produce same CID as IPFS."""
        data = b"hello world"

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Add to IPFS via file interface
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            ipfs_result = self.ipfs.add_file(temp_path)
            ipfs_cid = ipfs_result['Hash']

            # Clean up
            self.ipfs.pin_rm(ipfs_cid)
            Path(temp_path).unlink()

            assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_json_object_matches_ipfs(self):
        """JSON object should produce same CID as IPFS."""
        data = {"test": "data", "number": 42, "nested": {"key": "value"}}

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Get CID from IPFS
        ipfs_cid = self.ipfs.cid(data)

        # Clean up IPFS
        try:
            self.ipfs.pin_rm(ipfs_cid)
        except:
            pass

        assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"

    def test_bytes_data_matches_ipfs(self):
        """Raw bytes should produce same CID as IPFS."""
        data = b"raw binary data \x00\x01\x02\xff"

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Add to IPFS using add_file via temp file
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            ipfs_result = self.ipfs.add_file(temp_path)
            ipfs_cid = ipfs_result['Hash']

            # Clean up
            self.ipfs.pin_rm(ipfs_cid)
            Path(temp_path).unlink()

            assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_empty_data_matches_ipfs(self):
        """Empty data should produce same CID as IPFS."""
        data = b""

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Add to IPFS
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            ipfs_result = self.ipfs.add_file(temp_path)
            ipfs_cid = ipfs_result['Hash']

            # Clean up
            self.ipfs.pin_rm(ipfs_cid)
            Path(temp_path).unlink()

            assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_unicode_data_matches_ipfs(self):
        """Unicode data should produce same CID as IPFS."""
        data = "Hello 世界 🌍 Привет мир".encode('utf-8')

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Add to IPFS via file interface
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            ipfs_result = self.ipfs.add_file(temp_path)
            ipfs_cid = ipfs_result['Hash']

            # Clean up
            self.ipfs.pin_rm(ipfs_cid)
            Path(temp_path).unlink()

            assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_large_data_matches_ipfs(self):
        """
        Large data should produce same CID as IPFS.

        Note: For files larger than ~256KB, IPFS uses chunking and creates a Merkle DAG.
        LocalFS currently stores files as single blocks, so CIDs will differ for large files.
        This test uses a smaller file size (100KB) that doesn't trigger chunking.
        """
        # Create 100KB of data (below IPFS chunking threshold)
        data = b"x" * (100 * 1024)

        # Get CID from LocalFS
        localfs_cid = self.localfs.cid(data)

        # Add to IPFS
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            ipfs_result = self.ipfs.add_file(temp_path)
            ipfs_cid = ipfs_result['Hash']

            # Clean up
            self.ipfs.pin_rm(ipfs_cid)
            Path(temp_path).unlink()

            assert localfs_cid == ipfs_cid, f"LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"
        finally:
            if Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_multiple_entries_consistency(self):
        """Multiple different entries should all match IPFS."""
        # Only test JSON objects since IPFS module's cid() converts to JSON
        test_cases = [
            {"key": "value1"},
            {"key": "value2"},
            {"test": "data", "number": 1},
            {"test": "data", "number": 2},
        ]

        for i, data in enumerate(test_cases):
            # Get CID from LocalFS
            localfs_cid = self.localfs.cid(data)

            # Get CID from IPFS
            ipfs_cid = self.ipfs.cid(data)

            # Clean up IPFS
            try:
                self.ipfs.pin_rm(ipfs_cid)
            except:
                pass

            assert localfs_cid == ipfs_cid, \
                f"Test case {i}: LocalFS CID {localfs_cid} != IPFS CID {ipfs_cid}"


class TestIPFSDataRetrieval:
    """Test that data stored in LocalFS can be understood by IPFS and vice versa."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_ipfs')
        self.ipfs = IpfsClient()

    def teardown_method(self):
        """Clean up test data."""
        import shutil
        if Path('/tmp/test_localfs_ipfs').exists():
            shutil.rmtree('/tmp/test_localfs_ipfs')

    def test_localfs_cid_ipfs_retrieval(self):
        """
        Test that a CID computed by LocalFS can be used to retrieve
        the same data from IPFS (if IPFS has it).
        """
        data = {"test": "cross-platform", "value": 123}

        # Store in both systems
        localfs_cid = self.localfs.put(data)
        ipfs_cid = self.ipfs.put(data)

        # CIDs should match
        assert localfs_cid == ipfs_cid, "CIDs should match between systems"

        # Data should be retrievable from both
        localfs_data = self.localfs.get(localfs_cid)
        ipfs_data = self.ipfs.get(ipfs_cid)

        assert localfs_data == data == ipfs_data, "Data should match across systems"

        # Clean up
        self.localfs.rm(localfs_cid)
        self.ipfs.rm(ipfs_cid)

    def test_storage_compatibility(self):
        """Test that LocalFS can be used as a drop-in replacement for IPFS."""
        test_objects = [
            {"type": "user", "name": "Alice"},
            {"type": "user", "name": "Bob"},
            {"type": "config", "settings": {"debug": True}},
        ]

        for obj in test_objects:
            # Store in LocalFS
            localfs_cid = self.localfs.put(obj)

            # Store in IPFS
            ipfs_cid = self.ipfs.put(obj)

            # CIDs must match
            assert localfs_cid == ipfs_cid, \
                f"CIDs don't match for {obj}: LocalFS={localfs_cid}, IPFS={ipfs_cid}"

            # Retrieved data must match
            assert self.localfs.get(localfs_cid) == self.ipfs.get(ipfs_cid)

            # Clean up
            self.localfs.rm(localfs_cid)
            self.ipfs.rm(ipfs_cid)


if __name__ == '__main__':
    if not check_ipfs_running():
        print("⚠️  IPFS daemon not running. Start IPFS with:")
        print("   ipfs daemon")
        print("\nOr use the mod framework:")
        print("   m.mod('ipfs')().start_node()")
        sys.exit(1)

    pytest.main([__file__, '-v', '--tb=short'])
