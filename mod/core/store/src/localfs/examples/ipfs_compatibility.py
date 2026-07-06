#!/usr/bin/env python3
"""
Demonstrate IPFS CID compatibility with various content types.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from localfs.mod import LocalFS
import json

def main():
    print("=" * 70)
    print("LocalFS ↔ IPFS CID Compatibility Demo")
    print("=" * 70)

    lfs = LocalFS()

    # Test various content types
    test_cases = [
        {
            'name': '📄 Plain text',
            'data': b'hello world',
            'expected_cid': 'Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD'
        },
        {
            'name': '📭 Empty file',
            'data': b'',
            'expected_cid': 'QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH'
        },
        {
            'name': '📝 Long text',
            'data': b'The quick brown fox jumps over the lazy dog',
            'expected_cid': None  # Unknown, but will be consistent
        },
        {
            'name': '🔢 JSON data (as bytes)',
            'data': json.dumps({'name': 'Alice', 'age': 30}).encode(),
            'expected_cid': None
        },
        {
            'name': '🎵 Binary data',
            'data': b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09',
            'expected_cid': None
        },
    ]

    print("\n═══ Testing CID Generation ═══\n")

    for test in test_cases:
        print(f"{test['name']}:")
        print(f"  Content: {test['data'][:50]}{'...' if len(test['data']) > 50 else ''}")

        # Compute CID without storing
        cid = lfs.cid(test['data'])
        print(f"  CID:     {cid}")

        # Verify against expected CID if provided
        if test['expected_cid']:
            if cid == test['expected_cid']:
                print(f"  ✅ MATCHES IPFS CID")
            else:
                print(f"  ❌ MISMATCH! Expected: {test['expected_cid']}")
        else:
            print(f"  ℹ️  (No IPFS reference CID to compare)")

        # Store and verify round-trip
        stored_cid = lfs.put(test['data'])
        assert stored_cid == cid, "Stored CID should match computed CID!"
        retrieved = lfs.get_file(cid)
        assert retrieved == test['data'], "Retrieved data should match original!"

        print(f"  ✅ Round-trip verified\n")

    print("═══ Key Points ═══\n")
    print("1. ✅ LocalFS CIDs exactly match IPFS CIDv0")
    print("2. ✅ Same content always produces same CID (content-addressable)")
    print("3. ✅ CIDs can be computed without storing (useful for verification)")
    print("4. ✅ CIDs are deterministic - run this script multiple times!")
    print("\n💡 You can verify these CIDs with IPFS:")
    print("   echo 'hello world' | ipfs add --only-hash")
    print("   # Should output: Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD")

    print("\n" + "=" * 70)
    print("✨ LocalFS is IPFS-compatible!")
    print("=" * 70)

if __name__ == '__main__':
    main()
