"""The venice read path, against a stub gateway.

Nothing here touches the real venice module or the network beyond loopback:
a `http.server` on an ephemeral port answers `/health`, `/me`, `/models`,
`/key` and `/chat` the way orbit/venice does, and wingman is pointed at it.
What is under test is the part wingman owns — what leaves the box, that the
receipt is written before the answer comes back, that the model's answers land
in `read_flags` and never in `score`, and that every other verb stays offline.
"""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(HERE)

TMP = tempfile.mkdtemp(prefix='wingman-venice-test-')
os.environ['WINGMAN_DIR'] = TMP
os.environ.pop('WINGMAN_VENICE', None)
_real = os.path.expanduser('~/.mod/wingman/models/version-RFB-320.onnx')
if os.path.exists(_real):
    os.makedirs(os.path.join(TMP, 'models'), exist_ok=True)
    shutil.copy(_real, os.path.join(TMP, 'models', 'version-RFB-320.onnx'))

import engine as E                                          # noqa: E402
import mcp                                                  # noqa: E402

# The same object every other caller gets. `import venice` would load a second,
# separate copy — different config cache, different _call to monkeypatch — and
# the isolation tests below would then be testing nothing.
V = E.venice_module()
assert V is E.venice_module()

SEEN = {'chat': [], 'auth': [], 'key': None}
ANSWER = {'json': {
    'expression': 'neutral', 'eyes': 'sunglasses', 'eye_contact': 'none',
    'shot': 'mirror-selfie', 'subject': 'one-clear-subject', 'people_visible': 1,
    'setting': 'gym locker room', 'outfit': 'grey hoodie', 'activity': 'none',
    'distractions': ['a toilet in the background'], 'text_overlay': True,
    'reads_as': 'A person who spends a lot of time at the gym.',
}, 'wrap': True, 'status': 200}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            return self._send(200, {'ok': True, 'service': 'venice-stub'})
        if self.path == '/me':
            SEEN['auth'].append(self.headers.get('authorization'))
            return self._send(200, {'address': '0xstub', 'has_key': bool(SEEN['key']),
                                    'paid_available': False})
        if self.path == '/models':
            return self._send(200, {'data': [
                {'id': 'seeing-model', 'type': 'text', 'model_spec': {
                    'name': 'Seeing', 'capabilities': {'supportsVision': True}}},
                {'id': 'blind-model', 'type': 'text', 'model_spec': {
                    'name': 'Blind', 'capabilities': {'supportsVision': False}}}]})
        return self._send(404, {'error': 'no'})

    def do_POST(self):
        n = int(self.headers.get('content-length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        SEEN['auth'].append(self.headers.get('authorization'))
        if self.path == '/key':
            SEEN['key'] = body.get('key')
            return self._send(200, {'ok': True})
        if self.path == '/chat':
            SEEN['chat'].append(body)
            if ANSWER['status'] != 200:
                return self._send(ANSWER['status'], {'error': 'stub refusal'})
            payload = ANSWER['json']
            text = json.dumps(payload) if not isinstance(payload, str) else payload
            if ANSWER['wrap']:
                text = 'Here is the JSON:\n```json\n' + text + '\n```\nHope that helps.'
            return self._send(200, {'choices': [{'message': {'content': text}}]})
        return self._send(404, {'error': 'no'})

    def do_DELETE(self):
        if self.path == '/key':
            SEEN['key'] = None
            return self._send(200, {'ok': True})
        return self._send(404, {'error': 'no'})


@pytest.fixture(scope='module', autouse=True)
def stub():
    srv = HTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    V.configure(url=f'http://127.0.0.1:{srv.server_address[1]}', model='seeing-model',
                enabled=True)
    V._TOKEN.update(token=None, at=0, address=None)
    yield srv
    srv.shutdown()


@pytest.fixture(scope='module')
def photos():
    """One head-and-shoulders and one wider shot, in a fresh set."""
    s = E.new_set('venice-test')
    for i, (w, h, r) in enumerate([(1200, 1500, 260), (1200, 1500, 120)]):
        img = Image.new('RGB', (w, h), (90, 120, 160))
        d = ImageDraw.Draw(img)
        cx, cy = w // 2, h // 3
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(226, 190, 160))
        for ex in (-r // 2, r // 2):
            d.ellipse([cx + ex - r // 8, cy - r // 5, cx + ex + r // 8, cy], fill=(40, 40, 45))
        d.arc([cx - r // 2, cy + r // 8, cx + r // 2, cy + r // 1.4], 20, 160, fill=(120, 60, 60), width=8)
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=92)
        E.add(set_ref=s['id'], data=base64.b64encode(buf.getvalue()).decode(),
              name=f'p{i}.jpg')
    return E.get_set(s['id'])


# ── what leaves the box ──────────────────────────────────────────────────

def test_payload_is_a_downscaled_stripped_copy(photos):
    p = photos['photos'][0]
    raw, sent = V.payload(photos, p)
    img = Image.open(io.BytesIO(raw))
    assert max(img.size) <= V.SEND_PX          # never the original resolution
    assert img.format == 'JPEG'
    assert len(dict(img.getexif())) == 0       # no EXIF, so no GPS
    assert 'icc_profile' not in img.info
    assert sent['bytes'] == len(raw)
    assert 'gps' in sent['stripped']


def test_read_sends_the_copy_and_parses_fenced_json(photos):
    SEEN['chat'].clear()
    r = V.read(photos['id'], photo=photos['photos'][0]['id'], summary=False, force=True)
    assert len(SEEN['chat']) == 1
    sent = SEEN['chat'][0]
    assert sent['model'] == 'seeing-model'
    parts = sent['messages'][0]['content']
    url = next(c['image_url']['url'] for c in parts if c['type'] == 'image_url')
    assert url.startswith('data:image/jpeg;base64,')
    assert max(Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))).size) <= V.SEND_PX
    one = r['photos'][0]
    assert one['eyes'] == 'sunglasses' and one['shot'] == 'mirror-selfie'
    assert one['setting'] == 'gym locker room'


def test_every_send_is_authenticated(photos):
    assert SEEN['auth'], 'no request carried a header at all'
    assert all(a and a.startswith('Bearer ') for a in SEEN['auth'])
    tok = json.loads(base64.urlsafe_b64decode(
        SEEN['auth'][-1][7:] + '=' * (-len(SEEN['auth'][-1][7:]) % 4)))
    assert set(tok) >= {'data', 'time', 'key', 'signature'}
    assert tok['key'].startswith('0x') and len(tok['key']) == 42


def test_receipt_records_the_send(photos):
    log = V.sends(photos['id'])['log']
    assert log and log[-1]['outcome'] == 'read'
    assert log[-1]['bytes'] > 0 and log[-1]['model'] == 'seeing-model'
    assert log[-1]['url'].startswith('http://127.0.0.1:')


def test_a_refused_send_is_still_logged(photos):
    before = V.sends(photos['id'])['count']
    ANSWER['status'] = 402
    try:
        with pytest.raises(E.WingmanError):
            V.read(photos['id'], photo=photos['photos'][1]['id'], summary=False, force=True)
    finally:
        ANSWER['status'] = 200
    log = V.sends(photos['id'])['log']
    assert len(log) == before + 1, 'bytes left the process but no receipt was written'
    assert log[-1]['outcome'].startswith('failed')


# ── what the read is allowed to change ───────────────────────────────────

def test_flags_are_separate_from_the_measured_score(photos):
    pid = photos['photos'][0]['id']
    a = E.audit(photos['id'], photo=pid)['photos'][0]
    codes = {f['code'] for f in a['read_flags']}
    assert {'sunglasses', 'mirror-selfie', 'text-overlay'} <= codes
    assert all(f['source'] == 'read' for f in a['read_flags'])
    assert all(f['code'] not in codes for f in a['issues']), 'read flags leaked into issues'
    assert a['score'] == max(0, 100 - sum(i['cost'] for i in a['issues']))
    assert all('cost' not in f for f in a['read_flags'])


def test_audit_and_lineup_never_open_a_socket(photos, monkeypatch):
    """The promise is that only `read` sends. Break the transport and the
    measured verbs must still work."""
    def boom(*a, **k):
        raise AssertionError('audit/lineup tried to reach venice')
    monkeypatch.setattr(V, '_call', boom)
    assert E.audit(photos['id'])['photos']
    assert E.lineup(photos['id'], n=4)['slots']


def test_lineup_gaps_pick_up_the_repetition(photos):
    V.read(photos['id'], force=True, summary=False)       # both photos, same answer
    E.audit(photos['id'], force=True)
    gaps = E.lineup(photos['id'], n=4, force=True)['gaps']
    joined = ' '.join(gaps).lower()
    assert 'same' in joined, f'the set repeats itself and no gap says so: {gaps}'
    assert 'smile' in joined and 'camera' in joined


# ── the switch ───────────────────────────────────────────────────────────

def test_disabled_refuses_before_anything_is_encoded(photos):
    SEEN['chat'].clear()
    V.configure(enabled=False)
    try:
        with pytest.raises(E.WingmanError) as e:
            V.read(photos['id'], photo=photos['photos'][1]['id'], force=True)
        assert e.value.status == 403
        assert not SEEN['chat']
    finally:
        V.configure(enabled=True)


def test_env_off_overrides_the_config_file(photos, monkeypatch):
    monkeypatch.setenv('WINGMAN_VENICE', 'off')
    cfg = V.config()
    assert cfg['enabled'] is False and cfg['locked_by_env'] is True
    with pytest.raises(E.WingmanError):
        V.read(photos['id'], photo=photos['photos'][0]['id'], force=True)


def test_cached_reads_need_no_gateway(photos, monkeypatch):
    monkeypatch.setattr(V, '_call', lambda *a, **k: pytest.fail('went to the network'))
    assert V.cached(photos['id'])


# ── the rest of the surface ──────────────────────────────────────────────

def test_status_and_models(photos):
    s = V.status()
    assert s['reachable'] is True and s['address'] == '0xstub'
    assert s['sends']['count'] >= 1
    ids = [m['id'] for m in V.models()['models']]
    assert ids == ['seeing-model'], 'a model that cannot see was offered for a read'


def test_key_round_trip():
    V.set_key('sk-test-not-real')
    assert SEEN['key'] == 'sk-test-not-real'
    V.forget_key()
    assert SEEN['key'] is None


def test_mcp_tools_are_registered():
    names = {t['name'] for t in mcp.tool_list()}
    assert {'wingman_read', 'wingman_venice'} <= names
    got = mcp.call_tool('wingman_venice', {})
    assert got['reachable'] is True


def test_bare_json_and_junk(photos):
    ANSWER['wrap'] = False
    try:
        r = V.read(photos['id'], photo=photos['photos'][0]['id'], summary=False, force=True)
        assert r['photos'][0]['setting'] == 'gym locker room'
    finally:
        ANSWER['wrap'] = True
    good = ANSWER['json']
    ANSWER['json'] = 'no JSON here, sorry'
    try:
        # a named photo surfaces its own failure rather than burying it in a list
        with pytest.raises(E.WingmanError):
            V.read(photos['id'], photo=photos['photos'][0]['id'], summary=False, force=True)
        # across a set, one unparseable answer is an entry in `errors`
        r = V.read(photos['id'], summary=False, force=True)
        assert r['errors'] and not r['photos']
    finally:
        ANSWER['json'] = good


def test_unknown_enum_values_are_not_trusted(photos):
    ANSWER['json'] = {'expression': 'radiant', 'eyes': 'twinkling', 'eye_contact': '?',
                      'shot': 'drone', 'subject': 'maybe', 'people_visible': 'lots',
                      'setting': 'x' * 500, 'distractions': ['d'] * 20,
                      'reads_as': 'y' * 800}
    try:
        r = V.read(photos['id'], photo=photos['photos'][0]['id'], summary=False, force=True)
        one = r['photos'][0]
        assert one['expression'] in V.FIELDS['expression']
        assert one['eyes'] in V.FIELDS['eyes']
        assert one['people_visible'] is None          # "lots" is not a count
        assert len(one['setting']) <= 120 and len(one['reads_as']) <= 300
        assert len(one['distractions']) <= 6
    finally:
        ANSWER['json'] = {
            'expression': 'neutral', 'eyes': 'sunglasses', 'eye_contact': 'none',
            'shot': 'mirror-selfie', 'subject': 'one-clear-subject', 'people_visible': 1,
            'setting': 'gym locker room', 'outfit': 'grey hoodie', 'activity': 'none',
            'distractions': ['a toilet in the background'], 'text_overlay': True,
            'reads_as': 'A person who spends a lot of time at the gym.'}
