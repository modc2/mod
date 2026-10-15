import base64, json, os, shutil, sys, time
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
sys.path.insert(0, SRC)
from passport import Passport  # noqa: E402

ACCT = Account.create()


def tok(data='agent', t=None, acct=ACCT, key=None):
    t = str(int(time.time())) if t is None else t
    msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    sig = sig if sig.startswith('0x') else '0x' + sig
    env = {'data': data, 'time': t, 'key': key or acct.address.lower(), 'signature': sig}
    return base64.urlsafe_b64encode(json.dumps(env).encode()).decode().rstrip('=')


@pytest.fixture
def p(tmp_path):
    shutil.copy(os.path.join(os.path.dirname(SRC), 'protocol.json'), tmp_path / 'protocol.json')
    shutil.copy(os.path.join(SRC, 'schemes.py'), tmp_path / 'schemes.py')
    return Passport(str(tmp_path / 'protocol.json'), str(tmp_path / 'schemes.py'))


def test_canonical_token(p):
    v = p.verify(tok())
    assert v == {'address': ACCT.address.lower(), 'scheme': 'mod', 'time': v['time']}


def test_legacy_dict_data_float_time(p):
    assert p.address(tok(data={'scope': 'agent'}, t=str(time.time()))) == ACCT.address.lower()


def test_rejects_stale_forged_and_garbage(p):
    assert p.address(tok(t=str(int(time.time()) - 90000))) is None
    other = Account.create()
    assert p.address(tok(key=other.address.lower())) is None   # signed by someone else
    assert p.address('nope') is None and p.address('') is None


def test_build_shape_detected(p):
    s = p.schemes()
    assert s.detect('0x' + 'a' * 40 + ':1760000000:' + 'f' * 64) == 'build'
    assert s.detect('0x' + 'a' * 40 + ':1760000000:ho:' + 'f' * 64) == 'build'
    assert s.detect(tok()) == 'mod'


def test_protocol_change_is_live(p):
    t = tok()
    assert p.address(t)
    proto = json.load(open(p.protocol_path))
    proto['schemes']['mod']['enabled'] = False
    time.sleep(0.01)
    json.dump(proto, open(p.protocol_path, 'w'))
    os.utime(p.protocol_path, (time.time() + 1, time.time() + 1))
    with pytest.raises(ValueError, match='not accepted'):
        p.verify(t)                     # same instance, no restart


def test_schemes_edit_is_live_and_broken_edit_keeps_last_good(p):
    t = tok()
    assert p.address(t)
    src = open(p.schemes_path).read()
    open(p.schemes_path, 'w').write(src + "\n\ndef verify(token, proto):\n    raise ValueError('closed for maintenance')\n")
    os.utime(p.schemes_path, (time.time() + 1, time.time() + 1))
    with pytest.raises(ValueError, match='maintenance'):
        p.verify(t)
    open(p.schemes_path, 'w').write('this is not python(')
    os.utime(p.schemes_path, (time.time() + 2, time.time() + 2))
    with pytest.raises(ValueError, match='maintenance'):   # last good verifier stands
        p.verify(t)


def test_role_reads_off_tree_owner(p, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    os.makedirs(tmp_path / '.mod' / 'zzmod')
    json.dump({'owner': ACCT.address}, open(tmp_path / '.mod' / 'zzmod' / 'owner.json', 'w'))
    r = p.role(tok(), 'zzmod')
    assert r['is_owner'] and r['address'] == ACCT.address.lower()
    assert not p.role(tok(acct=Account.create()), 'zzmod')['is_owner']
