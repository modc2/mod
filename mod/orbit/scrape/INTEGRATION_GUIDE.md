# Scrape Module Integration Guide

This guide shows how to integrate the scrape module with your existing code, particularly for "chase" patterns and continuous monitoring.

## Overview

The scrape module has been created at:
```
/Users/broski/mod/mod/core/chain/scrape/
├── __init__.py           # Module exports
├── scrape.py             # Main Scrape class
├── example_usage.py      # Usage examples
├── test_scrape.py        # Test suite
├── README.md             # Full documentation
└── INTEGRATION_GUIDE.md  # This file
```

The `Scrape` class inherits from the main `Mod` (chain) class, giving it access to all chain functionality plus specialized event scraping methods.

## Integration with Chain Module

The scrape functionality is integrated into the main chain module:

```python
import mod as m

# Method 1: Use scraper through chain
chain = m.mod('chain')(network='testnet')
transfers = chain.scrape_transfers(token='market', weeks=2)

# Method 2: Get scraper instance directly
scraper = chain.scraper()
balances = scraper.track_balances_from_events(transfers)
```

## Creating a Chase Module

Here's how to create a chase module that uses the scraper for continuous monitoring:

### Basic Chase Structure

```python
# File: chase.py or wherever your chase module lives

import mod as m
import time
from typing import Dict, List, Any


class Chase:
    """Chase module for continuous blockchain monitoring"""

    def __init__(self, network='testnet', key='test'):
        # Initialize chain with scraper
        self.chain = m.mod('chain')(network=network, key=key)
        self.scraper = self.chain.scraper()
        self.last_processed_block = self.scraper.w3.eth.block_number

    def monitor_transfers(self,
                         token='market',
                         threshold=None,
                         callback=None):
        """Monitor token transfers continuously

        Args:
            token: Token symbol to monitor
            threshold: Minimum transfer value to report
            callback: Function to call for each transfer
        """
        current_block = self.scraper.w3.eth.block_number

        if current_block > self.last_processed_block:
            # Scrape new blocks
            transfers = self.scraper.scrape_transfers(
                token=token,
                from_block=self.last_processed_block + 1,
                to_block=current_block
            )

            # Process transfers
            for transfer in transfers:
                if threshold and transfer['value'] < threshold:
                    continue

                if callback:
                    callback(transfer)
                else:
                    self.process_transfer(transfer)

            self.last_processed_block = current_block

        return current_block

    def process_transfer(self, transfer: Dict[str, Any]):
        """Process a single transfer event"""
        value = transfer['value'] / (10 ** 18)
        m.print(f"Transfer: {value:.2f} tokens", color='green')
        m.print(f"  From: {transfer['from']}")
        m.print(f"  To: {transfer['to']}")
        m.print(f"  Tx: {transfer['transaction_hash']}")

    def monitor_registry(self, callback=None):
        """Monitor registry events"""
        current_block = self.scraper.w3.eth.block_number

        if current_block > self.last_processed_block:
            events = self.scraper.scrape_registry_events(
                event_type='ModRegistered',
                from_block=self.last_processed_block + 1,
                to_block=current_block
            )

            for event in events:
                if callback:
                    callback(event)
                else:
                    self.process_registry_event(event)

            self.last_processed_block = current_block

    def process_registry_event(self, event: Dict[str, Any]):
        """Process a registry event"""
        m.print(f"New mod registered: {event.get('name', 'Unknown')}", color='cyan')

    def chase_loop(self, interval=2):
        """Main chase loop - continuously monitor events

        Args:
            interval: Seconds between checks
        """
        m.print('Starting chase loop...', color='green')

        try:
            while True:
                # Monitor different event types
                self.monitor_transfers()
                self.monitor_registry()

                # Wait for new blocks
                time.sleep(interval)

        except KeyboardInterrupt:
            m.print('\nChase loop stopped', color='yellow')


# Usage
if __name__ == '__main__':
    chase = Chase(network='testnet')
    chase.chase_loop()
```

### Advanced Chase with Multiple Tokens

```python
class MultiTokenChase(Chase):
    """Chase multiple tokens and contracts"""

    def __init__(self, tokens=['market', 'usdc', 'usdt'], **kwargs):
        super().__init__(**kwargs)
        self.tokens = tokens
        self.token_balances = {}

    def monitor_all_tokens(self):
        """Monitor all configured tokens"""
        for token in self.tokens:
            transfers = self.scraper.scrape_transfers(
                token=token,
                from_block=self.last_processed_block + 1,
                to_block=self.scraper.w3.eth.block_number
            )

            if transfers:
                m.print(f"Found {len(transfers)} {token.upper()} transfers", color='cyan')

                # Track balances
                balances = self.scraper.track_balances_from_events(
                    transfers,
                    verify_onchain=False,
                    token=token
                )
                self.token_balances[token] = balances

    def get_holder_analytics(self, token='market'):
        """Get analytics for token holders"""
        transfers = self.scraper.scrape_transfers(
            token=token,
            weeks=4
        )

        # Get unique holders
        holders = set()
        for t in transfers:
            holders.add(t['from'].lower())
            holders.add(t['to'].lower())

        # Remove zero address
        holders.discard('0x0000000000000000000000000000000000000000')

        # Get balances
        balances = self.scraper.track_balances_from_events(
            transfers,
            verify_onchain=True,
            token=token
        )

        return {
            'total_holders': len(holders),
            'holders_with_balance': len(balances),
            'total_transfers': len(transfers),
            'top_holders': sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]
        }


# Usage
chase = MultiTokenChase(tokens=['market', 'usdc'], network='mainnet')
analytics = chase.get_holder_analytics('market')
m.print(f"Total holders: {analytics['total_holders']}")
```

