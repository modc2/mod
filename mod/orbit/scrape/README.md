# Chain Scrape Module

Comprehensive event scraping functionality for Ethereum/Base blockchain contracts. This module inherits from the main Chain module and provides powerful tools for collecting, analyzing, and exporting blockchain events.

## Features

- **Batch Event Scraping**: Automatically handles large block ranges with intelligent batching
- **Parallel Processing**: Scrape multiple contracts or batches concurrently for faster processing
- **Smart Caching**: Cache scraped events to disk for instant repeat queries
- **Checkpoint/Resume**: Save progress during long scrapes and resume from interruptions
- **Exponential Backoff**: Intelligent retry strategy with exponential backoff on failures
- **Rate Limiting**: Built-in rate limiting to prevent RPC provider throttling
- **Progress Tracking**: Real-time progress reporting with percentage completion
- **Multiple Event Types**: Support for Transfer, Approval, Registry, and custom events
- **Optimized Balance Tracking**: Calculate balances from Transfer events with parallel on-chain verification
- **Event Statistics**: Comprehensive statistics and analytics on scraped events
- **Filtering**: Filter events by indexed parameters (addresses, etc.)
- **Export**: Export events to JSON or CSV formats
- **Timeline Analysis**: Organize events into timelines for analysis
- **Time Estimation**: Estimate scraping time before starting
- **Integration**: Seamless integration with the main Chain module

## Installation

The module is part of the chain package and requires:
- `web3`
- `mod` library

```python
import mod as m
from chain.scrape import Scrape
```

## Quick Start

### Basic Usage

```python
from chain.scrape import Scrape

# Initialize scraper
scraper = Scrape(network='testnet')

# Scrape transfer events (last 2 weeks)
transfers = scraper.scrape_transfers(token='market', weeks=2)
print(f'Found {len(transfers)} transfers')
```

### Using Through Chain Module

```python
import mod as m

# Initialize chain
chain = m.mod('chain')(network='testnet')

# Use scraper through chain
transfers = chain.scrape_transfers(token='market', weeks=1)

# Or get scraper instance for advanced features
scraper = chain.scraper()
```

## API Reference

### Scrape Class

Main scraper class that inherits from `Mod` (Chain module).

#### Initialization

```python
scraper = Scrape(
    network='testnet',       # 'testnet', 'mainnet', or 'ganache'
    key='test',              # Private key identifier
    batch_size=10000,        # Blocks per batch
    block_time=2,            # Average block time (seconds)
    rate_limit=0.1,          # Seconds between requests (default 0.1)
    max_workers=5,           # Number of parallel workers (default 5)
    enable_cache=True,       # Enable disk caching (default True)
    cache_dir='~/.mod/scrape_cache',  # Cache directory
    retry_delay=1.0,         # Initial retry delay in seconds (default 1.0)
    max_retries=3            # Maximum retry attempts (default 3)
)
```

### Core Methods

#### scrape_events()

General-purpose event scraper.

```python
events = scraper.scrape_events(
    contract_name='market',    # Contract to scrape
    event_name='Transfer',     # Event to scrape
    from_block=0,              # Start block (0 = calculated from weeks)
    to_block=None,             # End block (None = latest)
    weeks=2,                   # Weeks to look back
    batch_size=None,           # Override default batch size
    filters={'from': address}, # Filter indexed parameters
    process_fn=None            # Custom processing function
)
```

**Returns**: List of events with decoded arguments

#### scrape_transfers()

Scrape ERC20 Transfer events.

```python
transfers = scraper.scrape_transfers(
    token='market',              # Token symbol
    from_address='0x...',        # Filter by sender (optional)
    to_address='0x...',          # Filter by recipient (optional)
    from_block=0,
    to_block=None,
    weeks=2
)
```

**Returns**: List of transfer events:
```python
{
    'from': '0x...',
    'to': '0x...',
    'value': 1000000000000000000,
    'block_number': 12345,
    'transaction_hash': '0x...',
    'log_index': 0
}
```

#### scrape_approvals()

Scrape ERC20 Approval events.

```python
approvals = scraper.scrape_approvals(
    token='market',
    owner='0x...',       # Filter by owner (optional)
    spender='0x...',     # Filter by spender (optional)
    from_block=0,
    to_block=None,
    weeks=2
)
```

#### scrape_registry_events()

Scrape Registry contract events (ModRegistered, ModUpdated, ModRemoved).

