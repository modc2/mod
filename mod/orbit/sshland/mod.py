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
    m sshland/test name             # ssh connectivity check (BatchMode, no prompt)
"""
import re
import subprocess
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
        return {name: {k: v for k, v in h.items() if k != 'key'}
                for name, h in st['hosts'].items()}

    def add(self, name: str, target: str, key: str = None, port: int = None) -> dict:
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
        st = self._load()
        st['hosts'][name] = host
        self._save(st)
        return {name: host}

    def remove(self, name: str) -> dict:
        st = self._load()
        removed = st['hosts'].pop(name, None)
        self._save(st)
        return {'removed': name, 'found': removed is not None}

    def test(self, name: str, timeout: int = 10) -> dict:
        """Non-interactive ssh connectivity check for a known host."""
        st = self._load()
        host = st['hosts'].get(name)
        if not host:
            raise ValueError(f'unknown host: {name!r} (add it with m sshland/add)')
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={timeout}',
               '-o', 'StrictHostKeyChecking=accept-new', '-p', str(host['port'])]
        if host.get('key'):
            cmd += ['-i', host['key']]
        cmd += [f"{host['user']}@{host['host']}", 'true']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return {
            'name': name,
            'ok': result.returncode == 0,
            'returncode': result.returncode,
            'stderr': result.stderr.strip()[-500:],
        }
