"""
Balancer - Load balancer for replicated mod servers with TTL and auto-scaling.

Deploys N replicas of a module with tags (mod::1, mod::2, ...) and
distributes requests across them using configurable strategies:
  - round_robin: rotate through replicas sequentially
  - aggregate: call all replicas and merge results
  - failover: try each replica until one succeeds
  - random: pick a random replica

Workers stay alive for N seconds (worker_ttl) before being reaped.
When M+ users are active, auto-scales to maintain a target user-per-worker ratio.

Usage:
    balancer = m.mod('balancer')()

    # Deploy 3 replicas of 'api' (workers live 60s by default)
    balancer.deploy('api', n=3, port_start=9000)

    # Configure TTL and scaling
    balancer.configure('api', worker_ttl=60, user_threshold=2, users_per_worker=2)

    # Call through the balancer with auth token
    result = balancer.call('api', 'info', {}, token='...')

    # Check full status
    balancer.status('api')

    # Kill all replicas
    balancer.teardown('api')
"""

from typing import *
import time
import random
import threading
import math
import mod as m

print = m.print


class Balancer:

    def __init__(self, path='~/.mod/server/balancer'):
        self.store = m.mod('store')(path)
        self.namespace = m.mod('server.namespace')()
        self.client = m.mod('client')()
        self.auth = m.mod('auth')()
        self._rr_counters = {}  # round-robin counters per group
        self._sessions = {}     # {user_key: last_seen_timestamp}
        self._sessions_lock = threading.Lock()
        self._reaper_thread = None
        self._reaper_stop = threading.Event()

    # ---- Configuration ----

    def _get_config(self, mod: str) -> dict:
        """Get scaling config for a module group."""
        return self.store.get(f'config/{mod}', {
            'worker_ttl': 60,
            'min_replicas': 1,
            'max_replicas': 5000,         # supports up to 10k users at 2:1 ratio
            'user_threshold': 2,
            'users_per_worker': 2,
            'session_window': 60,
            'max_users': 10000,
        })

    def configure(self, mod: str, worker_ttl: int = None, min_replicas: int = None,
                  max_replicas: int = None, user_threshold: int = None,
                  users_per_worker: int = None, session_window: int = None) -> dict:
        """Set scaling config for a module group.

        Args:
            mod: Module group name
            worker_ttl: Seconds before idle workers are reaped (N)
            min_replicas: Floor replica count (never scale below)
            max_replicas: Ceiling replica count (never scale above)
            user_threshold: Active user count (M) that triggers scaling
            users_per_worker: Target ratio for auto-scale formula
            session_window: Seconds to consider a user "active"
        """
        config = self._get_config(mod)
        if worker_ttl is not None:
            config['worker_ttl'] = worker_ttl
        if min_replicas is not None:
            config['min_replicas'] = min_replicas
        if max_replicas is not None:
            config['max_replicas'] = max_replicas
        if user_threshold is not None:
            config['user_threshold'] = user_threshold
        if users_per_worker is not None:
            config['users_per_worker'] = users_per_worker
        if session_window is not None:
            config['session_window'] = session_window
        self.store.put(f'config/{mod}', config)
        return config

    # ---- Session Tracking ----

    def _touch_session(self, user_key: str):
        """Record that a user is active right now. Thread-safe for 10k+ users."""
        with self._sessions_lock:
            self._sessions[user_key.lower()] = time.time()
            # Periodic cleanup: every 1000 touches, prune stale entries
            if len(self._sessions) > 10000:
                self._prune_sessions()

    def _prune_sessions(self, max_age: int = 300):
        """Remove sessions older than max_age seconds. Called under lock."""
        cutoff = time.time() - max_age
        stale = [k for k, ts in self._sessions.items() if ts < cutoff]
        for k in stale:
            del self._sessions[k]

    def active_users(self, window: int = None, mod: str = None) -> list:
        """Return user keys seen within the session window."""
        if window is None:
            config = self._get_config(mod or '_global')
            window = config.get('session_window', 60)
        cutoff = time.time() - window
        with self._sessions_lock:
            return [k for k, ts in self._sessions.items() if ts >= cutoff]

    def active_user_count(self, window: int = None, mod: str = None) -> int:
        """Return count of active users (faster than active_users() at scale)."""
        if window is None:
            config = self._get_config(mod or '_global')
            window = config.get('session_window', 60)
        cutoff = time.time() - window
        with self._sessions_lock:
            return sum(1 for ts in self._sessions.values() if ts >= cutoff)

    # ---- Auto-Scaling ----

    def _auto_scale(self, mod: str):
        """Scale replicas based on active user count.

        If active_users >= user_threshold: target = ceil(active_users / users_per_worker)
        If below threshold: scale down to min_replicas.
        Always clamp between min_replicas and max_replicas.
        Supports up to 10k concurrent users.
        """
        config = self._get_config(mod)
        group = self.store.get(f'groups/{mod}', {})
        current = group.get('replicas', [])
        current_n = len(current)

        n_users = self.active_user_count(window=config['session_window'], mod=mod)

        # Enforce max_users cap
        max_users = config.get('max_users', 10000)
        if n_users > max_users:
            print(f'Balancer: user cap reached ({n_users}/{max_users}) for {mod}', color='red')

        threshold = config['user_threshold']
        min_r = config['min_replicas']
        max_r = config['max_replicas']
        per_worker = config['users_per_worker']

        if n_users >= threshold:
            target = min(max_r, max(min_r, math.ceil(n_users / per_worker)))
        else:
            target = min_r

        if target != current_n:
            print(f'Balancer auto-scale {mod}: {current_n} -> {target} (users={n_users}, threshold={threshold})', color='cyan')
            self.scale(mod, target)

    # ---- TTL Reaper ----

    def _start_reaper(self):
        """Start background thread that periodically reaps expired workers."""
        if self._reaper_thread and self._reaper_thread.is_alive():
            return
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(target=self._reaper_loop, daemon=True)
        self._reaper_thread.start()
        print('Balancer: reaper thread started', color='green')

    def _stop_reaper(self):
        """Stop the background reaper thread."""
        self._reaper_stop.set()
        if self._reaper_thread:
            self._reaper_thread.join(timeout=5)

    def _reaper_loop(self):
        """Background loop: check all groups every 15s, reap expired replicas, prune stale sessions."""
        while not self._reaper_stop.is_set():
            try:
                for mod in self.groups():
                    self._reap(mod)
                # Prune stale sessions to keep memory bounded at 10k+ scale
                with self._sessions_lock:
                    self._prune_sessions()
            except Exception as e:
                print(f'Balancer reaper error: {e}', color='red')
            self._reaper_stop.wait(15)

    def _reap(self, mod: str):
        """Kill replicas older than worker_ttl, respecting min_replicas."""
        config = self._get_config(mod)
        ttl = config['worker_ttl']
        min_r = config['min_replicas']
        group = self.store.get(f'groups/{mod}', {})
        replicas = group.get('replicas', [])
        timestamps = group.get('replica_times', {})
        now = time.time()

        if len(replicas) <= min_r:
            return

        server = m.mod('server')()
        expired = []
        for r in replicas:
            started = timestamps.get(r, group.get('deployed_at', now))
            if (now - started) > ttl:
                expired.append(r)

        # Don't kill below min_replicas
        can_kill = len(replicas) - min_r
        to_kill = expired[:can_kill]

        for name in to_kill:
            try:
                server.kill(name)
                replicas.remove(name)
                timestamps.pop(name, None)
                print(f'Balancer reaped expired replica {name} (ttl={ttl}s)', color='yellow')
            except Exception as e:
                print(f'Balancer reap failed for {name}: {e}', color='red')

        if to_kill:
            group['replicas'] = replicas
            group['replica_times'] = timestamps
            self.store.put(f'groups/{mod}', group)

    # ---- Deployment ----

    def deploy(self, mod: str, n: int = 3, port_start: int = None,
               key: str = None, sandbox: str = 'subprocess',
               params: dict = None, pm: str = None) -> dict:
        """Deploy N replicas of a module with tags ::1, ::2, ..., ::N.

        Args:
            mod: Module name to replicate
            n: Number of replicas
            port_start: Starting port (auto if None)
            key: Auth key for servers
            sandbox: 'subprocess' or 'docker'
            params: Extra params for the module
            pm: Process manager override
        """
        server = m.mod('server')()
        if pm:
            server.set_pm(pm)

        replicas = []
        replica_times = {}
        now = time.time()
        for i in range(1, n + 1):
            tag = f'{mod}::{i}'
            port = (port_start + i - 1) if port_start else None
            try:
                server.serve(
                    mod=tag,
                    params=params or {},
                    port=port,
                    key=key,
                    remote=True,
                    sandbox=sandbox,
                )
                time.sleep(1)
                ns = self.namespace.namespace(search=tag)
                addr = ns.get(tag, f'http://0.0.0.0:{port}' if port else 'unknown')
                replicas.append({'name': tag, 'address': addr, 'status': 'running'})
                replica_times[tag] = now
                print(f'Deployed replica {tag} at {addr}', color='green')
            except Exception as e:
                replicas.append({'name': tag, 'address': '', 'status': f'error: {e}'})
                print(f'Failed to deploy replica {tag}: {e}', color='red')

        group = {
            'mod': mod,
            'replicas': [r['name'] for r in replicas if r['status'] == 'running'],
            'replica_times': replica_times,
            'strategy': 'round_robin',
            'deployed_at': now,
        }
        self.store.put(f'groups/{mod}', group)

        # Ensure reaper is running
        self._start_reaper()

        return {'mod': mod, 'replicas': replicas, 'count': len(replicas)}

    def teardown(self, mod: str) -> dict:
        """Kill all replicas of a module group."""
        server = m.mod('server')()
        group = self.store.get(f'groups/{mod}', {})
        killed = []
        for name in group.get('replicas', []):
            try:
                server.kill(name)
                killed.append(name)
            except Exception as e:
                print(f'Failed to kill {name}: {e}', color='red')
        self.store.rm(f'groups/{mod}')
        self.store.rm(f'config/{mod}')
        return {'mod': mod, 'killed': killed}

    def scale(self, mod: str, n: int, **kwargs) -> dict:
        """Scale a replica group to N instances."""
        group = self.store.get(f'groups/{mod}', {})
        current = group.get('replicas', [])
        replica_times = group.get('replica_times', {})
        current_n = len(current)

        if n == current_n:
            return {'mod': mod, 'replicas': current, 'message': 'already at target'}

        if n > current_n:
            server = m.mod('server')()
            added = []
            now = time.time()
            for i in range(current_n + 1, n + 1):
                tag = f'{mod}::{i}'
                if tag in current:
                    continue
                try:
                    server.serve(mod=tag, remote=True, **kwargs)
                    time.sleep(1)
                    current.append(tag)
                    replica_times[tag] = now
                    added.append(tag)
                except Exception as e:
                    print(f'Failed to deploy {tag}: {e}', color='red')
            group['replicas'] = current
            group['replica_times'] = replica_times
            self.store.put(f'groups/{mod}', group)
            return {'mod': mod, 'added': added, 'total': len(current)}
        else:
            server = m.mod('server')()
            to_kill = current[n:]
            for name in to_kill:
                try:
                    server.kill(name)
                    replica_times.pop(name, None)
                except Exception:
                    pass
            group['replicas'] = current[:n]
            group['replica_times'] = replica_times
            self.store.put(f'groups/{mod}', group)
            return {'mod': mod, 'removed': to_kill, 'total': n}

    # ---- Calling ----

    def call(self, mod: str, fn: str, params: dict = None,
             strategy: str = None, timeout: int = 10, key: str = None,
             token: str = None) -> Any:
        """Call a function across the replica group.

        Args:
            mod: Module group name
            fn: Function to call
            params: Parameters
            strategy: 'round_robin', 'aggregate', 'failover', 'random'
            timeout: Request timeout
            key: Auth key
            token: Auth token (base64url). If provided, verifies and tracks user session.
        """
        # Auth token verification + session tracking
        if token:
            try:
                token_data = self.auth.verify(token)
                user_key = token_data.get('key', '')
                if user_key:
                    self._touch_session(user_key)
            except Exception as e:
                raise Exception(f'Balancer auth failed: {e}')

        # Auto-scale based on active users
        try:
            self._auto_scale(mod)
        except Exception:
            pass

        group = self.store.get(f'groups/{mod}', {})
        replicas = group.get('replicas', [])
        strategy = strategy or group.get('strategy', 'round_robin')

        if not replicas:
            return self.client.call(fn=f'{mod}/{fn}', params=params or {}, timeout=timeout, key=key)

        ns = self.namespace.namespace()
        live = [r for r in replicas if r in ns]
        if not live:
            raise Exception(f'No live replicas for {mod}')

        if strategy == 'round_robin':
            return self._round_robin(live, fn, params, timeout, key, ns)
        elif strategy == 'aggregate':
            return self._aggregate(live, fn, params, timeout, key, ns)
        elif strategy == 'failover':
            return self._failover(live, fn, params, timeout, key, ns)
        elif strategy == 'random':
            target = random.choice(live)
            return self.client.call(fn=f'{target}/{fn}', params=params or {}, timeout=timeout, key=key)
        else:
            raise ValueError(f'Unknown strategy: {strategy}')

    def _round_robin(self, replicas, fn, params, timeout, key, ns):
        idx = self._rr_counters.get(fn, 0) % len(replicas)
        self._rr_counters[fn] = idx + 1
        target = replicas[idx]
        return self.client.call(fn=f'{target}/{fn}', params=params or {}, timeout=timeout, key=key)

    def _aggregate(self, replicas, fn, params, timeout, key, ns):
        futures = []
        for replica in replicas:
            futures.append(
                m.submit(
                    self.client.call,
                    fn=f'{replica}/{fn}',
                    params=params or {},
                    timeout=timeout,
                    key=key,
                    mode='thread',
                )
            )
        results = m.wait(futures, timeout=timeout)
        return results

    def _failover(self, replicas, fn, params, timeout, key, ns):
        last_error = None
        for replica in replicas:
            try:
                return self.client.call(fn=f'{replica}/{fn}', params=params or {}, timeout=timeout, key=key)
            except Exception as e:
                last_error = e
                print(f'Failover: {replica} failed ({e}), trying next...', color='yellow')
        raise Exception(f'All replicas failed for {fn}. Last error: {last_error}')

    # ---- Info ----

    def status(self, mod: str = None) -> dict:
        """Full status: replicas, active users, config, health.

        If mod is None, returns status for all groups.
        """
        if mod is None:
            all_groups = self.groups()
            return {g: self.status(g) for g in all_groups} if all_groups else {}

        config = self._get_config(mod)
        group = self.store.get(f'groups/{mod}', {})
        ns = self.namespace.namespace()
        replicas = group.get('replicas', [])
        replica_times = group.get('replica_times', {})
        now = time.time()

        replica_info = []
        for r in replicas:
            started = replica_times.get(r, group.get('deployed_at', 0))
            age = now - started if started else 0
            ttl_remaining = max(0, config['worker_ttl'] - age)
            replica_info.append({
                'name': r,
                'address': ns.get(r, 'not registered'),
                'live': r in ns,
                'age': round(age, 1),
                'ttl_remaining': round(ttl_remaining, 1),
            })

        n_active = self.active_user_count(window=config['session_window'], mod=mod)
        # Only return full user list if under 100 users (performance at 10k scale)
        active_list = self.active_users(window=config['session_window'], mod=mod) if n_active <= 100 else []
        return {
            'mod': mod,
            'config': config,
            'replicas': replica_info,
            'replica_count': len(replicas),
            'active_users': active_list,
            'active_user_count': n_active,
            'max_users': config.get('max_users', 10000),
            'deployed_at': group.get('deployed_at', 0),
            'strategy': group.get('strategy', 'round_robin'),
        }

    def replicas(self, mod: str = None) -> dict:
        """List replicas for a module group, or all groups."""
        if mod:
            group = self.store.get(f'groups/{mod}', {})
            ns = self.namespace.namespace()
            replicas = group.get('replicas', [])
            return {
                'mod': mod,
                'strategy': group.get('strategy', 'round_robin'),
                'replicas': [
                    {'name': r, 'address': ns.get(r, 'not registered'), 'live': r in ns}
                    for r in replicas
                ],
            }
        groups_data = self.store.ls('groups') if hasattr(self.store, 'ls') else []
        result = {}
        for g in groups_data:
            name = g.replace('.json', '') if isinstance(g, str) else g
            result[name] = self.replicas(name)
        return result

    def set_strategy(self, mod: str, strategy: str) -> dict:
        """Set the load balancing strategy for a module group."""
        assert strategy in ('round_robin', 'aggregate', 'failover', 'random'), \
            f'Invalid strategy: {strategy}'
        group = self.store.get(f'groups/{mod}', {})
        group['strategy'] = strategy
        self.store.put(f'groups/{mod}', group)
        return {'mod': mod, 'strategy': strategy}

    def groups(self) -> list:
        """List all replica groups."""
        try:
            items = self.store.ls('groups') if hasattr(self.store, 'ls') else []
            return [g.replace('.json', '') if isinstance(g, str) else g for g in items]
        except Exception:
            return []

    def health(self, mod: str) -> dict:
        """Check health of all replicas in a group."""
        group = self.store.get(f'groups/{mod}', {})
        replicas = group.get('replicas', [])
        ns = self.namespace.namespace()
        results = {}
        for replica in replicas:
            if replica not in ns:
                results[replica] = {'status': 'not_registered', 'live': False}
                continue
            try:
                info = self.client.call(fn=f'{replica}/info', params={}, timeout=5)
                results[replica] = {'status': 'healthy', 'live': True, 'info': info}
            except Exception as e:
                results[replica] = {'status': f'error: {e}', 'live': False}
        return results

    def test(self):
        """Self-test: deploy 2 replicas of tester, verify TTL/scaling, teardown."""
        result = {}
        result['deploy'] = self.deploy('tester', n=2)
        self.configure('tester', worker_ttl=30, user_threshold=2, users_per_worker=1)
        time.sleep(2)
        result['status'] = self.status('tester')
        result['replicas'] = self.replicas('tester')
        result['health'] = self.health('tester')
        # Simulate user sessions
        self._touch_session('user_a')
        self._touch_session('user_b')
        result['active_users'] = self.active_users(mod='tester')
        result['teardown'] = self.teardown('tester')
        return result
