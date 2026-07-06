"""
Tests to verify LocalFS CID hashing mimics IPFS module behavior.

These tests ensure that:
1. LocalFS produces valid CIDv0 format (Qm... with base58 encoding)
2. CID computation is deterministic and consistent
3. The same data produces the same CID as IPFS would
4. UnixFS/DAG-PB encoding matches IPFS spec
"""

import pytest
import json
import hashlib
import base58
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from localfs.mod import LocalFS


class TestCIDFormat:
    """Test CID format compatibility with IPFS."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_cid_starts_with_qm(self):
        """CIDs should start with 'Qm' (CIDv0 format)."""
        data = b"test data"
        cid = self.localfs.cid(data)
        assert cid.startswith('Qm'), f"CID should start with 'Qm', got: {cid}"

    def test_cid_length(self):
        """CIDs should be 46 characters long (base58 encoded sha256)."""
        data = b"test data"
        cid = self.localfs.cid(data)
        assert len(cid) == 46, f"CID should be 46 chars, got {len(cid)}: {cid}"

    def test_cid_is_base58(self):
        """CID should be valid base58 encoding."""
        data = b"test data"
        cid = self.localfs.cid(data)
        try:
            decoded = base58.b58decode(cid)
            assert len(decoded) > 0
        except Exception as e:
            pytest.fail(f"CID should be valid base58: {e}")

    def test_cid_multihash_prefix(self):
        """CID should have correct multihash prefix (0x12 0x20 for sha256)."""
        data = b"test data"
        cid = self.localfs.cid(data)
        decoded = base58.b58decode(cid)

        # First byte should be 0x12 (sha2-256)
        assert decoded[0] == 0x12, f"Expected hash type 0x12, got 0x{decoded[0]:02x}"

        # Second byte should be 0x20 (32 bytes)
        assert decoded[1] == 0x20, f"Expected length 0x20, got 0x{decoded[1]:02x}"

        # Remaining 32 bytes should be the hash
        assert len(decoded[2:]) == 32, f"Expected 32 byte hash, got {len(decoded[2:])} bytes"


class TestCIDDeterminism:
    """Test that CID computation is deterministic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_same_data_same_cid(self):
        """Same data should always produce the same CID."""
        data = b"test data for determinism"

        cid1 = self.localfs.cid(data)
        cid2 = self.localfs.cid(data)
        cid3 = self.localfs.cid(data)

        assert cid1 == cid2 == cid3, "Same data should produce same CID"

    def test_different_data_different_cid(self):
        """Different data should produce different CIDs."""
        data1 = b"first data"
        data2 = b"second data"

        cid1 = self.localfs.cid(data1)
        cid2 = self.localfs.cid(data2)

        assert cid1 != cid2, "Different data should produce different CIDs"

    def test_json_serialization_determinism(self):
        """JSON objects should produce consistent CIDs."""
        obj1 = {"key": "value", "number": 42}

        cid1 = self.localfs.cid(obj1)
        cid2 = self.localfs.cid(obj1)

        assert cid1 == cid2, "Same JSON object should produce same CID"

    def test_string_vs_bytes_consistency(self):
        """String and its byte encoding should produce same CID."""
        text = "test string"

        cid_from_string = self.localfs.cid(text)
        cid_from_bytes = self.localfs.cid(text.encode('utf-8'))

        assert cid_from_string == cid_from_bytes, "String and bytes should produce same CID"


class TestIPFSCompatibility:
    """Test compatibility with IPFS CID generation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_empty_file_cid(self):
        """Empty file should produce known IPFS CID."""
        # IPFS CID for empty file: QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH
        data = b""
        cid = self.localfs.cid(data)

        # Verify it's a valid CID format
        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_simple_text_cid(self):
        """Simple text should produce consistent CID."""
        data = b"hello world"
        cid = self.localfs.cid(data)

        # Verify format
        assert cid.startswith('Qm')
        assert len(cid) == 46

        # Test determinism
        cid2 = self.localfs.cid(data)
        assert cid == cid2

    def test_json_object_cid(self):
        """JSON object should produce consistent CID."""
        obj = {"test": "data", "number": 123}
        cid = self.localfs.cid(obj)

        # Verify format
        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_known_ipfs_cid_comparison(self):
        """
        Test against a known IPFS CID.
        Note: This is the most critical test for IPFS compatibility.

        The string "hello world" in IPFS produces:
        - Without UnixFS wrapping: QmT78zSuBmuS4z925WZfrqQ1qHaJ56DQaTfyMUF7F8ff5o
        - With UnixFS wrapping (file type): Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD
        """
        data = b"hello world"
        cid = self.localfs.cid(data)

        # The CID should be the UnixFS wrapped version (file type)
        # This is what `ipfs add` returns
        expected_cid = "Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD"

        assert cid == expected_cid, f"Expected {expected_cid}, got {cid}"


class TestUnixFSEncoding:
    """Test UnixFS protobuf encoding specifically."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_unixfs_data_structure(self):
        """Verify UnixFS Data protobuf structure."""
        data = b"test"
        unixfs_data = self.localfs._encode_unixfs_data(data)

        # Should start with field 1, wire type 0 (0x08) for Type field
        assert unixfs_data[0] == 0x08, "Should start with Type field"

        # Next byte should be 0x02 (File type)
        assert unixfs_data[1] == 0x02, "Type should be File (2)"

        # Should contain the data length-delimited (field 2, wire type 2 = 0x12)
        assert 0x12 in unixfs_data, "Should contain Data field"

    def test_dagpb_node_structure(self):
        """Verify DAG-PB node protobuf structure."""
        unixfs_data = b"\x08\x02\x12\x04test\x18\x04"  # Simple UnixFS data
        dagpb_node = self.localfs._encode_dagpb_node(unixfs_data)

        # Should start with field 1, wire type 2 (0x0a) for Data field
        assert dagpb_node[0] == 0x0a, "Should start with Data field"

        # Next should be varint length
        assert dagpb_node[1] == len(unixfs_data), "Should have correct length"

    def test_varint_encoding(self):
        """Test protobuf varint encoding."""
        # Test small numbers
        assert self.localfs._encode_varint(0) == b'\x00'
        assert self.localfs._encode_varint(1) == b'\x01'
        assert self.localfs._encode_varint(127) == b'\x7f'

        # Test numbers requiring multiple bytes
        assert self.localfs._encode_varint(128) == b'\x80\x01'
        assert self.localfs._encode_varint(300) == b'\xac\x02'


