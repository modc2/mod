"""passport - the fleet's one auth protocol (see README.md).

    m.mod('passport')().verify(token)
    m.mod('passport')().role(token, mod='agent')
    m.mod('passport')().serve()            # :51390
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from passport import Passport  # noqa: E402


class Mod:
    description = 'One auth protocol for every module; hot-reloaded, never restarted'
    port = 51390

    def __init__(self, **kwargs):
        self.p = Passport()

    def forward(self, token: str = None, mod: str = None):
        return self.role(token, mod) if token else self.protocol()

    def verify(self, token: str) -> dict:
        return self.p.verify(token)

    def address(self, token: str):
        return self.p.address(token)

    def role(self, token: str, mod: str = None) -> dict:
        return self.p.role(token, mod)

    def protocol(self) -> dict:
        return {**self.p.protocol(), 'fingerprint': self.p.fingerprint()}

    def health(self) -> dict:
        return {'ok': True, 'fingerprint': self.p.fingerprint(),
                'version': self.p.protocol().get('version')}

    def serve(self, port=None, background=False):
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'), '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE, start_new_session=True)
        return {'pid': proc.pid, 'port': port}

    def kill(self, port=None):
        """By port, never by name — a pattern kill on this box takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(os.path.dirname(HERE), 'tests')],
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'out': r.stdout[-2000:]}
