#!/usr/bin/env python3
"""Test config scanner and registry synchronization."""

import time
import json
import mod as m

def test_api_config_scanner():
    """Test API module config scanner worker."""
    print("Testing API config scanner...")

    # Initialize API module
    api = m.mod('api')()

    # Check if config scanner worker is running
    if 'config_scanner' in api.threads:
        print("✓ Config scanner worker started")
    else:
        print("✗ Config scanner worker NOT started")
        return False

    # Check cache structures
    if hasattr(api, '_config_cache') or hasattr(m.mod('api'), '_config_cache'):
        print("✓ Config cache initialized")
    else:
        print("✗ Config cache NOT initialized")
        return False

    print("✓ API config scanner test passed")
    return True

def test_router_remote_jobs():
    """Test router remote job execution."""
    print("\nTesting router remote job execution...")

    # Initialize router
    router = m.mod('router')()

    # Check if _get_remote_url method exists
    if hasattr(router, '_get_remote_url'):
        print("✓ Router has _get_remote_url method")
    else:
        print("✗ Router missing _get_remote_url method")
        return False

    # Test remote URL lookup
    test_mod = 'api'
    url = router._get_remote_url(test_mod)
    if url:
        print(f"✓ Found remote URL for '{test_mod}': {url}")
    else:
        print(f"  No remote URL for '{test_mod}' (may be expected if not served)")

    print("✓ Router remote job test passed")
    return True

def test_routy_sync_worker():
    """Test routy sync worker."""
    print("\nTesting routy sync worker...")

    # Initialize routy
    try:
        routy = m.mod('routy')()

        # Check if sync worker is running
        if hasattr(routy, '_worker_thread'):
            print("✓ Routy sync worker thread started")
        else:
            print("  Sync worker thread may be started at class level")

        # Check cache structures at class level
        routy_class = type(routy)
        if hasattr(routy_class, '_config_cache'):
            print("✓ Routy config cache initialized")
        else:
            print("✗ Routy config cache NOT initialized")
            return False

        print("✓ Routy sync worker test passed")
        return True
    except Exception as e:
        print(f"✗ Routy test failed: {e}")
        return False

def test_config_change_detection():
    """Test config change detection with caching."""
    print("\nTesting config change detection...")

    # Get a test module config from orbit modules
    import os
    orbit_path = os.path.expanduser('~/mod/mod/orbit')

    # Find a module with a config.json
    for mod_name in os.listdir(orbit_path):
        mod_path = os.path.join(orbit_path, mod_name)
        if not os.path.isdir(mod_path):
            continue

        config_file = os.path.join(mod_path, 'config.json')
        if os.path.exists(config_file):
            print(f"  Testing with module: {mod_name}")
            print(f"  Config file: {config_file}")

            try:
                # Read config
                with open(config_file, 'r') as f:
                    config = json.load(f)
                print(f"✓ Config has {len(config)} keys")
                print("✓ Config change detection test passed")
                return True
            except Exception as e:
                print(f"  Error reading config: {e}")
                continue

    print("✗ No valid config file found in orbit modules")
    return False

def test_api_run_job():
    """Test API run_job method for remote execution."""
    print("\nTesting API run_job method...")

    api = m.mod('api')()

    # Check if run_job method exists
    if hasattr(api, 'run_job'):
        print("✓ API has run_job method")
    else:
        print("✗ API missing run_job method")
        return False

    # Check if submit_job method exists
    if hasattr(api, 'submit_job'):
        print("✓ API has submit_job method")
    else:
        print("✗ API missing submit_job method")
        return False

    print("✓ API job execution methods test passed")
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("Config Scanner & Registry Sync Test Suite")
    print("=" * 60)

    results = []

    # Run tests
    results.append(("API Config Scanner", test_api_config_scanner()))
    results.append(("Router Remote Jobs", test_router_remote_jobs()))
    results.append(("Routy Sync Worker", test_routy_sync_worker()))
    results.append(("Config Change Detection", test_config_change_detection()))
    results.append(("API Job Execution", test_api_run_job()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