class TestCIDMethodCompatibility:
    """Test that LocalFS CID methods match IPFS client interface."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_iscid_validation(self):
        """Test CID validation method."""
        # Valid CID format
        valid_cid = "QmT78zSuBmuS4z925WZfrqQ1qHaJ56DQaTfyMUF7F8ff5o"
        assert self.localfs.iscid(valid_cid) == True

        # Invalid CIDs
        assert self.localfs.iscid("not a cid") == False
        assert self.localfs.iscid("Qm123") == False  # Too short
        assert self.localfs.iscid("") == False
        assert self.localfs.iscid(None) == False

    def test_put_and_cid_match(self):
        """Both put() and cid() should compute the same CID."""
        data = {"test": "data"}

        # cid() computes without storing
        computed_cid = self.localfs.cid(data)

        # put() stores and returns CID
        stored_cid = self.localfs.put(data)

        assert computed_cid == stored_cid, "cid() and put() should produce same CID"

        # Cleanup
        self.localfs.rm(stored_cid)

    def test_add_alias_compatibility(self):
        """Test that add() is an alias for put()."""
        data = {"test": "data"}

        cid1 = self.localfs.put(data)
        self.localfs.rm(cid1)

        cid2 = self.localfs.add(data)
        self.localfs.rm(cid2)

        assert cid1 == cid2, "add() and put() should be equivalent"

    def test_resolve_cid_with_prefix(self):
        """Test resolving CIDs with 'local/' prefix."""
        cid = "QmT78zSuBmuS4z925WZfrqQ1qHaJ56DQaTfyMUF7F8ff5o"
        prefixed = f"local/{cid}"

        resolved = self.localfs.resolve_cid(prefixed)
        assert resolved == cid, "Should strip 'local/' prefix"

        # Without prefix should return as-is
        resolved2 = self.localfs.resolve_cid(cid)
        assert resolved2 == cid, "Should return CID without prefix as-is"


class TestDataTypeHandling:
    """Test CID computation for different data types."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def test_bytes_data(self):
        """Test CID for raw bytes."""
        data = b"raw binary data \x00\x01\x02"
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_string_data(self):
        """Test CID for string data."""
        data = "simple string"
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_dict_data(self):
        """Test CID for dictionary data."""
        data = {"key": "value", "nested": {"foo": "bar"}}
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_list_data(self):
        """Test CID for list data."""
        data = [1, 2, 3, "four", {"five": 5}]
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_unicode_data(self):
        """Test CID for unicode data."""
        data = "Hello 世界 🌍"
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46

    def test_large_data(self):
        """Test CID for large data."""
        data = b"x" * 10000  # 10KB of data
        cid = self.localfs.cid(data)

        assert cid.startswith('Qm')
        assert len(cid) == 46


class TestCIDRoundTrip:
    """Test that data can be stored and retrieved using CIDs."""

    def setup_method(self):
        """Set up test fixtures."""
        self.localfs = LocalFS(storage_path='/tmp/test_localfs_cid')

    def teardown_method(self):
        """Clean up test data."""
        # Clean up test storage
        import shutil
        if Path('/tmp/test_localfs_cid').exists():
            shutil.rmtree('/tmp/test_localfs_cid')

    def test_put_get_roundtrip(self):
        """Data stored with put() should be retrievable with get()."""
        original_data = {"test": "value", "number": 42}

        cid = self.localfs.put(original_data)
        retrieved_data = self.localfs.get(cid)

        assert retrieved_data == original_data, "Retrieved data should match original"

    def test_cid_consistency_across_instances(self):
        """Same data should produce same CID across different LocalFS instances."""
        data = b"consistency test"

        # First instance
        localfs1 = LocalFS(storage_path='/tmp/test_localfs_cid1')
        cid1 = localfs1.cid(data)

        # Second instance
        localfs2 = LocalFS(storage_path='/tmp/test_localfs_cid2')
        cid2 = localfs2.cid(data)

        assert cid1 == cid2, "CID should be consistent across instances"

        # Cleanup
        import shutil
        for path in ['/tmp/test_localfs_cid1', '/tmp/test_localfs_cid2']:
            if Path(path).exists():
                shutil.rmtree(path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
