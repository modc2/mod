"""Example Usage of the Scrape Module

This file demonstrates how to use the scrape module to collect
and analyze blockchain events.
"""

import mod as m
from scrape import Scrape


def example_basic_scraping():
    """Example: Basic event scraping"""
    m.print('=== Example 1: Basic Transfer Event Scraping ===', color='blue')

    # Initialize scraper
    scraper = Scrape(network='testnet')

    # Scrape transfer events for MARKET token (last 2 weeks)
    transfers = scraper.scrape_transfers(
        token='market',
        weeks=2
    )

    m.print(f'Found {len(transfers)} transfers', color='green')

    # Show first few transfers
    for transfer in transfers[:5]:
        m.print(f"From: {transfer['from'][:10]}... To: {transfer['to'][:10]}... Value: {transfer['value']}")

    return transfers


def example_filtered_scraping():
    """Example: Scraping with filters"""
    m.print('=== Example 2: Filtered Event Scraping ===', color='blue')

    scraper = Scrape(network='testnet')

    # Scrape transfers TO a specific address
    recipient = '0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb'
    transfers = scraper.scrape_transfers(
        token='market',
        to_address=recipient,
        weeks=4
    )

    m.print(f'Found {len(transfers)} transfers to {recipient}', color='green')
    return transfers


def example_balance_tracking():
    """Example: Track balances from events"""
    m.print('=== Example 3: Balance Tracking ===', color='blue')

    scraper = Scrape(network='testnet')

    # Get transfer events
    transfers = scraper.scrape_transfers(token='market', weeks=2)

    # Track balances from events (with on-chain verification)
    balances = scraper.track_balances_from_events(
        transfers,
        verify_onchain=True,
        token='market'
    )

    m.print(f'Found {len(balances)} addresses with balances', color='green')

    # Show top 10 holders
    sorted_balances = sorted(balances.items(), key=lambda x: x[1], reverse=True)
    for addr, balance in sorted_balances[:10]:
        m.print(f'{addr}: {balance} MARKET')

    return balances


def example_registry_events():
    """Example: Scrape registry events"""
    m.print('=== Example 4: Registry Events ===', color='blue')

    scraper = Scrape(network='testnet')

    # Scrape mod registration events
    registrations = scraper.scrape_registry_events(
        event_type='ModRegistered',
        weeks=4
    )

    m.print(f'Found {len(registrations)} mod registrations', color='green')

    for reg in registrations[:5]:
        m.print(f"Mod ID: {reg.get('modId')} Owner: {reg.get('owner', 'N/A')[:10]}...")

    return registrations


def example_custom_events():
    """Example: Scrape custom contract events"""
    m.print('=== Example 5: Custom Event Scraping ===', color='blue')

    scraper = Scrape(network='testnet')

    # Scrape any custom event from a contract
    events = scraper.scrape_custom_event(
        contract_name='market',
        event_name='Credit',  # or any other event name
        weeks=2
    )

    m.print(f'Found {len(events)} Credit events', color='green')
    return events


def example_active_addresses():
    """Example: Get active addresses"""
    m.print('=== Example 6: Active Addresses ===', color='blue')

    scraper = Scrape(network='testnet')

    # Get all unique addresses that sent transfers
    active_senders = scraper.get_active_addresses(
        contract_name='market',
        event_name='Transfer',
        address_field='from',
        weeks=2
    )

    m.print(f'Found {len(active_senders)} active senders', color='green')

    # Get all unique receivers
    active_receivers = scraper.get_active_addresses(
        contract_name='market',
        event_name='Transfer',
        address_field='to',
        weeks=2
    )

    m.print(f'Found {len(active_receivers)} active receivers', color='green')

    return active_senders, active_receivers


