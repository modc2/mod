"""
Meter - Usage metering and billing for mod servers.

Tracks per-user compute usage (request count, CPU time, errors) and
calculates bills with a configurable profit margin.

Usage:
    meter = m.mod('meter')()

    # Record usage (called automatically by gate when integrated)
    meter.record(user='0xabc...', fn='api/info', duration=0.5, status='success')

    # Get usage for a user
    meter.usage(user='0xabc...')

    # Get billing summary
    meter.bill(user='0xabc...', margin=0.2)

    # Get all users' usage
    meter.usage_all()

    # Set cost per compute-second
    meter.set_rate(rate=0.001)  # $0.001 per compute-second

Integration with Gate:
    The gate can call meter.record() after every request to automatically
    track usage. See gate.py for integration.
"""

from typing import *
import threading
import time
import mod as m

print = m.print


class Meter:

    def __init__(self, path='~/.mod/server/meter'):
        self.store = m.mod('store')(path)
        self._lock = threading.Lock()
        # Default rate: cost per compute-second (in whatever unit you want - USD, ETH, credits)
        self._config = self.store.get('config', {
            'rate_per_second': 0.001,    # cost per CPU-second
            'rate_per_request': 0.0001,  # cost per request
            'margin': 0.0,              # profit margin (0.0 = at cost, 0.2 = 20% markup)
            'currency': 'USD',
        })

    # ---- Recording ----

    def record(self, user: str, fn: str, duration: float = 0.0,
               status: str = 'success', params_size: int = 0,
               result_size: int = 0, server: str = '', **kwargs) -> dict:
        """Record a single request's usage.

        Args:
            user: User key/address
            fn: Function called
            duration: Wall-clock time in seconds
            status: 'success' or 'error'
            params_size: Size of request params in bytes
            result_size: Size of response in bytes
            server: Which server handled it
        """
        user = user.lower()
        ts = time.time()

        with self._lock:
            # Update per-user totals
            totals = self.store.get(f'users/{user}/totals', {
                'requests': 0,
                'errors': 0,
                'total_duration': 0.0,
                'total_params_bytes': 0,
                'total_result_bytes': 0,
                'first_seen': ts,
                'last_seen': ts,
            })
            totals['requests'] += 1
            if status != 'success':
                totals['errors'] += 1
            totals['total_duration'] += duration
            totals['total_params_bytes'] += params_size
            totals['total_result_bytes'] += result_size
            totals['last_seen'] = ts
            self.store.put(f'users/{user}/totals', totals)

            # Update per-function breakdown
            fn_key = fn.replace('/', '_')
            fn_stats = self.store.get(f'users/{user}/fns/{fn_key}', {
                'requests': 0,
                'errors': 0,
                'total_duration': 0.0,
            })
            fn_stats['fn'] = fn
            fn_stats['requests'] += 1
            if status != 'success':
                fn_stats['errors'] += 1
            fn_stats['total_duration'] += duration
            self.store.put(f'users/{user}/fns/{fn_key}', fn_stats)

            # Update global server stats
            server_key = server or 'default'
            server_stats = self.store.get(f'servers/{server_key}', {
                'requests': 0,
                'total_duration': 0.0,
            })
            server_stats['requests'] += 1
            server_stats['total_duration'] += duration
            self.store.put(f'servers/{server_key}', server_stats)

            # Append to recent log (keep last 1000)
            log = self.store.get('recent_log', [])
            log.append({
                'user': user,
                'fn': fn,
                'duration': round(duration, 4),
                'status': status,
                'time': ts,
                'server': server,
            })
            if len(log) > 1000:
                log = log[-1000:]
            self.store.put('recent_log', log)

        return {'recorded': True}

    # ---- Querying ----

    def usage(self, user: str) -> dict:
        """Get usage summary for a single user."""
        user = user.lower()
        totals = self.store.get(f'users/{user}/totals', None)
        if not totals:
            return {'user': user, 'error': 'no usage data'}

        config = self.config()
        cost = self._calculate_cost(totals, config)

        return {
            'user': user,
            'requests': totals['requests'],
            'errors': totals['errors'],
            'total_duration': round(totals['total_duration'], 2),
            'total_params_bytes': totals['total_params_bytes'],
            'total_result_bytes': totals['total_result_bytes'],
            'first_seen': totals['first_seen'],
            'last_seen': totals['last_seen'],
            'cost': cost,
        }

    def usage_all(self) -> list:
        """Get usage summaries for all users."""
        try:
            users_dir = self.store.get_path('users')
            import os
            if not os.path.exists(users_dir):
                return []
            user_dirs = [d for d in os.listdir(users_dir) if os.path.isdir(os.path.join(users_dir, d))]
            return [self.usage(u) for u in user_dirs]
        except Exception:
            return []

    def usage_by_fn(self, user: str) -> dict:
        """Get per-function usage breakdown for a user."""
        user = user.lower()
        try:
            fns_dir = self.store.get_path(f'users/{user}/fns')
            import os
            if not os.path.exists(fns_dir):
                return {}
            result = {}
            for f in os.listdir(fns_dir):
                fn_stats = self.store.get(f'users/{user}/fns/{f.replace(".json", "")}', {})
                fn_name = fn_stats.get('fn', f.replace('.json', ''))
                result[fn_name] = fn_stats
            return result
        except Exception:
            return {}

    def recent(self, n: int = 50, user: str = None) -> list:
        """Get recent request log entries."""
        log = self.store.get('recent_log', [])
        if user:
            log = [e for e in log if e.get('user', '').lower() == user.lower()]
        return log[-n:]

    def server_stats(self, server: str = None) -> dict:
        """Get per-server usage stats."""
        if server:
            return self.store.get(f'servers/{server}', {})
        try:
            servers_dir = self.store.get_path('servers')
            import os
            if not os.path.exists(servers_dir):
                return {}
            result = {}
            for f in os.listdir(servers_dir):
                name = f.replace('.json', '')
                result[name] = self.store.get(f'servers/{name}', {})
            return result
        except Exception:
            return {}

    # ---- Billing ----

    def bill(self, user: str, margin: float = None) -> dict:
        """Calculate the bill for a user.

        Args:
            user: User key/address
            margin: Override profit margin (0.0 = at cost, 0.2 = 20% markup)

        Returns:
            Dict with cost breakdown and total bill
        """
        user_usage = self.usage(user)
        if 'error' in user_usage:
            return user_usage

        config = self.config()
        margin = margin if margin is not None else config.get('margin', 0.0)

        base_cost = user_usage['cost']
        margin_amount = base_cost * margin
        total = base_cost + margin_amount

        return {
            'user': user,
            'requests': user_usage['requests'],
            'compute_seconds': user_usage['total_duration'],
            'base_cost': round(base_cost, 6),
            'margin': margin,
            'margin_amount': round(margin_amount, 6),
            'total': round(total, 6),
            'currency': config.get('currency', 'USD'),
        }

    def bill_all(self, margin: float = None) -> list:
        """Calculate bills for all users."""
        all_usage = self.usage_all()
        return [self.bill(u['user'], margin=margin) for u in all_usage if 'error' not in u]

    # ---- Configuration ----

    def config(self) -> dict:
        """Get current meter configuration."""
        return self.store.get('config', self._config)

    def set_rate(self, rate_per_second: float = None, rate_per_request: float = None) -> dict:
        """Set the cost rates."""
        config = self.config()
        if rate_per_second is not None:
            config['rate_per_second'] = rate_per_second
        if rate_per_request is not None:
            config['rate_per_request'] = rate_per_request
        self.store.put('config', config)
        self._config = config
        return config

    def set_margin(self, margin: float) -> dict:
        """Set the profit margin (0.0 = at cost, 0.2 = 20% markup)."""
        config = self.config()
        config['margin'] = margin
        self.store.put('config', config)
        self._config = config
        return config

    def set_currency(self, currency: str) -> dict:
        """Set the billing currency label."""
        config = self.config()
        config['currency'] = currency
        self.store.put('config', config)
        self._config = config
        return config

    def _calculate_cost(self, totals: dict, config: dict) -> float:
        """Calculate raw cost (before margin) from usage totals."""
        duration_cost = totals.get('total_duration', 0) * config.get('rate_per_second', 0.001)
        request_cost = totals.get('requests', 0) * config.get('rate_per_request', 0.0001)
        return round(duration_cost + request_cost, 6)

    # ---- Reset ----

    def reset_user(self, user: str) -> dict:
        """Reset usage data for a specific user."""
        user = user.lower()
        try:
            self.store.put(f'users/{user}/totals', {})
        except Exception:
            pass
        return {'status': 'reset', 'user': user}

    def reset_all(self) -> dict:
        """Reset all metering data."""
        try:
            self.store.put('recent_log', [])
        except Exception:
            pass
        return {'status': 'all metering data reset'}

    def test(self):
        """Self-test: record some usage and verify billing."""
        # Record sample usage
        self.record(user='test_user', fn='api/info', duration=1.5, status='success')
        self.record(user='test_user', fn='api/info', duration=0.5, status='success')
        self.record(user='test_user', fn='api/compute', duration=3.0, status='error')
        self.record(user='test_user2', fn='api/info', duration=0.2, status='success')

        result = {
            'usage_test_user': self.usage('test_user'),
            'usage_all': self.usage_all(),
            'bill_test_user': self.bill('test_user', margin=0.2),
            'bill_all': self.bill_all(margin=0.1),
            'recent': self.recent(n=5),
            'config': self.config(),
        }

        # Cleanup
        self.reset_user('test_user')
        self.reset_user('test_user2')

        return result
