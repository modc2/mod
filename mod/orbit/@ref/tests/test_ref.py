"""Offline tests for @ref: pure orientation math + a store on a temp DB."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import orientation as ori  # noqa: E402


def test_wrap_and_normalize():
    assert ori.wrap180(270) == -90
    assert ori.wrap180(-270) == 90
    assert ori.wrap180(180) == 180
    y, p, r = ori.normalize(370, 120, -540)
    assert (y, p, r) == (10, 90, 180)


def test_distance_identity_and_symmetry():
    a, b = (-45, 15, 0), (30, -10, 5)
    assert ori.distance(a, a) == 0
    assert abs(ori.distance(a, b) - ori.distance(b, a)) < 1e-9


def test_distance_orders_by_closeness():
    target = (-45, 0, 0)          # 3/4 left
    near = (-40, 5, 0)
    far = (90, 0, 0)              # right profile
    assert ori.distance(target, near) < ori.distance(target, far)


def test_yaw_wraps_across_the_back():
    # 175 and -175 face nearly the same way; the seam must not split them.
    assert ori.distance((175, 0, 0), (-175, 0, 0)) < 15


def test_roll_is_discounted():
    tilt_only = ori.distance((0, 0, 0), (0, 0, 40), roll_weight=0.5)
    turn_only = ori.distance((0, 0, 0), (40, 0, 0), roll_weight=0.5)
    assert tilt_only < turn_only


def test_describe_names_the_classic_views():
    assert ori.describe(0, 0, 0) == 'front'
    assert '3/4 left' in ori.describe(-45, 0, 0)
    assert 'profile right' in ori.describe(90, 0, 0)
    assert ori.describe(180, 0, 0) == 'back'
    assert 'from above' in ori.describe(0, 30, 0)
    assert 'tilted left' in ori.describe(0, 0, -30)


def _store(tmp):
    os.environ['REF_DATA_DIR'] = tmp
    for m in ('store',):
        sys.modules.pop(m, None)
    import store
    return store


def _fake_png(folder, name):
    p = os.path.join(folder, name)
    with open(p, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\nfake')
    return p


def test_store_roundtrip_and_gimbal_query():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        imgs = os.path.join(tmp, 'imgs')
        os.makedirs(imgs)
        left = _fake_png(imgs, 'left34.png')
        right = _fake_png(imgs, 'right_profile.png')
        blank = _fake_png(imgs, 'unposed.png')

        out = store.scan(imgs, tags=['figure'])
        assert out['indexed'] == 3 and out['untagged'] == 3

        store.set_pose(store.ref_id(left), -45, 10, 0)
        store.set_pose(store.ref_id(right), 90, 0, 0)

        q = store.query(-40, 5, 0, tolerance=45)
        ids = [r['id'] for r in q['refs']]
        assert ids == [store.ref_id(left)]          # profile is out of tolerance
        assert q['refs'][0]['pose'].startswith('3/4 left')

        u = store.untagged()
        assert [r['id'] for r in u['refs']] == [store.ref_id(blank)]

        s = store.stats()
        assert s['total'] == 3 and s['tagged'] == 2

        store.remove(store.ref_id(blank))
        assert store.stats()['total'] == 2


def test_query_filters_by_tag():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        hand = _fake_png(tmp, 'hand.png')
        head = _fake_png(tmp, 'head.png')
        store.add(hand, 0, 0, 0, tags=['hands'])
        store.add(head, 0, 0, 0, tags=['head'])
        q = store.query(0, 0, 0, tags=['hands'])
        assert [r['id'] for r in q['refs']] == [store.ref_id(hand)]
