#!/usr/bin/env python3
"""
Quick test script to verify LocalFS CID compatibility with IPFS.
Run this to quickly check if CID generation is working correctly.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from localfs.mod import LocalFS


def print_section(title):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_cid_format():
    """Test basic CID format."""
    print_section("Testing CID Format")

    localfs = LocalFS(storage_path='/tmp/test_localfs_quick')

    test_cases = [
        ("Simple text", b"hello world"),
        ("Empty data", b""),
        ("Unicode", "Hello 世界 🌍"),
        ("JSON", {"key": "value", "number": 42}),
        ("Binary", b"\x00\x01\x02\xff"),
    ]

    all_passed = True

    for name, data in test_cases:
        try:
            cid = localfs.cid(data)

            # Verify format
            is_valid = (
                cid.startswith('Qm') and
                len(cid) == 46 and
                localfs.iscid(cid)
            )

            status = "✅ PASS" if is_valid else "❌ FAIL"
            print(f"{status} {name:20s} -> {cid}")

            if not is_valid:
                all_passed = False

        except Exception as e:
            print(f"❌ FAIL {name:20s} -> Error: {e}")
            all_passed = False

    return all_passed


def test_determinism():
    """Test CID determinism."""
    print_section("Testing Determinism")

    localfs = LocalFS(storage_path='/tmp/test_localfs_quick')

    data = b"determinism test"
    cids = [localfs.cid(data) for _ in range(5)]

    all_same = all(cid == cids[0] for cid in cids)

    if all_same:
        print(f"✅ PASS All 5 computations produced: {cids[0]}")
    else:
        print(f"❌ FAIL CIDs vary: {cids}")

    return all_same


def test_known_cids():
    """Test against known IPFS CIDs."""
    print_section("Testing Known IPFS CIDs")

    localfs = LocalFS(storage_path='/tmp/test_localfs_quick')

    # Known IPFS CID for "hello world"
    # This is the CID that `ipfs add` produces with UnixFS wrapping
    test_cases = [
        ("hello world", b"hello world", "Qmf412jQZiuVUtdgnB36FXFX7xg5V6KEbSJ4dpQuhkLyfD"),
    ]

    all_passed = True

    for name, data, expected_cid in test_cases:
        try:
            cid = localfs.cid(data)

            if cid == expected_cid:
                print(f"✅ PASS {name:20s}")
                print(f"       Expected: {expected_cid}")
                print(f"       Got:      {cid}")
            else:
                print(f"❌ FAIL {name:20s}")
                print(f"       Expected: {expected_cid}")
                print(f"       Got:      {cid}")
                all_passed = False

        except Exception as e:
            print(f"❌ FAIL {name:20s} -> Error: {e}")
            all_passed = False

    return all_passed


def test_roundtrip():
    """Test put/get roundtrip."""
    print_section("Testing Put/Get Roundtrip")

    localfs = LocalFS(storage_path='/tmp/test_localfs_quick')

    test_data = {"test": "roundtrip", "value": 123}

    try:
        # Store
        cid = localfs.put(test_data)
        print(f"Stored data with CID: {cid}")

        # Retrieve
        retrieved = localfs.get(cid)
        print(f"Retrieved data: {retrieved}")

        # Verify
        if retrieved == test_data:
            print("✅ PASS Roundtrip successful")
            success = True
        else:
            print("❌ FAIL Data mismatch")
            success = False

        # Cleanup
        localfs.rm(cid)

        return success

    except Exception as e:
        print(f"❌ FAIL Error: {e}")
        return False


def test_ipfs_comparison():
    """Test direct comparison with IPFS if available."""
    print_section("Testing IPFS Comparison (Optional)")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'ipfs'))
        from ipfs.ipfs import IpfsClient

        ipfs = IpfsClient()
        localfs = LocalFS(storage_path='/tmp/test_localfs_quick')

        # Test JSON objects only, as IPFS module's cid() method
        # always converts data to JSON format via put()
        test_cases = [
            ("JSON object 1", {"key": "value"}),
            ("JSON object 2", {"test": "data", "number": 42}),
            ("JSON array", [1, 2, 3, "four"]),
        ]

        all_passed = True

        for name, data in test_cases:
            try:
                localfs_cid = localfs.cid(data)
                ipfs_cid = ipfs.cid(data)

                if localfs_cid == ipfs_cid:
                    print(f"✅ PASS {name:20s}")
                    print(f"       CID: {localfs_cid}")
                else:
                    print(f"❌ FAIL {name:20s}")
                    print(f"       LocalFS: {localfs_cid}")
                    print(f"       IPFS:    {ipfs_cid}")
                    all_passed = False

                # Cleanup
                try:
                    ipfs.pin_rm(ipfs_cid)
                except:
                    pass

            except Exception as e:
                print(f"❌ FAIL {name:20s} -> Error: {e}")
                all_passed = False

        return all_passed

    except Exception as e:
        print(f"⚠️  SKIP IPFS not available: {e}")
        print("   (This is optional - start IPFS daemon to enable)")
        return None  # None means skipped


def main():
    """Run all quick tests."""
    print("\n" + "="*60)
    print("  LocalFS CID Compatibility Quick Test")
    print("="*60)

    results = []

    # Run tests
    results.append(("CID Format", test_cid_format()))
    results.append(("Determinism", test_determinism()))
    results.append(("Known CIDs", test_known_cids()))
    results.append(("Roundtrip", test_roundtrip()))
    ipfs_result = test_ipfs_comparison()
    if ipfs_result is not None:
        results.append(("IPFS Comparison", ipfs_result))

    # Summary
    print_section("Summary")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {name}")

    print(f"\n{passed}/{total} test groups passed")

    if passed == total:
        print("\n🎉 All tests passed! LocalFS CID generation is compatible with IPFS.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above for details.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
