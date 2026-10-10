"""
sshland — an SSH host and connection manager for the mod fleet.

Keeps a local inventory of SSH hosts and exposes fns to add, list, remove,
and test connections. The inventory (private: addresses, users, key paths)
lives off-tree in ~/.mod/sshland/hosts.json, never in the committed config.

CLI:
    m sshland                       # module info
    m sshland/hosts                 # list known hosts
    m sshland/add name user@host    # add a host (optional key=~/.ssh/id_ed25519 port=22)
    m sshland/remove name
    m sshland/test name             # ssh connectivity check for one host (BatchMode, no prompt)
    m sshland/test                  # test every host in parallel, return summary
    m sshland/test_all              # alias for test with no name
    m sshland/rename old new        # rename a host entry without remove+re-add
"""
import os
import re
import subprocess
import concurrent.futures
from datetime import datetime, timezone
import mod as m

STATE_PATH = '~/.mod/sshland/hosts.json'
TARGET_RE = re.compile(r'^(?:(?P<user>[^@\s]+)@)?(?P<host>[^@\s:]+)(?::(?P<port>\d+))?$')


class Mod:
    description = 'SSH host and connection manager: keep an inventory of hosts and test connectivity'

    def __init__(self, key='sshland', state_path=None):
        self.state_path = m.abspath(state_path or STATE_PATH)

    # --- state ---------------------------------------------------------------

    def _load(self) -> dict:
        return m.get(self.state_path, {'hosts': {}})

    def _save(self, st: dict):
        m.put(self.state_path, st)

    # --- fns -----------------------------------------------------------------

    def info(self) -> dict:
        st = self._load()
        return {
            'name': 'sshland',
            'description': self.description,
            'hosts': len(st['hosts']),
            'state_path': self.state_path,
        }

    def hosts(self) -> dict:
        """List known hosts (name -> user/host/port; key path omitted from listing)."""
        st = self._load()
        return {name: {**{k: v for k, v in h.items() if k != 'key'}, 'has_key': 'key' in h}
                for name, h in st['hosts'].items()}

    def add(self, name: str, target: str, key: str = None, port: int = None, overwrite: bool = False) -> dict:
        """Add a host. target is 'user@host', 'user@host:port', or bare 'host'."""
        match = TARGET_RE.match((target or '').strip())
        if not match:
            raise ValueError(f'cannot parse target: {target!r} (use user@host[:port])')
        host = {
            'user': match.group('user') or 'root',
            'host': match.group('host'),
            'port': int(port or match.group('port') or 22),
        }
        if key:
            host['key'] = m.abspath(key)
            if not os.path.isfile(host['key']):
                raise ValueError(f'key file not found: {host["key"]!r}')
        st = self._load()
        updated = name in st['hosts']
        if updated and not overwrite:
            raise ValueError(f"host {name!r} already exists; use overwrite=True to replace it")
        st['hosts'][name] = host
        self._save(st)
        safe = {k: v for k, v in host.items() if k != 'key'}
        if 'key' in host:
            safe['has_key'] = True
        return {name: safe, 'updated': updated}

    def rename(self, old_name: str, new_name: str) -> dict:
        """Rename a host entry in the inventory."""
        st = self._load()
        if old_name not in st['hosts']:
            raise ValueError(f'unknown host: {old_name!r} (add it with m sshland/add)')
        if new_name in st['hosts']:
            raise ValueError(f"host {new_name!r} already exists; remove it first or choose a different name")
        st['hosts'][new_name] = st['hosts'].pop(old_name)
        self._save(st)
        return {'renamed': old_name, 'to': new_name}

    def remove(self, name: str) -> dict:
        st = self._load()
        removed = st['hosts'].pop(name, None)
        self._save(st)
        return {'removed': name, 'found': removed is not None}

    def _test_one(self, name: str, host: dict, timeout: int) -> dict:
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={timeout}',
               '-o', 'StrictHostKeyChecking=accept-new', '-p', str(host['port'])]
        if host.get('key'):
            cmd += ['-i', host['key']]
        cmd += [f"{host['user']}@{host['host']}", 'true']
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f'ssh timed out after {timeout + 5}s')
        return {
            'ok': result.returncode == 0,
            'returncode': result.returncode,
            'stderr': result.stderr.strip()[-500:],
        }

    def test(self, name: str = None, timeout: int = 10) -> dict:
        """Non-interactive ssh connectivity check.

        With a name: tests one host, returns {name, ok, returncode, stderr}.
        Without a name (or name="*"/"all"): tests every host in parallel,
        returns {host_name: {ok, returncode, stderr}, ..., _summary: {total, reachable, failed}}.
        """
        st = self._load()
        if name and name not in ('*', 'all'):
            host = st['hosts'].get(name)
            if not host:
                raise ValueError(f'unknown host: {name!r} (add it with m sshland/add)')
            try:
                result = self._test_one(name, host, timeout)
            except Exception as exc:
                result = {'ok': False, 'returncode': -1, 'stderr': str(exc)[:500]}
            st['hosts'][name]['last_tested'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            st['hosts'][name]['last_ok'] = result['ok']
            self._save(st)
            return {'name': name, **result}

        hosts = st['hosts']
        if not hosts:
            return {'_summary': {'total': 0, 'reachable': 0, 'failed': 0}}

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
            futures = {pool.submit(self._test_one, n, h, timeout): n for n, h in hosts.items()}
            for future in concurrent.futures.as_completed(futures):
                n = futures[future]
                try:
                    results[n] = future.result()
                except Exception as exc:
                    results[n] = {'ok': False, 'returncode': -1, 'stderr': str(exc)[:500]}

        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for n, r in results.items():
            st['hosts'][n]['last_tested'] = ts
            st['hosts'][n]['last_ok'] = r['ok']
        self._save(st)
        reachable = sum(1 for r in results.values() if r['ok'])
        results['_summary'] = {'total': len(hosts), 'reachable': reachable, 'failed': len(hosts) - reachable}
        return results

    def test_all(self, timeout: int = 10) -> dict:
        """Test every host in parallel. Alias for test() with no name."""
        return self.test(timeout=timeout)
