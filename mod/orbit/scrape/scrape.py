"""Event Scraper Module for Chain Interactions

This module provides event scraping functionality for Ethereum/Base contracts.
Inherits from the Mod chain class to reuse contract loading and interaction logic.

Features:
- Batch event scraping with automatic retry logic
- Support for multiple event types (Transfer, Approval, custom events)
- Efficient address tracking and balance calculation
- Configurable time ranges and block ranges
- Integration with mod library for logging and utilities
"""

from web3 import Web3
from typing import Dict, Any, Optional, List, Union, Tuple
import mod as m
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
# Import Chain Mod class
class Scrape(m.mod('chain')):
    """Event Scraper that inherits from Chain Mod.

    Provides comprehensive event scraping functionality for blockchain events,
    with built-in batching, retry logic, and efficient processing.
    """

    def __init__(self, network: str = 'testnet', key='test', **kwargs):
        """Initialize scraper with chain connection.

        Args:
            network: Network to connect to ('testnet', 'mainnet', 'ganache')
            key: Private key identifier
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(network=network, key=key, **kwargs)
        self.batch_size = kwargs.get('batch_size', 10000)
        self.block_time = kwargs.get('block_time', 2)  # Base chain default
        self.rate_limit = kwargs.get('rate_limit', 0.1)  # seconds between requests
        self.max_workers = kwargs.get('max_workers', 5)  # parallel workers
        self.cache_dir = kwargs.get('cache_dir', Path.home() / '.mod' / 'scrape_cache')
        self.enable_cache = kwargs.get('enable_cache', True)
        self.retry_delay = kwargs.get('retry_delay', 1.0)  # initial retry delay
        self.max_retries = kwargs.get('max_retries', 3)

        # Create cache directory if needed
        if self.enable_cache:
            self.cache_dir = Path(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        m.print(f'Scraper initialized for {network}', color='green')

    def _get_cache_key(self, contract_name: str, event_name: str,
                      from_block: int, to_block: int, filters: Dict = None) -> str:
        """Generate cache key for event scraping."""
        filter_str = json.dumps(filters, sort_keys=True) if filters else 'none'
        return f"{contract_name}_{event_name}_{from_block}_{to_block}_{filter_str}"

    def _load_from_cache(self, cache_key: str) -> Optional[List[Dict[str, Any]]]:
        """Load events from cache if available."""
        if not self.enable_cache:
            return None

        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                m.print(f'Loaded {len(data)} events from cache', color='cyan')
                return data
            except Exception as e:
                m.print(f'Cache read error: {e}', color='yellow')
        return None

    def _save_to_cache(self, cache_key: str, events: List[Dict[str, Any]]):
        """Save events to cache."""
        if not self.enable_cache:
            return

        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(events, f)
            m.print(f'Cached {len(events)} events', color='cyan')
        except Exception as e:
            m.print(f'Cache write error: {e}', color='yellow')

    def clear_cache(self, pattern: str = '*'):
        """Clear cache files matching pattern.

        Args:
            pattern: Glob pattern for cache files to delete (default: all)
        """
        if not self.cache_dir.exists():
            return

        deleted = 0
        for cache_file in self.cache_dir.glob(f"{pattern}.json"):
            cache_file.unlink()
            deleted += 1

        m.print(f'Cleared {deleted} cache files', color='green')

    def get_event_filter(self,
                        contract_name: str,
                        event_name: str,
                        from_block: int,
                        to_block: int,
                        filters: Dict[str, Any] = None) -> Any:
        """Create an event filter for a contract.

        Args:
            contract_name: Name of contract (e.g., 'market', 'registry')
            event_name: Name of event (e.g., 'Transfer', 'ModRegistered')
            from_block: Starting block number
            to_block: Ending block number
            filters: Optional event filters (indexed parameters)

        Returns:
            Event filter object
        """
        contract = self.contracts.get(contract_name.lower())
        if not contract:
            raise ValueError(f'Contract {contract_name} not loaded')

        event = getattr(contract.events, event_name)
        filter_params = {
            'from_block': from_block,
            'to_block': to_block
        }

        if filters:
            filter_params['argument_filters'] = filters

        return event.create_filter(**filter_params)

    def scrape_events(self,
                     contract_name: str,
                     event_name: str,
                     from_block: int = 0,
                     to_block: int = None,
                     weeks: int = 2,
                     batch_size: int = None,
                     filters: Dict[str, Any] = None,
                     process_fn: callable = None,
                     use_cache: bool = True,
                     checkpoint_file: str = None) -> List[Dict[str, Any]]:
        """Scrape events from a contract with automatic batching.

        Args:
            contract_name: Name of contract to scrape
            event_name: Name of event to scrape
            from_block: Starting block (0 = calculated from weeks)
            to_block: Ending block (None = latest)
            weeks: Number of weeks to look back if from_block=0
            batch_size: Blocks per batch (None = use default)
            filters: Optional event filters for indexed parameters
            process_fn: Optional function to process each event
            use_cache: Whether to use caching (default True)
            checkpoint_file: Optional file to save/resume progress

        Returns:
            List of processed events
        """
        batch_size = batch_size or self.batch_size

        # Calculate block range
        if to_block is None:
            to_block = self.w3.eth.block_number

        if from_block == 0:
            seconds_in_period = weeks * 7 * 24 * 60 * 60
            blocks_in_period = seconds_in_period // self.block_time
            from_block = max(0, to_block - blocks_in_period)

        # Check cache first
        if use_cache:
            cache_key = self._get_cache_key(contract_name, event_name, from_block, to_block, filters)
            cached_events = self._load_from_cache(cache_key)
            if cached_events is not None:
                return cached_events

        # Load checkpoint if exists
        if checkpoint_file:
            checkpoint = self._load_checkpoint(checkpoint_file)
            if checkpoint:
                from_block = checkpoint.get('last_block', from_block)
                all_events = checkpoint.get('events', [])
                m.print(f'Resuming from block {from_block:,}', color='cyan')
            else:
                all_events = []
        else:
            all_events = []

        total_blocks = to_block - from_block
        m.print(f'Scraping {event_name} events from {contract_name}', color='cyan')
        m.print(f'Block range: {from_block:,} to {to_block:,} ({total_blocks:,} blocks)', color='cyan')

        # Scrape in batches
        current_block = from_block
        batches_completed = 0
        total_batches = (total_blocks + batch_size - 1) // batch_size

        while current_block <= to_block:
            batch_end = min(current_block + batch_size - 1, to_block)
            retry_count = 0
            success = False

            while retry_count < self.max_retries and not success:
                try:
                    progress = (batches_completed / total_batches) * 100 if total_batches > 0 else 0
                    m.print(f'[{progress:.1f}%] Fetching blocks {current_block:,} to {batch_end:,}...', color='yellow')

                    event_filter = self.get_event_filter(
                        contract_name,
                        event_name,
                        current_block,
                        batch_end,
                        filters
                    )

                    events = event_filter.get_all_entries()

                    # Process events if function provided
                    if process_fn:
                        events = [process_fn(e) for e in events]

                    all_events.extend(events)
                    m.print(f'  Found {len(events)} events', color='green')

                    # Rate limiting
                    if self.rate_limit > 0:
                        time.sleep(self.rate_limit)

                    success = True

                except Exception as e:
                    retry_count += 1
                    if retry_count < self.max_retries:
                        delay = self.retry_delay * (2 ** retry_count)  # Exponential backoff
                        m.print(f'Error: {e}. Retrying in {delay:.1f}s... (attempt {retry_count}/{self.max_retries})', color='yellow')
                        time.sleep(delay)
                    else:
                        m.print(f'Error fetching events: {e}', color='red')
                        # Retry with smaller batch size
                        if batch_size > 1000:
                            m.print('Retrying with smaller batch size...', color='yellow')
                            return self.scrape_events(
                                contract_name,
                                event_name,
                                current_block,
                                to_block,
                                weeks,
                                batch_size=batch_size // 2,
                                filters=filters,
                                process_fn=process_fn,
                                use_cache=use_cache,
                                checkpoint_file=checkpoint_file
                            )
                        raise

            # Save checkpoint
            if checkpoint_file:
                self._save_checkpoint(checkpoint_file, current_block, all_events)

            current_block = batch_end + 1
            batches_completed += 1

        m.print(f'Total events found: {len(all_events):,}', color='green')

        # Save to cache
        if use_cache:
            cache_key = self._get_cache_key(contract_name, event_name, from_block, to_block, filters)
            self._save_to_cache(cache_key, all_events)

        # Clean up checkpoint
        if checkpoint_file:
            self._delete_checkpoint(checkpoint_file)

        return all_events

    def scrape_transfers(self,
                        token: str = 'market',
                        from_address: str = None,
                        to_address: str = None,
                        from_block: int = 0,
                        to_block: int = None,
                        days: int = 1) -> List[Dict[str, Any]]:
        """Scrape Transfer events for a token.

        Args:
            token: Token symbol (e.g., 'market', 'usdc')
            from_address: Filter by sender address
            to_address: Filter by recipient address
            from_block: Starting block
            to_block: Ending block
            weeks: Number of weeks to look back

        Returns:
            List of transfer events with decoded data
        """
        filters = {}
        if from_address:
            filters['from'] = Web3.to_checksum_address(from_address)
        if to_address:
            filters['to'] = Web3.to_checksum_address(to_address)

        def process_transfer(event):
            return {
                'from': event['args']['from'],
                'to': event['args']['to'],
                'value': event['args']['value'],
                'block_number': event['blockNumber'],
                'transaction_hash': event['transactionHash'].hex(),
                'log_index': event['logIndex']
            }

        return self.scrape_events(
            token.lower(),
            'Transfer',
            from_block=from_block,
            to_block=to_block,
            weeks=weeks,
            filters=filters if filters else None,
            process_fn=process_transfer
        )

    def scrape_approvals(self,
                        token: str = 'market',
                        owner: str = None,
                        spender: str = None,
                        from_block: int = 0,
                        to_block: int = None,
                        weeks: int = 2) -> List[Dict[str, Any]]:
        """Scrape Approval events for a token.

        Args:
            token: Token symbol
            owner: Filter by token owner
            spender: Filter by approved spender
            from_block: Starting block
            to_block: Ending block
            weeks: Number of weeks to look back

        Returns:
            List of approval events
        """
        filters = {}
        if owner:
            filters['owner'] = Web3.to_checksum_address(owner)
        if spender:
            filters['spender'] = Web3.to_checksum_address(spender)

        def process_approval(event):
            return {
                'owner': event['args']['owner'],
                'spender': event['args']['spender'],
                'value': event['args']['value'],
                'block_number': event['blockNumber'],
                'transaction_hash': event['transactionHash'].hex()
            }

        return self.scrape_events(
            token.lower(),
            'Approval',
            from_block=from_block,
            to_block=to_block,
            weeks=weeks,
            filters=filters if filters else None,
            process_fn=process_approval
        )

    def scrape_registry_events(self,
                               event_type: str = 'ModRegistered',
                               owner: str = None,
                               from_block: int = 0,
                               to_block: int = None,
                               weeks: int = 4) -> List[Dict[str, Any]]:
        """Scrape Registry events (ModRegistered, ModUpdated, ModRemoved).

        Args:
            event_type: Type of registry event
            owner: Filter by mod owner
            from_block: Starting block
            to_block: Ending block
            weeks: Number of weeks to look back

        Returns:
            List of registry events
        """
        filters = {}
        if owner:
            filters['owner'] = Web3.to_checksum_address(owner)

        def process_registry_event(event):
            args = event['args']
            result = {
                'block_number': event['blockNumber'],
                'transaction_hash': event['transactionHash'].hex(),
                'log_index': event['logIndex']
            }
            # Add all event arguments
            result.update(dict(args))
            return result

        return self.scrape_events(
            'registry',
            event_type,
            from_block=from_block,
            to_block=to_block,
            weeks=weeks,
            filters=filters if filters else None,
            process_fn=process_registry_event
        )

    def track_balances_from_events(self,
                                   transfers: List[Dict[str, Any]],
                                   verify_onchain: bool = True,
                                   token: str = 'market',
                                   parallel: bool = True) -> Dict[str, float]:
        """Track balances from transfer events with optimized processing.

        Args:
            transfers: List of transfer events from scrape_transfers
            verify_onchain: Whether to verify balances on-chain
            token: Token symbol for verification
            parallel: Whether to verify balances in parallel (default True)

        Returns:
            Dictionary mapping addresses to balances
        """
        zero_address = '0x0000000000000000000000000000000000000000'

        m.print(f'Processing {len(transfers):,} transfers...', color='cyan')

        # Sort transfers by block number for correct ordering
        sorted_transfers = sorted(transfers, key=lambda x: (x.get('block_number', 0), x.get('log_index', 0)))

        # Use defaultdict for cleaner code
        from collections import defaultdict
        balances = defaultdict(int)

        for transfer in sorted_transfers:
            from_addr = transfer['from'].lower()
            to_addr = transfer['to'].lower()
            value = transfer['value']

            # Subtract from sender (unless mint)
            if from_addr != zero_address.lower():
                balances[from_addr] -= value

            # Add to receiver (unless burn)
            if to_addr != zero_address.lower():
                balances[to_addr] += value

        # Convert back to regular dict and filter positive balances
        balances = {addr: bal for addr, bal in balances.items() if bal > 0}

        if verify_onchain:
            m.print(f'Verifying balances on-chain for {len(balances):,} addresses...', color='yellow')

            token_cfg = self.contracts_config().get(token.lower())
            if not token_cfg:
                m.print(f'Token {token} not found, skipping verification', color='red')
                return {addr: self.format_balance(bal, token=token.upper()) for addr, bal in balances.items()}

            token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_cfg['address']),
                abi=self.ipfs.get(token_cfg['abi'])
            )

            if parallel and len(balances) > 10:
                # Parallel verification for large address sets
                verified_balances = self._verify_balances_parallel(
                    list(balances.keys()),
                    token_contract,
                    token
                )
            else:
                # Sequential verification for small sets
                verified_balances = {}
                for i, addr in enumerate(balances.keys()):
                    if (i + 1) % 100 == 0:
                        m.print(f'  Progress: {i + 1}/{len(balances)}', color='cyan')

                    try:
                        balance = token_contract.functions.balanceOf(
                            Web3.to_checksum_address(addr)
                        ).call()

                        if balance > 0:
                            verified_balances[addr] = self.format_balance(balance, token=token.upper())
                    except Exception as e:
                        m.print(f'Error verifying balance for {addr}: {e}', color='red')
                        continue

            m.print(f'Found {len(verified_balances):,} addresses with non-zero balances', color='green')
            return verified_balances

        # Format balances without verification
        return {
            addr: self.format_balance(bal, token=token.upper())
            for addr, bal in balances.items()
        }

    def _verify_balances_parallel(self,
                                  addresses: List[str],
                                  token_contract: Any,
                                  token: str) -> Dict[str, float]:
        """Verify balances in parallel using thread pool.

        Args:
            addresses: List of addresses to verify
            token_contract: Web3 contract instance
            token: Token symbol

        Returns:
            Dictionary mapping addresses to verified balances
        """
        def verify_single(addr: str) -> Tuple[str, Optional[float]]:
            """Verify balance for a single address."""
            try:
                balance = token_contract.functions.balanceOf(
                    Web3.to_checksum_address(addr)
                ).call()

                if balance > 0:
                    return (addr, self.format_balance(balance, token=token.upper()))
                return (addr, None)
            except Exception as e:
                m.print(f'Error verifying {addr}: {e}', color='red')
                return (addr, None)

        verified_balances = {}
        total = len(addresses)
        completed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_addr = {executor.submit(verify_single, addr): addr for addr in addresses}

            for future in as_completed(future_to_addr):
                addr, balance = future.result()
                if balance is not None:
                    verified_balances[addr] = balance

                completed += 1
                if completed % 100 == 0:
                    progress = (completed / total) * 100
                    m.print(f'  Progress: {completed}/{total} ({progress:.1f}%)', color='cyan')

        return verified_balances

    def get_active_addresses(self,
                            contract_name: str,
                            event_name: str,
                            address_field: str = 'from',
                            from_block: int = 0,
                            to_block: int = None,
                            weeks: int = 2) -> List[str]:
        """Get list of unique active addresses from events.

        Args:
            contract_name: Contract to scrape
            event_name: Event to scrape
            address_field: Field containing the address
            from_block: Starting block
            to_block: Ending block
            weeks: Number of weeks to look back

        Returns:
            List of unique addresses
        """
        events = self.scrape_events(
            contract_name,
            event_name,
            from_block=from_block,
            to_block=to_block,
            weeks=weeks
        )

        addresses = set()
        for event in events:
            if address_field in event['args']:
                addr = event['args'][address_field]
                addresses.add(addr.lower())

        m.print(f'Found {len(addresses):,} unique addresses', color='green')
        return list(addresses)

    def scrape_custom_event(self,
                           contract_name: str,
                           event_name: str,
                           from_block: int = 0,
                           to_block: int = None,
                           weeks: int = 2,
                           filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Scrape any custom contract event.

        Args:
            contract_name: Contract name
            event_name: Event name
            from_block: Starting block
            to_block: Ending block
            weeks: Number of weeks to look back
            filters: Optional indexed parameter filters

        Returns:
            List of events with all arguments decoded
        """
        def process_event(event):
            result = {
                'block_number': event['blockNumber'],
                'transaction_hash': event['transactionHash'].hex(),
                'log_index': event['logIndex'],
                'address': event['address']
            }
            # Add all event arguments
            result.update(dict(event['args']))
            return result

        return self.scrape_events(
            contract_name,
            event_name,
            from_block=from_block,
            to_block=to_block,
            weeks=weeks,
            filters=filters,
            process_fn=process_event
        )

    def get_event_timeline(self,
                          events: List[Dict[str, Any]],
                          time_field: str = 'block_number',
                          group_by: str = None) -> Dict[int, List[Dict[str, Any]]]:
        """Organize events into a timeline.

        Args:
            events: List of events
            time_field: Field to use for timeline (default 'block_number')
            group_by: Optional field to group events by

        Returns:
            Dictionary mapping time points to events
        """
        timeline = {}

        for event in events:
            time_point = event.get(time_field)
            if time_point not in timeline:
                timeline[time_point] = []
            timeline[time_point].append(event)

        if group_by:
            grouped_timeline = {}
            for time_point, evts in timeline.items():
                grouped = {}
                for evt in evts:
                    key = evt.get(group_by, 'unknown')
                    if key not in grouped:
                        grouped[key] = []
                    grouped[key].append(evt)
                grouped_timeline[time_point] = grouped
            return grouped_timeline

        return timeline

    def _load_checkpoint(self, checkpoint_file: str) -> Optional[Dict]:
        """Load scraping checkpoint."""
        checkpoint_path = Path(checkpoint_file)
        if checkpoint_path.exists():
            try:
                with open(checkpoint_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                m.print(f'Checkpoint read error: {e}', color='yellow')
        return None

    def _save_checkpoint(self, checkpoint_file: str, last_block: int, events: List):
        """Save scraping checkpoint."""
        checkpoint_path = Path(checkpoint_file)
        try:
            with open(checkpoint_path, 'w') as f:
                json.dump({'last_block': last_block, 'events': events}, f)
        except Exception as e:
            m.print(f'Checkpoint write error: {e}', color='yellow')

    def _delete_checkpoint(self, checkpoint_file: str):
        """Delete checkpoint file."""
        checkpoint_path = Path(checkpoint_file)
        if checkpoint_path.exists():
            checkpoint_path.unlink()

    def scrape_multiple_contracts(self,
                                  scrape_configs: List[Dict[str, Any]],
                                  parallel: bool = True) -> Dict[str, List[Dict[str, Any]]]:
        """Scrape events from multiple contracts in parallel.

        Args:
            scrape_configs: List of dicts with keys: contract_name, event_name, and optional params
            parallel: Whether to scrape in parallel (default True)

        Returns:
            Dictionary mapping config keys to event lists

        Example:
            configs = [
                {'contract_name': 'market', 'event_name': 'Transfer', 'weeks': 2},
                {'contract_name': 'registry', 'event_name': 'ModRegistered', 'weeks': 4}
            ]
            results = scraper.scrape_multiple_contracts(configs)
        """
        results = {}

        if not parallel:
            for i, config in enumerate(scrape_configs):
                key = f"{config['contract_name']}_{config['event_name']}"
                m.print(f'Scraping {i+1}/{len(scrape_configs)}: {key}', color='cyan')
                results[key] = self.scrape_events(**config)
            return results

        # Parallel scraping
        m.print(f'Scraping {len(scrape_configs)} contracts in parallel...', color='cyan')

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_config = {}
            for config in scrape_configs:
                future = executor.submit(self.scrape_events, **config)
                key = f"{config['contract_name']}_{config['event_name']}"
                future_to_config[future] = key

            for future in as_completed(future_to_config):
                key = future_to_config[future]
                try:
                    results[key] = future.result()
                    m.print(f'Completed: {key}', color='green')
                except Exception as e:
                    m.print(f'Failed {key}: {e}', color='red')
                    results[key] = []

        return results

    def scrape_batch_parallel(self,
                             contract_name: str,
                             event_name: str,
                             from_block: int = 0,
                             to_block: int = None,
                             weeks: int = 2,
                             batch_size: int = None,
                             filters: Dict[str, Any] = None,
                             process_fn: callable = None) -> List[Dict[str, Any]]:
        """Scrape events with parallel batch processing.

        Args:
            Same as scrape_events

        Returns:
            List of processed events
        """
        batch_size = batch_size or self.batch_size

        # Calculate block range
        if to_block is None:
            to_block = self.w3.eth.block_number

        if from_block == 0:
            seconds_in_period = weeks * 7 * 24 * 60 * 60
            blocks_in_period = seconds_in_period // self.block_time
            from_block = max(0, to_block - blocks_in_period)

        total_blocks = to_block - from_block
        m.print(f'Parallel scraping {event_name} from {contract_name}', color='cyan')
        m.print(f'Block range: {from_block:,} to {to_block:,} ({total_blocks:,} blocks)', color='cyan')

        # Create batch ranges
        batches = []
        current = from_block
        while current <= to_block:
            batch_end = min(current + batch_size - 1, to_block)
            batches.append((current, batch_end))
            current = batch_end + 1

        m.print(f'Processing {len(batches)} batches in parallel...', color='yellow')

        def scrape_batch(start, end):
            """Helper to scrape a single batch."""
            try:
                event_filter = self.get_event_filter(
                    contract_name,
                    event_name,
                    start,
                    end,
                    filters
                )
                events = event_filter.get_all_entries()

                if process_fn:
                    events = [process_fn(e) for e in events]

                if self.rate_limit > 0:
                    time.sleep(self.rate_limit)

                return events
            except Exception as e:
                m.print(f'Error in batch {start}-{end}: {e}', color='red')
                return []

        # Process batches in parallel
        all_events = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_batch = {
                executor.submit(scrape_batch, start, end): (start, end)
                for start, end in batches
            }

            completed = 0
            for future in as_completed(future_to_batch):
                start, end = future_to_batch[future]
                events = future.result()
                all_events.extend(events)
                completed += 1
                progress = (completed / len(batches)) * 100
                m.print(f'[{progress:.1f}%] Batch {start:,}-{end:,}: {len(events)} events', color='green')

        m.print(f'Total events found: {len(all_events):,}', color='green')
        return all_events

    def export_events(self,
                     events: List[Dict[str, Any]],
                     filename: str,
                     format: str = 'json') -> str:
        """Export events to file.

        Args:
            events: List of events to export
            filename: Output filename
            format: Export format ('json', 'csv')

        Returns:
            Path to exported file
        """
        if format == 'json':
            m.save(filename, events)
            m.print(f'Exported {len(events):,} events to {filename}', color='green')
            return filename

        elif format == 'csv':
            import csv

            if not events:
                m.print('No events to export', color='yellow')
                return None

            keys = events[0].keys()
            with open(filename, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(events)

            m.print(f'Exported {len(events):,} events to {filename}', color='green')
            return filename

        else:
            raise ValueError(f'Unsupported format: {format}')

    def get_transfer_stats(self,
                          transfers: List[Dict[str, Any]],
                          token: str = 'market') -> Dict[str, Any]:
        """Get statistics from transfer events.

        Args:
            transfers: List of transfer events
            token: Token symbol for formatting

        Returns:
            Dictionary with transfer statistics
        """
        if not transfers:
            return {}

        from collections import defaultdict

        total_volume = 0
        unique_senders = set()
        unique_receivers = set()
        transfer_counts = defaultdict(int)
        volume_by_address = defaultdict(int)

        for transfer in transfers:
            from_addr = transfer['from'].lower()
            to_addr = transfer['to'].lower()
            value = transfer['value']

            total_volume += value
            unique_senders.add(from_addr)
            unique_receivers.add(to_addr)
            transfer_counts[from_addr] += 1
            volume_by_address[from_addr] += value

        # Find top senders by volume
        top_senders = sorted(volume_by_address.items(), key=lambda x: x[1], reverse=True)[:10]

        stats = {
            'total_transfers': len(transfers),
            'total_volume': self.format_balance(total_volume, token=token.upper()),
            'total_volume_raw': total_volume,
            'unique_senders': len(unique_senders),
            'unique_receivers': len(unique_receivers),
            'unique_participants': len(unique_senders | unique_receivers),
            'avg_transfer_size': self.format_balance(total_volume // len(transfers), token=token.upper()),
            'top_senders': [(addr, self.format_balance(vol, token=token.upper())) for addr, vol in top_senders]
        }

        return stats

    def scrape_with_retry_async(self,
                                contract_name: str,
                                event_name: str,
                                from_block: int = 0,
                                to_block: int = None,
                                **kwargs) -> List[Dict[str, Any]]:
        """Async wrapper for scraping with better error handling.

        This is a convenience method that wraps scrape_events with
        additional retry logic and error handling.

        Args:
            contract_name: Contract to scrape
            event_name: Event to scrape
            from_block: Starting block
            to_block: Ending block
            **kwargs: Additional arguments for scrape_events

        Returns:
            List of events
        """
        max_attempts = kwargs.pop('max_attempts', 3)
        initial_batch = kwargs.get('batch_size', self.batch_size)

        for attempt in range(max_attempts):
            try:
                return self.scrape_events(
                    contract_name=contract_name,
                    event_name=event_name,
                    from_block=from_block,
                    to_block=to_block,
                    **kwargs
                )
            except Exception as e:
                if attempt < max_attempts - 1:
                    # Reduce batch size for next attempt
                    kwargs['batch_size'] = max(1000, (kwargs.get('batch_size', initial_batch) // 2))
                    m.print(f'Attempt {attempt + 1} failed: {e}', color='yellow')
                    m.print(f'Retrying with batch_size={kwargs["batch_size"]}', color='yellow')
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    m.print(f'All attempts failed: {e}', color='red')
                    raise

    def estimate_scrape_time(self,
                            from_block: int,
                            to_block: int = None,
                            batch_size: int = None) -> Dict[str, Any]:
        """Estimate time to scrape a block range.

        Args:
            from_block: Starting block
            to_block: Ending block (None = current)
            batch_size: Batch size to use

        Returns:
            Dictionary with time estimates
        """
        if to_block is None:
            to_block = self.w3.eth.block_number

        batch_size = batch_size or self.batch_size
        total_blocks = to_block - from_block
        num_batches = (total_blocks + batch_size - 1) // batch_size

        # Estimate time per batch (including rate limit and processing)
        time_per_batch = self.rate_limit + 0.5  # 0.5s for processing

        # Sequential estimate
        sequential_time = num_batches * time_per_batch

        # Parallel estimate (with max_workers)
        parallel_time = (num_batches / self.max_workers) * time_per_batch

        return {
            'total_blocks': total_blocks,
            'num_batches': num_batches,
            'batch_size': batch_size,
            'estimated_time_sequential': f'{sequential_time:.1f}s ({sequential_time/60:.1f}m)',
            'estimated_time_parallel': f'{parallel_time:.1f}s ({parallel_time/60:.1f}m)',
            'max_workers': self.max_workers
        }