def example_export_events():
    """Example: Export events to file"""
    m.print('=== Example 7: Export Events ===', color='blue')

    scraper = Scrape(network='testnet')

    # Scrape events
    transfers = scraper.scrape_transfers(token='market', weeks=1)

    # Export as JSON
    json_path = scraper.export_events(
        transfers,
        filename='market_transfers.json',
        format='json'
    )

    # Export as CSV
    csv_path = scraper.export_events(
        transfers,
        filename='market_transfers.csv',
        format='csv'
    )

    m.print(f'Exported to {json_path} and {csv_path}', color='green')
    return json_path, csv_path


def example_timeline_analysis():
    """Example: Create event timeline"""
    m.print('=== Example 8: Timeline Analysis ===', color='blue')

    scraper = Scrape(network='testnet')

    # Get transfers
    transfers = scraper.scrape_transfers(token='market', weeks=2)

    # Create timeline grouped by block
    timeline = scraper.get_event_timeline(
        transfers,
        time_field='block_number'
    )

    m.print(f'Events across {len(timeline)} blocks', color='green')

    # Find blocks with most activity
    active_blocks = sorted(timeline.items(), key=lambda x: len(x[1]), reverse=True)[:5]
    for block, events in active_blocks:
        m.print(f'Block {block}: {len(events)} transfers')

    return timeline


def example_using_chain_module():
    """Example: Using scraper through main chain module"""
    m.print('=== Example 9: Using Chain Module Integration ===', color='blue')

    # Use the main chain module
    chain = m.mod('chain')(network='testnet')

    # Access scraper through chain
    transfers = chain.scrape_transfers(token='market', weeks=1)
    m.print(f'Found {len(transfers)} transfers via chain module', color='green')

    # Or get scraper instance for advanced usage
    scraper = chain.scraper()
    balances = scraper.track_balances_from_events(transfers, verify_onchain=False)
    m.print(f'Tracked {len(balances)} addresses', color='green')

    return transfers, balances


def example_chase_integration():
    """Example: Chase pattern - continuous monitoring

    This shows how you might use the scraper for "chase" functionality
    to continuously monitor and act on blockchain events.
    """
    m.print('=== Example 10: Chase Integration (Continuous Monitoring) ===', color='blue')

    scraper = Scrape(network='testnet')

    # Get the latest block
    current_block = scraper.w3.eth.block_number
    m.print(f'Starting chase from block {current_block}', color='cyan')

    # Chase pattern: monitor recent blocks for events
    # In a real implementation, this would run continuously
    chase_window = 100  # blocks to monitor

    transfers = scraper.scrape_transfers(
        token='market',
        from_block=current_block - chase_window,
        to_block=current_block
    )

    # Process each transfer (example: filter by value)
    large_transfers = [t for t in transfers if t['value'] > 1000 * (10 ** 18)]

    m.print(f'Found {len(large_transfers)} large transfers (>1000 tokens)', color='yellow')

    for transfer in large_transfers:
        m.print(f"Large transfer: {transfer['value'] / (10**18):.2f} tokens")
        m.print(f"  From: {transfer['from']}")
        m.print(f"  To: {transfer['to']}")
        m.print(f"  Tx: {transfer['transaction_hash']}")

    return large_transfers


def example_parallel_scraping():
    """Example: Parallel scraping of multiple contracts"""
    m.print('=== Example 11: Parallel Multi-Contract Scraping ===', color='blue')

    scraper = Scrape(network='testnet', max_workers=5)

    # Define multiple scrape jobs
    configs = [
        {'contract_name': 'market', 'event_name': 'Transfer', 'weeks': 1},
        {'contract_name': 'market', 'event_name': 'Approval', 'weeks': 1},
        {'contract_name': 'registry', 'event_name': 'ModRegistered', 'weeks': 2}
    ]

    # Scrape all in parallel
    results = scraper.scrape_multiple_contracts(configs, parallel=True)

    for key, events in results.items():
        m.print(f'{key}: {len(events)} events', color='green')

    return results


