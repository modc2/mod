"""The shared core: stark.py and the Cairo signer agree with the fixture
the Cairo prover wrote (the same one every chain's native suite pins to)."""
import json
import os

import pytest

from ztensor_src import SRC
from ztensor_src.core import stark
from ztensor_src.core.util import PRIME, felt

FX = json.load(open(os.path.join(
    SRC, 'networks', 'near', 'contract', 'tests', 'fixture.json')))
I = lambda v: int(v, 16) if isinstance(v, str) else int(v)
HAVE_SCARB = os.path.exists(os.path.expanduser('~/.local/bin/scarb'))


def _sig():
    s = FX['sig']
    return ([(I(p['x']), I(p['y'])) for p in s['ring']], I(s['c0']),
            [I(x) for x in s['s']], (I(s['image']['x']), I(s['image']['y'])))


def test_felt_forms():
    assert felt(5) == felt('5') == felt('0x5') == 5
    assert felt(PRIME + 1) == 1
    assert felt('hello') == felt('hello') != felt('hellp')
    with pytest.raises(ValueError):
        felt(True)


def test_keys_match_cairo():
    for k in FX['keys']:
        pub = stark.mul(k['secret'], stark.G)
        assert pub == (I(k['x']), I(k['y']))
        assert stark.key_id(pub) == I(k['key_id'])


def test_hash_to_point_matches_cairo():
    for row in FX['hash_to_point']:
        hp, _ = stark.hash_to_point(I(row['scope']))
        assert hp == (I(row['x']), I(row['y'])) and stark.on_curve(hp)


def test_cairo_signature_verifies_in_python():
    ring, c0, s, image = _sig()
    tr = stark.trace(ring, I(FX['scope_mine']['scope']), I(FX['msg']), c0, s,
                     image)
    assert len(tr['c']) == len(ring)


def test_tampered_signature_rejected():
    ring, c0, s, image = _sig()
    s[0] = (s[0] + 1) % stark.P
    with pytest.raises(ValueError):
        stark.trace(ring, I(FX['scope_mine']['scope']), I(FX['msg']), c0, s,
                    image)


@pytest.mark.skipif(not HAVE_SCARB, reason='no scarb')
def test_prover_roundtrip(tmp_path):
    """keygen -> sign -> verify through `scarb cairo-run`, on a scratch
    keystore (never data/keys.json)."""
    from ztensor_src.core.signer import Signer
    sg = Signer(str(tmp_path / 'keys.json'))
    keys = [sg.keygen(f'k{i}', secret=s) for i, s in enumerate((11, 22, 33))]
    assert keys[0]['x'] == FX['keys'][0]['x']
    ring = [{'x': k['x'], 'y': k['y']} for k in keys]
    sig = sg.sign('k1', 777, 4242, ring)
    assert sg.verify(777, 4242, sig)
    assert not sg.verify(777, 4243, sig)
    # linkability: same key + scope -> same tag, other scope -> other tag
    assert I(sig['tag']) == sg.tag('k1', 777) != sg.tag('k1', 778)
    assert oct(os.stat(tmp_path / 'keys.json').st_mode)[-3:] == '600'



def test_native_signer_verifies_cairo_fixture():
    """core/lsag.py accepts the Cairo prover's recorded signature."""
    from ztensor_src.core import lsag
    ring, c0, s, image = _sig()
    sc, msg = I(FX['scope_mine']['scope']), I(FX['msg'])
    assert lsag.verify(ring, sc, msg, c0, s, image)
    assert not lsag.verify(ring, sc, msg + 1, c0, s, image)


@pytest.mark.skipif(not HAVE_SCARB, reason='no scarb')
def test_native_signer_bit_identical_to_cairo():
    """Same secret/alpha/fill -> the exact felts `scarb cairo-run` prints."""
    from ztensor_src.core import lsag
    from ztensor_src.core.signer import Signer
    sg = Signer('/nonexistent/keys.json')
    xs, alpha, fill = [11, 22, 33, 44], 5555, [7, 8, 9, 10]
    pts = [lsag.public_key(x) for x in xs]
    args = [2, 777, 4242, 33, alpha, 4] + [c for p in pts for c in p] + fill
    want = sg.prove(args)
    ring = lsag.sort_ring(pts)
    c0, s, img = lsag.sign(ring, ring.index(pts[2]), 33, 777, 4242, alpha, fill)
    assert [4, *[c for p in ring for c in p], c0, *s, *img,
            lsag.key_id(img)] == want
    assert sg.prove([5, 2, 9, 3, 55]) == [lsag.scope(2, 9, 3, 55)]
