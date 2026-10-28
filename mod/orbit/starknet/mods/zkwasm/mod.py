"""starknet.zkwasm — zkWASM: The zkwasm mod (zero-knowledge WASM lab), as a Starknet app."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import apps  # noqa: E402


class Mod:
    """starknet.zkwasm — points at orbit/zkwasm (API only, no app yet)."""

    def facts(self):
        i = apps.get(f"http://127.0.0.1:{apps.port_of('zkwasm')}/info")
        return [['stage', i.get('stage', '—')], ['version', i.get('version', '—')]]
