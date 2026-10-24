"""
Paywall - Gate-compatible x402 payment adapter.

Bridges the x402 middleware with the gate's paywall interface.
Can be enabled/disabled at runtime and configured per-function.

Usage:
    paywall = m.mod('paywall')()
    paywall.enable(receiver='0xYourAddress', price='0.01', currency='USDC', network='base')
    paywall.protect('api/premium_fn')

    # Pass to gate or server:
    gate = m.mod('gate')(paywall=paywall)
    # OR
    m.serve('api', paywall=paywall)

    # Disable at runtime:
    paywall.disable()
"""

from typing import *
import time
import json
import mod as m

print = m.print


class Paywall:

    def __init__(self, path='~/.mod/server/paywall'):
        self.store = m.mod('store')(path)
        self._config = self.store.get('config', {
            'enabled': False,
            'receiver': '',
            'network': 'base',
            'price': '0.01',
            'currency': 'USDC',
            'protected_fns': [],      # list of fn names that require payment (empty = all)
            'free_fns': [],           # explicit free list (overrides protected)
            'facilitator_url': 'https://x402.org/facilitator',
            'verify_timeout': 5,
        })
        self._payment_cache = {}  # simple cache: payment_hash -> (valid, timestamp)
        self._cache_ttl = 300

    # ---- Gate Interface ----

    def gate_check(self, fn: str, headers: dict) -> Optional[dict]:
        """Called by Gate before executing a function.

        Returns None if payment is valid or not required.
        Returns a 402 dict if payment is required.
        """
        config = self.config()
        if not config.get('enabled', False):
            return None

        # Check if this function requires payment
        if not self._is_protected(fn, config):
            return None

        # Check for payment header
        payment = headers.get('X-PAYMENT') or headers.get('X-Payment') or headers.get('x-payment')
        if not payment:
            return self._payment_required(fn, config)

        # Check cache
        import hashlib
        cache_key = hashlib.sha256(f'{fn}:{payment}'.encode()).hexdigest()
        cached = self._payment_cache.get(cache_key)
        if cached:
            valid, ts = cached
            if time.time() - ts < self._cache_ttl:
                if valid:
                    return None  # Payment already verified
                return self._payment_required(fn, config, error='Payment previously rejected')

        # Verify payment
        valid = self._verify_payment(payment, config)
        self._payment_cache[cache_key] = (valid, time.time())

        if valid:
            return None  # Payment verified, proceed

        return self._payment_required(fn, config, error='Payment verification failed')

    def _is_protected(self, fn: str, config: dict) -> bool:
        """Check if a function requires payment."""
        free_fns = config.get('free_fns', [])
        if fn in free_fns:
            return False

        protected_fns = config.get('protected_fns', [])
        if not protected_fns:
            return True  # If no specific protected list, protect all (when enabled)
        return fn in protected_fns

    def _payment_required(self, fn: str, config: dict, error: str = None) -> dict:
        """Return a 402 Payment Required response."""
        return {
            'error': error or 'Payment Required',
            'code': 402,
            'payment_required': {
                'receiver': config.get('receiver', ''),
                'network': config.get('network', 'base'),
                'price': config.get('price', '0.01'),
                'currency': config.get('currency', 'USDC'),
                'fn': fn,
                'facilitator': config.get('facilitator_url', ''),
            }
        }

    def _verify_payment(self, payment: str, config: dict) -> bool:
        """Verify a payment with the facilitator."""
        import requests as req
        try:
            url = config.get('facilitator_url', 'https://x402.org/facilitator')
            res = req.post(
                f'{url}/verify',
                json={
                    'payment': payment,
                    'receiver': config.get('receiver', ''),
                    'network': config.get('network', 'base'),
                    'expectedAmount': config.get('price', '0.01'),
                    'currency': config.get('currency', 'USDC'),
                },
                timeout=config.get('verify_timeout', 5),
            )
            if res.ok:
                data = res.json()
                return data.get('valid', False)
            return False
        except Exception as e:
            print(f'Payment verification error: {e}', color='red')
            return False

    # ---- Configuration ----

    def config(self) -> dict:
        """Get current paywall configuration."""
        return self.store.get('config', self._config)

    def enable(self, receiver: str = None, price: str = None,
               currency: str = None, network: str = None) -> dict:
        """Enable the paywall."""
        config = self.config()
        config['enabled'] = True
        if receiver:
            config['receiver'] = receiver
        if price:
            config['price'] = price
        if currency:
            config['currency'] = currency
        if network:
            config['network'] = network
        self.store.put('config', config)
        self._config = config
        print(f'Paywall enabled: {config["price"]} {config["currency"]} on {config["network"]}', color='green')
        return config

    def disable(self) -> dict:
        """Disable the paywall."""
        config = self.config()
        config['enabled'] = False
        self.store.put('config', config)
        self._config = config
        print('Paywall disabled', color='yellow')
        return config

    def protect(self, fn: str) -> dict:
        """Add a function to the protected list."""
        config = self.config()
        fns = config.get('protected_fns', [])
        if fn not in fns:
            fns.append(fn)
        config['protected_fns'] = fns
        self.store.put('config', config)
        self._config = config
        return config

    def unprotect(self, fn: str) -> dict:
        """Remove a function from the protected list."""
        config = self.config()
        fns = config.get('protected_fns', [])
        if fn in fns:
            fns.remove(fn)
        config['protected_fns'] = fns
        self.store.put('config', config)
        self._config = config
        return config

    def set_free(self, fn: str) -> dict:
        """Mark a function as explicitly free (bypasses payment)."""
        config = self.config()
        fns = config.get('free_fns', [])
        if fn not in fns:
            fns.append(fn)
        config['free_fns'] = fns
        self.store.put('config', config)
        self._config = config
        return config

    def set_price(self, price: str, currency: str = None) -> dict:
        """Update the price."""
        config = self.config()
        config['price'] = price
        if currency:
            config['currency'] = currency
        self.store.put('config', config)
        self._config = config
        return config

    def status(self) -> dict:
        """Get paywall status summary."""
        config = self.config()
        return {
            'enabled': config.get('enabled', False),
            'receiver': config.get('receiver', ''),
            'price': config.get('price', '0.01'),
            'currency': config.get('currency', 'USDC'),
            'network': config.get('network', 'base'),
            'protected_fns': config.get('protected_fns', []),
            'free_fns': config.get('free_fns', []),
        }

    def test(self):
        """Self-test."""
        self.enable(receiver='0xtest', price='0.001', currency='USDC', network='base')
        status = self.status()
        assert status['enabled'] == True
        assert status['receiver'] == '0xtest'

        # Test gate_check without payment header
        result = self.gate_check('some_fn', {})
        assert result is not None
        assert result['code'] == 402

        # Test free fn bypass
        self.set_free('free_fn')
        result = self.gate_check('free_fn', {})
        assert result is None

        self.disable()
        result = self.gate_check('some_fn', {})
        assert result is None

        return {'status': 'all tests passed', 'config': self.config()}
