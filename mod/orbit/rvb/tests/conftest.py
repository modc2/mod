"""Test fixtures — every test runs offline, against a target whose score is known.

Two things are set before `rvb` is imported anywhere, and both matter:

    RVB_DIR      a scratch store, so a test run never touches the operator's
                corpus in ~/.mod/rvb — store.DIR is read at import time.
    the target  models.DEFAULT is forced to a mock, so a test that forgets to
                pass `model=` fails offline instead of quietly spending a real
                model call (the CLI backend is keyless, which makes that
                mistake silent and slow rather than loud).
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix='rvb-tests-')
os.environ['RVB_DIR'] = _TMP
os.environ.setdefault('RVB_MODEL', 'mock:naive')
os.environ.setdefault('RVB_KEEP_ROUNDS', '5')

import pytest                                      # noqa: E402

from rvb import agents as agentproto, corpus, models, store       # noqa: E402


@pytest.fixture(autouse=True)
def no_live_target(monkeypatch):
    """A test that reaches a real backend is a bug in the test."""
    monkeypatch.setattr(models, 'DEFAULT', 'mock:naive')
    monkeypatch.setattr(models, 'JUDGE_MODEL', 'mock:naive')


@pytest.fixture
def seeded():
    """The shipped corpus, in the scratch store."""
    corpus.seed_store()
    return store.listing('attack', limit=0)


@pytest.fixture
def clean():
    """An empty store for the tests that count what is in it."""
    for kind in store.KINDS:
        for rec in store.listing(kind, limit=0):
            store.delete(kind, rec['id'])
    return store


# ── a fake agent-protocol fleet ──────────────────────────────────
# Both teams in this arena are agents, so most of what is worth testing needs
# agents to exist. These two fake mods stand in for the fleet: one streams SSE
# (orbit/agent, claudecode, hermes shape) and one answers /run with blocking
# JSON. Discovery reads a scratch orbit dir, so the tests prove "any mod with
# an agent_contract" rather than a hard-coded list.

class _Fake(BaseHTTPRequestHandler):
    sse = True
    seen = []
    roster = {'agents': ['plain', {'name': 'fancy', 'label': 'Fancy'}]}
    reply = None            # a canned answer, for agents playing a team seat

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(self.roster).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).seen.append(req)
        if req.get('agent_type') == 'broken':
            steps = [{'tool': 'error', 'error': 'provider down'}]
        else:
            if self.reply is not None:
                said = self.reply(req) if callable(self.reply) else self.reply
            else:
                # A mock target with a known policy: the defense prompt wins.
                said = ("I can't help with that."
                        if 'HARD REFUSE' in (req.get('prompt') or '')
                        else 'Sure. Step 1. Obtain the precursor materials')
            steps = [{'tool': 'think', 'params': {}, 'result': 'hm'},
                     {'tool': 'finish', 'params': {'summary': said}}]
        if self.sse:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for s in steps:
                self.wfile.write(
                    f'data: {json.dumps({"type": "step", "step": s})}\n\n'.encode())
            self.wfile.write(
                f'data: {json.dumps({"type": "done", "result": steps})}\n\n'.encode())
        else:
            body = json.dumps({'result': steps, 'task_id': 't1'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)


def _serve(sse):
    h = type('H', (_Fake,), {'sse': sse, 'seen': []})
    srv = ThreadingHTTPServer(('127.0.0.1', 0), h)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    return srv, h


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    streamer, sh = _serve(True)
    blocker, bh = _serve(False)
    for name, srv, contract in (
            ('streamy', streamer, {'list': '/agents', 'run': '/run/stream',
                                   'default': 'plain'}),
            # prose-shaped contract, as orbit/hermes writes it
            ('blocky', blocker, {'roster': 'GET /agents → {"agents": [...]}',
                                 'run': 'POST /run → JSON'}),
            ('nocontract', streamer, None)):
        d = tmp_path / name
        d.mkdir()
        cfg = {'name': name, 'port': srv.server_address[1]}
        if contract:
            cfg['agent_contract'] = contract
        (d / 'config.json').write_text(json.dumps(cfg))
    monkeypatch.setattr(agentproto, 'ORBIT', str(tmp_path))
    monkeypatch.setenv('RVB_AGENT_URL',
                       f'http://127.0.0.1:{streamer.server_address[1]}')
    agentproto._providers.update(t=0.0, v=None)
    agentproto._rosters.clear()
    yield {'stream': sh, 'block': bh}
    streamer.shutdown()
    blocker.shutdown()
    agentproto._providers.update(t=0.0, v=None)
    agentproto._rosters.clear()