### Event-Driven Chase

```python
class EventDrivenChase(Chase):
    """Event-driven chase with callbacks for different events"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.handlers = {}

    def on(self, event_type, handler):
        """Register event handler

        Args:
            event_type: Event type ('transfer', 'registration', 'approval')
            handler: Callback function
        """
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)

    def emit(self, event_type, data):
        """Emit event to registered handlers"""
        if event_type in self.handlers:
            for handler in self.handlers[event_type]:
                handler(data)

    def chase_loop(self, interval=2):
        """Event-driven chase loop"""
        while True:
            current_block = self.scraper.w3.eth.block_number

            if current_block > self.last_processed_block:
                # Check transfers
                transfers = self.scraper.scrape_transfers(
                    token='market',
                    from_block=self.last_processed_block + 1,
                    to_block=current_block
                )

                for transfer in transfers:
                    self.emit('transfer', transfer)

                # Check registrations
                registrations = self.scraper.scrape_registry_events(
                    event_type='ModRegistered',
                    from_block=self.last_processed_block + 1,
                    to_block=current_block
                )

                for reg in registrations:
                    self.emit('registration', reg)

                self.last_processed_block = current_block

            time.sleep(interval)


# Usage
chase = EventDrivenChase()

# Register handlers
def on_large_transfer(transfer):
    if transfer['value'] > 1000 * (10 ** 18):
        m.print(f"ALERT: Large transfer detected!", color='red')

def on_new_mod(registration):
    m.print(f"New mod: {registration.get('name')}", color='green')

chase.on('transfer', on_large_transfer)
chase.on('registration', on_new_mod)

# Start monitoring
chase.chase_loop()
```

## Reusing Scraper in Existing Code

### In API Routes

```python
# In your API module
from fastapi import APIRouter
import mod as m

router = APIRouter()

@router.get("/analytics/transfers")
async def get_transfers(token: str = 'market', weeks: int = 2):
    """Get token transfer analytics"""
    chain = m.mod('chain')()
    transfers = chain.scrape_transfers(token=token, weeks=weeks)

    return {
        'total_transfers': len(transfers),
        'transfers': transfers[:100]  # Limit response
    }

@router.get("/analytics/holders")
async def get_holders(token: str = 'market', weeks: int = 4):
    """Get token holder analytics"""
    chain = m.mod('chain')()
    scraper = chain.scraper()

    transfers = scraper.scrape_transfers(token=token, weeks=weeks)
    balances = scraper.track_balances_from_events(
        transfers,
        verify_onchain=True,
        token=token
    )

    return {
        'total_holders': len(balances),
        'top_holders': sorted(balances.items(), key=lambda x: x[1], reverse=True)[:50]
    }
```

### In Background Tasks

```python
# In your background job scheduler
import mod as m
from datetime import datetime

def daily_analytics_job():
    """Run daily analytics"""
    chain = m.mod('chain')()
    scraper = chain.scraper()

    # Get yesterday's transfers
    current_block = scraper.w3.eth.block_number
    blocks_per_day = (24 * 60 * 60) // scraper.block_time

    transfers = scraper.scrape_transfers(
        token='market',
        from_block=current_block - blocks_per_day,
        to_block=current_block
    )

    # Save analytics
    analytics = {
        'date': datetime.now().isoformat(),
        'total_transfers': len(transfers),
        'unique_senders': len(set(t['from'] for t in transfers)),
        'unique_receivers': len(set(t['to'] for t in transfers)),
        'total_volume': sum(t['value'] for t in transfers) / (10 ** 18)
    }

    m.save(f'analytics_{datetime.now().date()}.json', analytics)
    return analytics
```

## Testing

Run the test suite:

```bash
cd /Users/broski/mod/mod/core/chain/scrape
python test_scrape.py
```

## Examples

Run the example usage file:

```bash
cd /Users/broski/mod/mod/core/chain/scrape
python example_usage.py
```

## Performance Considerations

1. **Block Range**: Start with small ranges (100-1000 blocks) for testing
2. **Batch Size**: Adjust based on your RPC provider's limits
3. **Caching**: Cache results for repeated queries
4. **Rate Limiting**: Add delays if you hit rate limits

## Common Patterns

### Pattern 1: Snapshot Analysis
```python
# Get current state at a specific time
scraper = Scrape()
transfers = scraper.scrape_transfers(token='market', weeks=4)
current_balances = scraper.track_balances_from_events(transfers, verify_onchain=True)
```

### Pattern 2: Historical Analysis
```python
# Analyze historical trends
scraper = Scrape()
transfers = scraper.scrape_transfers(token='market', weeks=12)
timeline = scraper.get_event_timeline(transfers)
# Analyze timeline for trends
```

### Pattern 3: Real-time Monitoring
```python
# Continuous monitoring (chase pattern)
chase = Chase()
chase.chase_loop()
```

### Pattern 4: Export for Analysis
```python
# Export events for external analysis
scraper = Scrape()
transfers = scraper.scrape_transfers(token='market', weeks=8)
scraper.export_events(transfers, 'transfers.csv', format='csv')
```

## Next Steps

1. Integrate scraper into your chase module
2. Add custom event processing logic
3. Set up monitoring for your specific use case
4. Implement alerting for important events
5. Create dashboards using the analytics

## Support

For issues or questions:
- Check the README.md for detailed API documentation
- Review example_usage.py for code examples
- Run test_scrape.py to verify installation