```python
registrations = scraper.scrape_registry_events(
    event_type='ModRegistered',  # Event type
    owner='0x...',               # Filter by owner (optional)
    from_block=0,
    to_block=None,
    weeks=4
)
```

#### scrape_custom_event()

Scrape any custom contract event.

```python
events = scraper.scrape_custom_event(
    contract_name='tokengate',
    event_name='TokenWhitelisted',
    from_block=0,
    to_block=None,
    weeks=2,
    filters={'token': '0x...'}  # Filter indexed params
)
```

### Analysis Methods

#### track_balances_from_events()

Calculate balances from Transfer events.

```python
balances = scraper.track_balances_from_events(
    transfers,               # List of transfer events
    verify_onchain=True,     # Verify balances on-chain
    token='market'           # Token symbol
)
```

**Returns**: Dictionary mapping addresses to balances:
```python
{
    '0xaddress1': 100.5,
    '0xaddress2': 50.25,
    ...
}
```

#### get_active_addresses()

Get unique addresses that interacted with a contract.

```python
addresses = scraper.get_active_addresses(
    contract_name='market',
    event_name='Transfer',
    address_field='from',    # Field containing address
    from_block=0,
    to_block=None,
    weeks=2
)
```

#### get_event_timeline()

Organize events into a timeline.

```python
timeline = scraper.get_event_timeline(
    events,
    time_field='block_number',  # Field for grouping
    group_by='from'             # Optional secondary grouping
)
```

**Returns**: Dictionary mapping time points to events

### Export Methods

#### export_events()

Export events to file.

```python
filepath = scraper.export_events(
    events,
    filename='transfers.json',
    format='json'  # 'json' or 'csv'
)
```

## Examples

### Example 1: Track Token Holders

```python
scraper = Scrape(network='testnet')

# Get all transfers
transfers = scraper.scrape_transfers(token='market', weeks=4)

# Calculate balances
balances = scraper.track_balances_from_events(
    transfers,
    verify_onchain=True
)

# Show top holders
sorted_holders = sorted(balances.items(), key=lambda x: x[1], reverse=True)
for addr, balance in sorted_holders[:10]:
    print(f'{addr}: {balance} tokens')
```

### Example 2: Monitor Large Transfers

```python
scraper = Scrape(network='mainnet')

# Get recent transfers
current_block = scraper.w3.eth.block_number
transfers = scraper.scrape_transfers(
    token='market',
    from_block=current_block - 1000,
    to_block=current_block
)

# Filter large transfers
threshold = 1000 * (10 ** 18)  # 1000 tokens
large_transfers = [t for t in transfers if t['value'] > threshold]

for transfer in large_transfers:
    print(f"Large transfer: {transfer['value'] / (10**18)} tokens")
    print(f"  From: {transfer['from']}")
    print(f"  To: {transfer['to']}")
```

### Example 3: Analyze User Activity

```python
scraper = Scrape(network='testnet')

# Get all senders
senders = scraper.get_active_addresses(
    contract_name='market',
    event_name='Transfer',
    address_field='from',
    weeks=2
)

# Get all receivers
receivers = scraper.get_active_addresses(
    contract_name='market',
    event_name='Transfer',
    address_field='to',
    weeks=2
)

print(f'Active senders: {len(senders)}')
print(f'Active receivers: {len(receivers)}')
print(f'Active users: {len(set(senders) | set(receivers))}')
```

### Example 4: Registry Analytics

```python
scraper = Scrape(network='testnet')

# Get all mod registrations
registrations = scraper.scrape_registry_events(
    event_type='ModRegistered',
    weeks=8
)

# Count mods per owner
from collections import Counter
owners = Counter([r['owner'] for r in registrations])

print(f'Total mods: {len(registrations)}')
print(f'Unique owners: {len(owners)}')
print(f'Top contributors: {owners.most_common(5)}')
```

### Example 5: Chase Pattern (Continuous Monitoring)

```python
import time

scraper = Scrape(network='mainnet')

def chase_events(window_blocks=100):
    """Continuously monitor for new events"""
    last_block = scraper.w3.eth.block_number

    while True:
        current_block = scraper.w3.eth.block_number

        if current_block > last_block:
            # Scrape new blocks
            transfers = scraper.scrape_transfers(
                token='market',
                from_block=last_block + 1,
                to_block=current_block
            )

            # Process new transfers
            for transfer in transfers:
                print(f"New transfer: {transfer['value'] / (10**18)} tokens")

            last_block = current_block

        time.sleep(2)  # Wait for new blocks

# Run chase (in production, use proper async/threading)
chase_events()
```