def example_caching():
    """Example: Using caching for faster repeated queries"""
    m.print('=== Example 12: Caching ===', color='blue')

    scraper = Scrape(network='testnet', enable_cache=True)

    m.print('First scrape (will cache results):', color='yellow')
    transfers1 = scraper.scrape_transfers(token='market', weeks=1)

    m.print('Second scrape (will use cache):', color='yellow')
    transfers2 = scraper.scrape_transfers(token='market', weeks=1)

    m.print(f'Both queries returned {len(transfers1)} events', color='green')

    # Clear cache
    scraper.clear_cache('market_Transfer*')

    return transfers1


def example_checkpoint_resume():
    """Example: Using checkpoints for long scrapes"""
    m.print('=== Example 13: Checkpoint/Resume ===', color='blue')

    scraper = Scrape(network='testnet')

    # Scrape with checkpoint (can be interrupted and resumed)
    transfers = scraper.scrape_transfers(
        token='market',
        weeks=4,
        checkpoint_file='/tmp/scrape_checkpoint.json'
    )

    m.print(f'Scraped {len(transfers)} transfers with checkpoint support', color='green')
    return transfers


def example_transfer_stats():
    """Example: Get transfer statistics"""
    m.print('=== Example 14: Transfer Statistics ===', color='blue')

    scraper = Scrape(network='testnet')

    transfers = scraper.scrape_transfers(token='market', weeks=2)

    # Get comprehensive stats
    stats = scraper.get_transfer_stats(transfers, token='market')

    m.print(f"Total transfers: {stats['total_transfers']}", color='cyan')
    m.print(f"Total volume: {stats['total_volume']}", color='cyan')
    m.print(f"Unique senders: {stats['unique_senders']}", color='cyan')
    m.print(f"Unique receivers: {stats['unique_receivers']}", color='cyan')
    m.print(f"Avg transfer size: {stats['avg_transfer_size']}", color='cyan')

    m.print('Top senders:', color='yellow')
    for addr, volume in stats['top_senders'][:5]:
        m.print(f"  {addr[:10]}...: {volume}")

    return stats


def example_parallel_batch_scraping():
    """Example: Parallel batch scraping for faster processing"""
    m.print('=== Example 15: Parallel Batch Scraping ===', color='blue')

    scraper = Scrape(network='testnet', max_workers=5, rate_limit=0.05)

    # Scrape with parallel batches
    transfers = scraper.scrape_batch_parallel(
        contract_name='market',
        event_name='Transfer',
        weeks=2,
        batch_size=5000
    )

    m.print(f'Found {len(transfers)} transfers using parallel batching', color='green')
    return transfers


def example_estimate_time():
    """Example: Estimate scraping time"""
    m.print('=== Example 16: Time Estimation ===', color='blue')

    scraper = Scrape(network='testnet')

    current_block = scraper.w3.eth.block_number
    from_block = current_block - 100000  # Last ~100k blocks

    estimate = scraper.estimate_scrape_time(
        from_block=from_block,
        to_block=current_block,
        batch_size=10000
    )

    m.print(f"Total blocks: {estimate['total_blocks']:,}", color='cyan')
    m.print(f"Number of batches: {estimate['num_batches']}", color='cyan')
    m.print(f"Estimated time (sequential): {estimate['estimated_time_sequential']}", color='yellow')
    m.print(f"Estimated time (parallel): {estimate['estimated_time_parallel']}", color='green')

    return estimate


def run_all_examples():
    """Run all examples"""
    examples = [
        example_basic_scraping,
        example_filtered_scraping,
        example_balance_tracking,
        example_registry_events,
        example_custom_events,
        example_active_addresses,
        example_export_events,
        example_timeline_analysis,
        example_using_chain_module,
        example_chase_integration,
        example_parallel_scraping,
        example_caching,
        example_checkpoint_resume,
        example_transfer_stats,
        example_parallel_batch_scraping,
        example_estimate_time
    ]

    for example in examples:
        try:
            m.print(f'\n{"="*60}', color='cyan')
            example()
            m.print(f'{"="*60}\n', color='cyan')
        except Exception as e:
            m.print(f'Error in {example.__name__}: {e}', color='red')


if __name__ == '__main__':
    # Run a specific example
    # example_basic_scraping()

    # Or run all examples
    run_all_examples()
