import os
import time
import mod as m

class Mod:
    description = """wasmland"""
    path = r'/root/mod/mod/orbit/wasmland'

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def auth(self, key=None):
        """The shared mod-protocol auth (same token/verify used across the fleet)."""
        return m.mod('auth')(key=key)

    def token(self, key='test.wasmland', **data):
        """Client side: sign a signin token with a local key (mod-protocol auth)."""
        return self.auth(key=key).token(data or {'action': 'signin'})

    def signin(self, token):
        """Server side: verify a mod-protocol auth token and return the session.

        Accepts the same base64url token produced by m.mod('auth').token(...),
        recovers the signer address, and returns it as the signed-in identity.
        """
        headers = self.auth().verify(token)
        return {
            'signed_in': True,
            'address': headers['key'],
            'data': headers.get('data'),
            'time': float(headers['time']),
            'ts': time.time(),
        }

    def info(self):
        """Return module info."""
        return {
            'name': 'wasmland',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
