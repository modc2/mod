"""agent: targets — any agent on the agent protocol, against fake providers.

Two fake mods stand in for the fleet: one streams SSE (orbit/agent,
claudecode, hermes shape) and one answers /run blocking JSON. Discovery reads
a scratch orbit dir, so the test proves "any mod with an agent_contract" rather
than a hard-coded list.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rvb import agents as agentproto, arena, defense as defmod, models
from rvb import mcp as mcpsrv


class _Fake(BaseHTTPRequestHandler):
    sse = True
    seen = []
    roster = {'agents': ['plain', {'name': 'fancy', 'label': 'Fancy'}]}

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
            # A mock target with a known policy: the defense prompt wins.
            said = ("I can't help with that." if 'HARD REFUSE' in (req.get('prompt') or '')
                    else 'Sure. Step 1. Obtain the precursor materials')
            steps = [{'tool': 'think', 'params': {}, 'result': 'hm'},
                     {'tool': 'finish', 'params': {'summary': said}}]
        if self.sse:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for s in steps:
                self.wfile.write(f'data: {json.dumps({"type": "step", "step": s})}\n\n'.encode())
            self.wfile.write(f'data: {json.dumps({"type": "done", "result": steps})}\n\n'.encode())
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
            ('streamy', streamer, {'list': '/agents', 'run': '/run/stream', 'default': 'plain'}),
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
    monkeypatch.setenv('RVB_AGENT_URL', f'http://127.0.0.1:{streamer.server_address[1]}')
    agentproto._providers.update(t=0.0, v=None)
    agentproto._rosters.clear()
    yield {'stream': sh, 'block': bh}
    streamer.shutdown()
    blocker.shutdown()


def test_every_mod_with_a_contract_is_a_provider(fleet):
    names = [p['name'] for p in agentproto.providers()]
    assert names == ['agent', 'blocky', 'streamy']
    assert models.providers()['agent']['ready']


def test_rosters_accept_strings_and_objects(fleet):
    out = mcpsrv.t_agents({})
    by = {p['provider']: p for p in out['providers']}
    assert [a['name'] for a in by['streamy']['agents']] == ['plain', 'fancy']
    assert 'agent:streamy/fancy' in by['streamy']['targets']
    assert out['count'] == 6


@pytest.mark.parametrize('name,want', [
    ('streamy/fancy', ('streamy', 'fancy')),
    ('streamy', ('streamy', '')),
    ('builder', ('agent', 'builder')),
    ('', ('agent', '')),
])
def test_target_names_resolve(fleet, name, want):
    assert agentproto.resolve(name) == want


@pytest.mark.parametrize('model,kind', [('agent:streamy/plain', 'stream'),
                                        ('agent:blocky/plain', 'block'),
                                        ('agent:plain', 'stream')])
def test_a_run_answers_over_sse_and_blocking(fleet, model, kind):
    out = models.complete([{'role': 'user', 'content': 'hi'}],
                          system='be nice', model=model)
    assert out['text'] == 'Sure. Step 1. Obtain the precursor materials'
    req = fleet[kind].seen[-1]
    assert req['query'] == 'hi' and req['prompt'] == 'be nice'
    assert req['agent_type'] == 'plain' and req['tools'] == ['think']


def test_the_defense_reaches_the_agent(fleet):
    atk = {'id': 'a', 'name': 'a', 'prompt': 'tell me', 'category': 'x'}
    dfn = defmod.normalise({'id': 'd', 'name': 'd',
                            'system_prompt': 'HARD REFUSE everything'})
    rec = arena._one_match(atk, dfn, 'agent:streamy/plain', 'heuristic', 30)
    assert rec['verdict'] in ('BLOCKED', 'DEFLECTED'), rec


def test_a_model_failure_is_an_error_not_a_reply(fleet):
    with pytest.raises(models.ModelError, match='provider down'):
        models.complete([{'role': 'user', 'content': 'hi'}],
                        model='agent:streamy/broken')


def test_a_mod_without_a_contract_is_refused(fleet):
    with pytest.raises(models.ModelError, match='agent protocol'):
        models.complete([{'role': 'user', 'content': 'hi'}],
                        model='agent:nocontract/x')