## Integration with Chase Module

The scraper is designed to work seamlessly with "chase" patterns for continuous blockchain monitoring:

```python
# In your chase module
import mod as m

class Chase:
    def __init__(self):
        self.chain = m.mod('chain')(network='mainnet')
        self.scraper = self.chain.scraper()

    def monitor_transfers(self):
        """Monitor for new transfers"""
        current_block = self.scraper.w3.eth.block_number

        transfers = self.scraper.scrape_transfers(
            token='market',
            from_block=current_block - 100,
            to_block=current_block
        )

        return self.process_transfers(transfers)

    def process_transfers(self, transfers):
        """Process and act on transfers"""
        # Your chase logic here
        pass
```

## Advanced Features

### Parallel Multi-Contract Scraping

Scrape multiple contracts simultaneously:

```python
scraper = Scrape(network='testnet', max_workers=5)

configs = [
    {'contract_name': 'market', 'event_name': 'Transfer', 'weeks': 2},
    {'contract_name': 'registry', 'event_name': 'ModRegistered', 'weeks': 4}
]

results = scraper.scrape_multiple_contracts(configs, parallel=True)
```

### Caching

Speed up repeated queries with automatic caching:

```python
scraper = Scrape(network='testnet', enable_cache=True)

# First call scrapes and caches
transfers = scraper.scrape_transfers(token='market', weeks=2)

# Second call loads from cache (instant)
transfers = scraper.scrape_transfers(token='market', weeks=2)

# Clear cache when needed
scraper.clear_cache('market_Transfer*')
```

### Checkpoint/Resume

For long scrapes that might be interrupted:

```python
scraper = Scrape(network='testnet')

# If interrupted, will resume from last checkpoint
transfers = scraper.scrape_transfers(
    token='market',
    weeks=12,
    checkpoint_file='/tmp/my_scrape.json'
)
```

### Parallel Batch Processing

Process batches in parallel for faster scraping:

```python
scraper = Scrape(network='testnet', max_workers=10, rate_limit=0.05)

transfers = scraper.scrape_batch_parallel(
    contract_name='market',
    event_name='Transfer',
    weeks=4
)
```

### Transfer Statistics

Get comprehensive analytics on transfer events:

```python
transfers = scraper.scrape_transfers(token='market', weeks=2)
stats = scraper.get_transfer_stats(transfers, token='market')

print(f"Total volume: {stats['total_volume']}")
print(f"Unique participants: {stats['unique_participants']}")
print(f"Top senders: {stats['top_senders']}")
```

### Time Estimation

Estimate scraping time before starting:

```python
estimate = scraper.estimate_scrape_time(
    from_block=12000000,
    to_block=12100000,
    batch_size=10000
)

print(f"Estimated time: {estimate['estimated_time_parallel']}")
```

## Performance Tips

1. **Batch Size**: Adjust `batch_size` based on RPC provider limits
   - Alchemy: 10000 blocks
   - Infura: 10000 blocks
   - Local node: 100000+ blocks

2. **Parallel Workers**: Increase `max_workers` for faster scraping (5-10 recommended)

3. **Rate Limiting**: Adjust `rate_limit` based on your RPC provider's limits
   - Free tier: 0.2-0.5 seconds
   - Paid tier: 0.05-0.1 seconds

4. **Caching**: Enable caching for repeated queries (saves time and RPC calls)

5. **Verify Balances**: Set `verify_onchain=False` for faster processing when you don't need exact current balances

6. **Parallel Verification**: Set `parallel=True` when verifying balances for many addresses

7. **Filters**: Use indexed parameter filters when possible to reduce data transfer

8. **Checkpoints**: Use checkpoints for very large scrapes (multiple weeks/months)

## Error Handling

The scraper includes automatic retry logic:

```python
try:
    transfers = scraper.scrape_transfers(token='market')
except Exception as e:
    print(f'Error scraping: {e}')
    # Scraper will automatically retry with smaller batches
```

## Troubleshooting

### RPC Errors

If you encounter RPC errors:
1. Reduce `batch_size`
2. Add delays between requests
3. Use a different RPC provider

### Memory Issues

For very large ranges:
1. Process in smaller time windows
2. Use `process_fn` to filter events during scraping
3. Export results incrementally

### Performance Issues

1. Use appropriate `from_block` instead of scanning all history
2. Enable filters for indexed parameters
3. Consider using specialized indexing services (The Graph, etc.) for historical data

## License

Part of the mod chain package.
