"""Test Suite for Scrape Module

Basic tests to verify scrape functionality.
"""

import mod as m


def test_import():
    """Test that scrape module can be imported"""
    try:
        from scrape import Scrape
        m.print('✓ Import successful', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Import failed: {e}', color='red')
        return False


def test_initialization():
    """Test scraper initialization"""
    try:
        from scrape import Scrape
        scraper = Scrape(network='testnet')
        assert scraper is not None
        assert scraper.w3 is not None
        assert scraper.contracts is not None
        m.print('✓ Initialization successful', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Initialization failed: {e}', color='red')
        return False


def test_chain_integration():
    """Test integration with chain module"""
    try:
        chain = m.mod('chain')(network='testnet')
        scraper = chain.scraper()
        assert scraper is not None
        m.print('✓ Chain integration successful', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Chain integration failed: {e}', color='red')
        return False


def test_block_calculation():
    """Test block range calculation"""
    try:
        from scrape import Scrape
        scraper = Scrape(network='testnet')

        current_block = scraper.w3.eth.block_number
        assert current_block > 0

        # Test weeks calculation
        weeks = 1
        seconds_in_period = weeks * 7 * 24 * 60 * 60
        blocks_in_period = seconds_in_period // scraper.block_time
        from_block = max(0, current_block - blocks_in_period)

        assert from_block < current_block
        m.print(f'✓ Block calculation successful (current: {current_block:,}, from: {from_block:,})', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Block calculation failed: {e}', color='red')
        return False


def test_event_scraping():
    """Test basic event scraping (small range)"""
    try:
        from scrape import Scrape
        scraper = Scrape(network='testnet')

        current_block = scraper.w3.eth.block_number

        # Test with very small range to avoid timeout
        transfers = scraper.scrape_transfers(
            token='market',
            from_block=max(0, current_block - 100),
            to_block=current_block
        )

        assert isinstance(transfers, list)
        m.print(f'✓ Event scraping successful ({len(transfers)} transfers found)', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Event scraping failed: {e}', color='red')
        return False


def test_balance_tracking():
    """Test balance tracking from events"""
    try:
        from scrape import Scrape
        scraper = Scrape(network='testnet')

        # Create mock transfer events
        mock_transfers = [
            {
                'from': '0x0000000000000000000000000000000000000000',
                'to': '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb',
                'value': 1000000000000000000,
            },
            {
                'from': '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb',
                'to': '0x1234567890123456789012345678901234567890',
                'value': 500000000000000000,
            }
        ]

        # Track balances without verification
        balances = scraper.track_balances_from_events(
            mock_transfers,
            verify_onchain=False,
            token='market'
        )

        assert isinstance(balances, dict)
        m.print(f'✓ Balance tracking successful ({len(balances)} addresses)', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Balance tracking failed: {e}', color='red')
        return False


def test_timeline_creation():
    """Test timeline creation"""
    try:
        from scrape import Scrape
        scraper = Scrape(network='testnet')

        # Mock events
        mock_events = [
            {'block_number': 100, 'value': 100},
            {'block_number': 100, 'value': 200},
            {'block_number': 101, 'value': 300},
        ]

        timeline = scraper.get_event_timeline(mock_events)

        assert isinstance(timeline, dict)
        assert 100 in timeline
        assert len(timeline[100]) == 2
        m.print('✓ Timeline creation successful', color='green')
        return True
    except Exception as e:
        m.print(f'✗ Timeline creation failed: {e}', color='red')
        return False


def run_tests():
    """Run all tests"""
    m.print('\n' + '='*60, color='cyan')
    m.print('Running Scrape Module Tests', color='cyan')
    m.print('='*60 + '\n', color='cyan')

    tests = [
        test_import,
        test_initialization,
        test_chain_integration,
        test_block_calculation,
        test_event_scraping,
        test_balance_tracking,
        test_timeline_creation
    ]

    results = []
    for test in tests:
        m.print(f'\nRunning {test.__name__}...', color='yellow')
        result = test()
        results.append(result)

    m.print('\n' + '='*60, color='cyan')
    passed = sum(results)
    total = len(results)
    m.print(f'Tests: {passed}/{total} passed', color='green' if passed == total else 'yellow')
    m.print('='*60 + '\n', color='cyan')

    return all(results)


if __name__ == '__main__':
    success = run_tests()
    exit(0 if success else 1)
