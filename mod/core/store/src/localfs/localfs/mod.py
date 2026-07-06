"""
LocalFS - Content-Addressable Local File Storage
A local filesystem implementation that mimics IPFS behavior without requiring IPFS.
Uses Python-to-Rust bindings for performance-critical hashing and I/O operations.
"""

import json
import os
import hashlib
from typing import Dict, Any
from pathlib import Path
import base58
import mod as m


class LocalFS:
    """
    Content-addressable local filesystem storage.
    Compatible with IPFS-like CID interface but purely local.
    """

    prefix = 'localfs'

    def __init__(self, storage_path: str = None, use_rust=False):
        """
        Initialize LocalFS with a storage directory.

        Args:
            storage_path: Base directory for content storage.
                         Defaults to ~/.localfs
        """
        self.storage_path = Path(storage_path or os.path.expanduser('~/.localfs'))
        self.blocks_path = self.storage_path / 'blocks'
        self.pins_path = self.storage_path / 'pins'

        # Ensure directories exist
        self.blocks_path.mkdir(parents=True, exist_ok=True)
        self.pins_path.mkdir(parents=True, exist_ok=True)
        self.rust = None
        self.use_rust = use_rust
        # Try to import Rust bindings, fall back to pure Python
        self.ensure_bindings()

    def _meta_path(self, cid: str) -> Path:
        """
        Get the filesystem path for a CID's metadata.

        Args:
            cid: Content identifier

        Returns:
            Path to the metadata JSON file
        """
        # Shard by first 4 chars of CID
        shard = cid[:4]
        meta_dir = self.blocks_path / shard
        meta_dir.mkdir(exist_ok=True)
        return meta_dir / f"{cid}.json"

    def _save_meta(self, cid: str, metadata: Dict[str, Any]):
        """Save metadata for a specific CID."""
        meta_path = self._meta_path(cid)
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=2)

    def _load_meta(self, cid: str) -> Dict[str, Any]:
        """Load metadata for a specific CID."""
        meta_path = self._meta_path(cid)
        if meta_path.exists():
            with open(meta_path, 'r') as f:
                return json.load(f)
        return {}

    def _delete_meta(self, cid: str):
        """Delete metadata for a specific CID."""
        meta_path = self._meta_path(cid)
        if meta_path.exists():
            os.remove(meta_path)

    def _compute_cid(self, data: bytes) -> str:
        """
        Compute a content identifier (CID) for the given data.
        Uses SHA-256 hash encoded in base58 (IPFS CIDv0 style).
        Wraps data in UnixFS/DAG-PB format for IPFS compatibility.

        Args:
            data: Raw bytes to hash

        Returns:
            CID string (Qm... format)
        """
        if self.use_rust and self.rust is not None:
            # Use Rust implementation for faster hashing
            return self.rust.compute_cid(data)
        else:
            # Pure Python implementation with UnixFS/DAG-PB encoding
            # Build UnixFS Data protobuf structure
            unixfs_data = self._encode_unixfs_data(data)

            # Build DAG-PB node wrapping the UnixFS data
            dag_pb_node = self._encode_dagpb_node(unixfs_data)

            # Hash the DAG-PB encoded data
            hash_bytes = hashlib.sha256(dag_pb_node).digest()

            # Add multihash prefix for SHA-256 (0x12 = sha2-256, 0x20 = 32 bytes)
            multihash = b'\x12\x20' + hash_bytes

            return base58.b58encode(multihash).decode('ascii')

    def _compute_cidv1(self, data: bytes, codec: int = 0x70) -> str:
        """
        Compute a CIDv1 for the given data (base32lower multibase).

        Args:
            data: Raw bytes to hash
            codec: Multicodec code (0x70=dag-pb, 0x55=raw, 0x71=dag-cbor)

        Returns:
            CIDv1 string (bafy... format for dag-pb)
        """
        import base64
        # For dag-pb, wrap in UnixFS first
        if codec == 0x70:
            unixfs_data = self._encode_unixfs_data(data)
            dag_pb_node = self._encode_dagpb_node(unixfs_data)
            hash_bytes = hashlib.sha256(dag_pb_node).digest()
        else:
            hash_bytes = hashlib.sha256(data).digest()

        # CIDv1 = version(1) + codec + multihash
        # multihash = hash_fn(0x12) + digest_size(0x20) + digest
        multihash = b'\x12\x20' + hash_bytes
        cid_bytes = self._encode_varint(1) + self._encode_varint(codec) + multihash

        # base32lower encoding (multibase prefix 'b')
        return 'b' + base64.b32encode(cid_bytes).decode('ascii').lower().rstrip('=')

    def to_cidv1(self, cidv0: str) -> str:
        """
        Convert a CIDv0 (Qm...) to CIDv1 (base32lower).

        Args:
            cidv0: CIDv0 string

        Returns:
            CIDv1 string
        """
        import base64
        if not cidv0.startswith('Qm'):
            return cidv0  # already v1 or unknown
        multihash = base58.b58decode(cidv0)
        # CIDv1 = version(1) + codec(dag-pb=0x70) + multihash
        cid_bytes = self._encode_varint(1) + self._encode_varint(0x70) + multihash
        return 'b' + base64.b32encode(cid_bytes).decode('ascii').lower().rstrip('=')

    def to_cidv0(self, cidv1: str) -> str:
        """
        Convert a CIDv1 (base32lower, dag-pb) back to CIDv0 (Qm...).

        Args:
            cidv1: CIDv1 string

        Returns:
            CIDv0 string or original if not convertible
        """
        import base64
        if cidv1.startswith('Qm'):
            return cidv1  # already v0
        if not cidv1.startswith('b'):
            return cidv1  # unknown format
        # Remove multibase prefix and decode base32
        b32 = cidv1[1:].upper()
        # Add padding
        padding = (8 - len(b32) % 8) % 8
        b32 += '=' * padding
        try:
            cid_bytes = base64.b32decode(b32)
        except Exception:
            return cidv1
        # Parse: version(varint) + codec(varint) + multihash
        version, offset = self._decode_varint(cid_bytes, 0)
        if version != 1:
            return cidv1
        codec, offset = self._decode_varint(cid_bytes, offset)
        if codec != 0x70:  # only dag-pb can convert to v0
            return cidv1
        multihash = cid_bytes[offset:]
        return base58.b58encode(multihash).decode('ascii')

    def _decode_varint(self, data: bytes, offset: int) -> tuple:
        """Decode a varint from bytes at given offset."""
        result = 0
        shift = 0
        while offset < len(data):
            byte = data[offset]
            result |= (byte & 0x7F) << shift
            offset += 1
            if not (byte & 0x80):
                break
            shift += 7
        return result, offset

    def _encode_unixfs_data(self, data: bytes) -> bytes:
        """
        Encode data in UnixFS protobuf format.
        UnixFS Data protobuf structure:
          message Data {
            enum DataType {
              Raw = 0;
              Directory = 1;
              File = 2;
              Metadata = 3;
              Symlink = 4;
              HAMTShard = 5;
            }
            required DataType Type = 1;
            optional bytes Data = 2;
            optional uint64 filesize = 3;
            repeated uint64 blocksizes = 4;
            optional uint64 hashType = 5;
            optional uint64 fanout = 6;
          }

        For a simple file:
        - Type = 2 (File)
        - Data = <file content>
        - filesize = <size>
        """
        # Protobuf field encoding: (field_number << 3) | wire_type
        # wire_type: 0=varint, 2=length-delimited

        result = bytearray()

        # Field 1: Type (varint) = 2 (File)
        result.extend(b'\x08')  # field 1, wire type 0 (varint)
        result.extend(b'\x02')  # value = 2 (File type)

        # Field 2: Data (length-delimited)
        if data:
            result.extend(b'\x12')  # field 2, wire type 2 (length-delimited)
            result.extend(self._encode_varint(len(data)))
            result.extend(data)

        # Field 3: filesize (varint) = size of data
        result.extend(b'\x18')  # field 3, wire type 0 (varint)
        result.extend(self._encode_varint(len(data)))

        return bytes(result)

    def _encode_dagpb_node(self, unixfs_data: bytes) -> bytes:
        """
        Encode a DAG-PB node containing UnixFS data.
        DAG-PB protobuf structure:
          message PBNode {
            repeated PBLink Links = 2;
            optional bytes Data = 1;
          }

        For a simple file with no links:
        - Data = <UnixFS data>
        """
        result = bytearray()

        # Field 1: Data (length-delimited)
        result.extend(b'\x0a')  # field 1, wire type 2 (length-delimited)
        result.extend(self._encode_varint(len(unixfs_data)))
        result.extend(unixfs_data)

        return bytes(result)

    def _encode_varint(self, value: int) -> bytes:
        """
        Encode an integer as protobuf varint.
        """
        result = bytearray()
        while value > 127:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

    def _block_path(self, cid: str) -> Path:
        """
        Get the filesystem path for a CID's block.
        Uses sharding to avoid too many files in one directory.

        Args:
            cid: Content identifier

        Returns:
            Path to the block file
        """
        # Shard by first 4 chars of CID
        shard = cid[:4]
        block_dir = self.blocks_path / shard
        block_dir.mkdir(exist_ok=True)
        return block_dir / cid

    def put(self, data: Any, pin: bool = True) -> str:
        """
        Store data and return its CID.

        Args:
            data: Data to store (dict will be JSON serialized)
            pin: Whether to pin the content

        Returns:
            CID string
        """
        # Convert data to bytes
        if isinstance(data, dict) or isinstance(data, list):
            data_bytes = json.dumps(data).encode('utf-8')
        elif isinstance(data, str):
            data_bytes = data.encode('utf-8')
        elif isinstance(data, bytes):
            data_bytes = data
        else:
            data_bytes = str(data).encode('utf-8')

        # Compute CID
        cid = self._compute_cid(data_bytes)

        # Write block to disk
        block_path = self._block_path(cid)
        if self.use_rust and self.rust is not None:
            # Use Rust for fast I/O
            self.rust.write_block(str(block_path), data_bytes)
        else:
            with open(block_path, 'wb') as f:
                f.write(data_bytes)

        # Save metadata
        metadata = {
            'size': len(data_bytes),
            'type': type(data).__name__,
            'pinned': pin
        }
        self._save_meta(cid, metadata)

        return cid

    add = put  # Alias for IPFS compatibility

    def get(self, cid: str) -> Any:
        """
        Retrieve data by CID. Supports CIDv0 (Qm...) and CIDv1 (bafy...).

        Args:
            cid: Content identifier (v0 or v1)

        Returns:
            Parsed data (dict if JSON, bytes otherwise)
        """
        # Handle prefix
        if cid.startswith(f'{self.prefix}/'):
            cid = cid[len(self.prefix) + 1:]

        # If CIDv1, try to convert to v0 for lookup
        lookup_cid = cid
        if not cid.startswith('Qm') and self.iscid(cid):
            converted = self.to_cidv0(cid)
            if converted != cid:
                lookup_cid = converted

        # Read block
        block_path = self._block_path(lookup_cid)
        if not block_path.exists():
            # Try original CID if conversion didn't help
            if lookup_cid != cid:
                block_path = self._block_path(cid)
            if not block_path.exists():
                raise FileNotFoundError(f"Block not found: {cid}")

        if self.use_rust and self.rust is not None:
            data_bytes = self.rust.read_block(str(block_path))
        else:
            with open(block_path, 'rb') as f:
                data_bytes = f.read()

        # Try to parse as JSON
        try:
            output =  json.loads(data_bytes.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            output =  data_bytes
        
        if isinstance(output, bytes):
            return output.decode('utf-8', errors='replace')
        return output

    def get_file(self, cid: str) -> bytes:
        """
        Retrieve raw file content by CID.

        Args:
            cid: Content identifier

        Returns:
            Raw bytes
        """
        if cid.startswith(f'{self.prefix}/'):
            cid = cid[len(self.prefix) + 1:]

        block_path = self._block_path(cid)
        if not block_path.exists():
            raise FileNotFoundError(f"Block not found: {cid}")

        if self.use_rust and self.rust is not None:
            return self.rust.read_block(str(block_path))
        else:
            with open(block_path, 'rb') as f:
                return f.read()

    cat = get_file  # Alias for IPFS compatibility

    def add_file(self, file_path: str) -> Dict[str, Any]:
        """
        Add a file from the filesystem.

        Args:
            file_path: Path to file to add

        Returns:
            Dict with Hash (CID) and metadata
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, 'rb') as f:
            data = f.read()

        cid = self.put(data, pin=True)

        return {
            'Hash': cid,
            'Name': os.path.basename(file_path),
            'Size': len(data)
        }

    def rm(self, cid: str) -> Dict[str, Any]:
        """
        Remove content by CID.

        Args:
            cid: Content identifier

        Returns:
            Status dict
        """
        # Remove block
        block_path = self._block_path(cid)
        if block_path.exists():
            os.remove(block_path)

        # Remove metadata
        self._delete_meta(cid)

        return {"Status": "Removed"}

    def pin_add(self, cid: str) -> Dict[str, Any]:
        """
        Pin content to prevent garbage collection.

        Args:
            cid: Content identifier

        Returns:
            Pin status dict
        """
        metadata = self._load_meta(cid)
        if not metadata:
            raise ValueError(f"CID not found: {cid}")

        metadata['pinned'] = True
        self._save_meta(cid, metadata)

        return {'Pins': [cid]}

    def pin_rm(self, cid: str) -> Dict[str, Any]:
        """
        Unpin content.

        Args:
            cid: Content identifier

        Returns:
            Unpin status dict
        """
        metadata = self._load_meta(cid)
        if metadata:
            metadata['pinned'] = False
            self._save_meta(cid, metadata)

        return {'Pins': [cid]}

    def pins(self, cid: str = None) -> Dict[str, Any]:
        """
        List pinned content.

        Args:
            cid: Optional specific CID to check

        Returns:
            Dict with Keys containing pinned CIDs
        """
        if cid:
            metadata = self._load_meta(cid)
            if metadata.get('pinned'):
                return {'Keys': {cid: {'Type': 'recursive'}}}
            return {'Keys': {}}

        # Scan all metadata files for pinned content
        pinned = {}
        for shard_dir in self.blocks_path.iterdir():
            if shard_dir.is_dir():
                for meta_file in shard_dir.glob('*.json'):
                    cid = meta_file.stem
                    metadata = self._load_meta(cid)
                    if metadata.get('pinned'):
                        pinned[cid] = {'Type': 'recursive'}

        return {'Keys': pinned}

    def pinned(self, cid: str) -> bool:
        """
        Check if content is pinned.

        Args:
            cid: Content identifier

        Returns:
            True if pinned
        """
        metadata = self._load_meta(cid)
        return metadata.get('pinned', False)

    def cid(self, data: Any) -> str:
        """
        Compute CID without storing.

        Args:
            data: Data to compute CID for

        Returns:
            CID string
        """
        # Convert to bytes
        if isinstance(data, dict) or isinstance(data, list):
            data_bytes = json.dumps(data).encode('utf-8')
        elif isinstance(data, str):
            data_bytes = data.encode('utf-8')
        elif isinstance(data, bytes):
            data_bytes = data
        else:
            data_bytes = str(data).encode('utf-8')

        return self._compute_cid(data_bytes)

    def resolve_cid(self, path: str) -> str:
        """
        Resolve a path to a CID.

        Args:
            path: Path like 'local/Qm...'

        Returns:
            Just the CID part
        """
        if path.startswith(f'{self.prefix}/'):
            return path[len(self.prefix) + 1:]
        if path.startswith('local/'):
            return path[6:]
        return path

    def iscid(self, text: str) -> bool:
        """
        Check if text looks like a valid CID (multiformat).
        Supports CIDv0 (Qm...) and CIDv1 (bafy..., bafk..., etc).

        Args:
            text: String to check

        Returns:
            True if it looks like a CID
        """
        if not isinstance(text, str) or len(text) < 10:
            return False
        # CIDv0: base58btc-encoded multihash (Qm prefix, 46 chars)
        if text.startswith('Qm') and len(text) == 46:
            return True
        # CIDv1: multibase prefix + multicodec + multihash
        # Common base32lower CIDv1 prefixes: bafy (dag-pb), bafk (raw), bafyr (dag-cbor)
        cidv1_prefixes = ('bafy', 'bafk', 'bafyr', 'bafyb', 'bafzb')
        if any(text.startswith(p) for p in cidv1_prefixes):
            return True
        # base58btc CIDv1: starts with 'z'
        if text.startswith('z') and len(text) >= 40:
            try:
                base58.b58decode(text[1:])
                return True
            except Exception:
                return False
        return False

    def valid_cid(self, cid: str) -> bool:
        """
        Check if a CID exists in storage.

        Args:
            cid: Content identifier

        Returns:
            True if the block exists
        """
        if cid.startswith(f'{self.prefix}/'):
            cid = cid[len(self.prefix) + 1:]
        if not self.iscid(cid):
            return False
        return self._block_path(cid).exists()

    def gc(self, aggressive: bool = False) -> Dict[str, Any]:
        """
        Garbage collect unpinned blocks.

        Args:
            aggressive: If True, remove all unpinned blocks

        Returns:
            Dict with removed CIDs
        """
        removed = []

        # Scan all metadata files
        for shard_dir in self.blocks_path.iterdir():
            if shard_dir.is_dir():
                for meta_file in shard_dir.glob('*.json'):
                    cid = meta_file.stem
                    metadata = self._load_meta(cid)

                    if not metadata.get('pinned') and (aggressive or True):
                        try:
                            # Remove block
                            block_path = self._block_path(cid)
                            if block_path.exists():
                                os.remove(block_path)

                            # Remove metadata
                            self._delete_meta(cid)
                            removed.append(cid)
                        except Exception as e:
                            print(f"Error removing {cid}: {e}")

        return {'Removed': removed, 'Count': len(removed)}

    def stats(self) -> Dict[str, Any]:
        """
        Get storage statistics.

        Returns:
            Dict with stats
        """
        total_blocks = 0
        total_pinned = 0
        total_size = 0

        # Scan all metadata files
        for shard_dir in self.blocks_path.iterdir():
            if shard_dir.is_dir():
                for meta_file in shard_dir.glob('*.json'):
                    total_blocks += 1
                    metadata = self._load_meta(meta_file.stem)
                    total_size += metadata.get('size', 0)
                    if metadata.get('pinned'):
                        total_pinned += 1

        return {
            'blocks': total_blocks,
            'pinned': total_pinned,
            'total_size': total_size,
            'storage_path': str(self.storage_path)
        }

    def test(self) -> bool:
        """
        Test storage by adding and retrieving data.

        Returns:
            True if test passes
        """
        test_obj = {"test_key": "test_value", "timestamp": m.time()}
        print("Testing LocalFS storage...", test_obj)

        cid = self.add(test_obj)
        print(f"Stored with CID: {cid}")

        retrieved = self.get(cid)
        print(f"Retrieved: {retrieved}")

        success = retrieved == test_obj

        # Cleanup
        self.rm(cid)

        return success

    def __str__(self):
        return f"LocalFS(storage_path={self.storage_path})"
    

    _binding_cache = None   # class-level: None=unchecked, False=unavailable, module=available

    def ensure_bindings(self):
        """
        Ensure Rust bindings are available, attempt to compile if not.
        """
        if self.use_rust and self.rust is not None:
            return True
        cls = type(self)
        if cls._binding_cache is not None:
            if cls._binding_cache is False:
                self.use_rust = False
                return False
            self.rust = cls._binding_cache
            self.use_rust = True
            return True
        try:
            from . import localfs_rs
            cls._binding_cache = localfs_rs
            self.rust = localfs_rs
            self.use_rust = True
            print("[localfs] Rust bindings are now available")
            return True
        except ImportError:
            cls._binding_cache = False
            self.use_rust = False
            print("[localfs] Rust bindings not available, using pure Python")
            return False
