"""
hermes - NousResearch Hermes weights as a provider, running on this box.

Fourth local provider beside the three LFM ones in liquid.py, and the same
deal: the weights are on this machine (or in a llama.cpp / ollama server on
it), nothing goes to OpenRouter or Venice, so a call costs the module nothing
and a run on it is never billed and never gated on a credit balance.

What is different from `liquidai` is where the weights are held. The liquidai
runtime loads them inside THAT module's process; hermes does the same in its
own. Either way this file is a client, not a loader — an 8B model resident in
the console's process is a console that swaps, and the module whose job is to
hold the weights should be the process that holds them.

Reached over HTTP, activator knock first: a direct port call never wakes a
slept module, and a provider that reports "unreachable" because its module was
asleep is a provider nobody can pick.
"""
import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from .liquid import ZeroCostModel

try:
    import mod as m
except ImportError:
    m = None

# the module's own port (config.json) and the activator's knock
DIRECT = os.environ.get('HERMES_URL', 'http://127.0.0.1:50920')
KNOCK = os.environ.get('ACTIVATOR_URL', 'http://localhost:9000') + '/api/hermes'

CHAT_TIMEOUT = float(os.environ.get('AGENT_HERMES_TIMEOUT', 600))
PROBE_TIMEOUT = 5.0


class HermesModel(ZeroCostModel):
    """The hermes module's local weights, driven over its HTTP API."""

    def __init__(self, base: str = None, **kwargs):
        self._base = base
        self._why: Optional[str] = None

    # ── reaching the module ──────────────────────────────────────────

    def base(self, refresh: bool = False) -> Optional[str]:
        """Where hermes is answering, or None.

        The knock is tried first because it revives a sleeping module; the
        direct port is the fallback for a deployment with no activator. The
        answer is cached on the instance, and instances are per run.
        """
        if self._base and not refresh:
            return self._base
        for candidate in (KNOCK, DIRECT):
            try:
                with urllib.request.urlopen(candidate + '/health',
                                            timeout=PROBE_TIMEOUT) as r:
                    body = json.loads(r.read().decode() or '{}')
                # a hermes with no llama backend answers 503 and never gets
                # here — which is right: it cannot generate, so it is not a
                # provider a run should be handed
                if body.get('ok'):
                    self._base = candidate
                    self._why = None
                    return candidate
                # `why` is the module's own account of what is missing and how
                # to fix it; `detail` only names which backend it picked
                self._why = (body.get('why') or body.get('detail')
                             or 'hermes has no inference backend')
            except urllib.error.HTTPError as e:
                try:
                    self._why = (json.loads(e.read().decode() or '{}').get('detail')
                                 or f'hermes answered {e.code}')
                except Exception:
                    self._why = f'hermes answered {e.code}'
            except Exception as e:
                self._why = f'hermes unreachable: {e}'
        return None

    def ready(self) -> bool:
        return self.base() is not None

    def why(self) -> str:
        return self._why or 'hermes is not serving on this host'

    # ── the model list ───────────────────────────────────────────────

    def free_models(self) -> List[str]:
        """Everything hermes can run — its Hermes registry plus whatever its
        backend already serves. Free by construction: it is this box's CPU."""
        base = self.base()
        if not base:
            return []
        try:
            with urllib.request.urlopen(base + '/models', timeout=PROBE_TIMEOUT) as r:
                data = json.loads(r.read().decode() or '{}')
        except Exception as e:
            print(f'[agent] hermes model list failed: {e}')
            return []
        return [row.get('key') for row in data.get('models', []) if row.get('key')]

    # ── one completion ───────────────────────────────────────────────

    def forward(self, prompt: str, stream: bool = True, model: str = None,
                max_tokens: int = 1024, temperature: float = 0.0,
                free: bool = False, history: List[dict] = None, **kwargs) -> str:
        """One completion through the hermes module.

        `stream` is accepted and ignored, like the LFM providers: the agent
        loop needs the whole step back before it can parse an anchor out of
        it, and hermes streams internally either way.
        """
        base = self.base()
        if not base:
            raise RuntimeError(
                f"hermes cannot run this step — {self.why()}. It runs weights on "
                f"this box only: install llama-cpp-python, or start a llama-server "
                f"/ ollama and point HERMES_API_URL at it.")
        body = {'message': prompt, 'model': model, 'max_tokens': max_tokens,
                'temperature': temperature,
                # every call is its own step: the agent loop carries the whole
                # transcript in `prompt`, so a second history inside hermes
                # would be the same conversation twice
                'clear': True}
        req = urllib.request.Request(
            base + '/chat', data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT) as r:
                out = json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:400]
            raise RuntimeError(f'hermes refused the completion ({e.code}): {detail}')
        except Exception as e:
            raise RuntimeError(f'hermes unreachable mid-run: {e}') from e
        if out.get('error'):
            raise RuntimeError(f"hermes: {out['error']}")
        return out.get('text') or ''
