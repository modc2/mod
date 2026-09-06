"""wingman tests — synthetic images, an isolated state dir, no network needed
for anything but the face model (which is copied in if the box has it)."""

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(HERE)

TMP = tempfile.mkdtemp(prefix='wingman-test-')
os.environ['WINGMAN_DIR'] = TMP
# reuse the real detector if the box already fetched it — the tests must not
# need the network
_real = os.path.expanduser('~/.mod/wingman/models/version-RFB-320.onnx')
if os.path.exists(_real):
    os.makedirs(os.path.join(TMP, 'models'), exist_ok=True)
    shutil.copy(_real, os.path.join(TMP, 'models', 'version-RFB-320.onnx'))

import engine as E                                          # noqa: E402
import mcp                                                  # noqa: E402
import api                                                  # noqa: E402


def _face_like(w=1200, h=1500, cx=None, cy=None, r=None, bg=(90, 120, 160), n=1,
               dark=False, blur=0):
    """A cartoon head on a plain background. Skin-coloured disc with eyes and a
    mouth — enough for the skin fallback, and usually for UltraFace too."""
    img = Image.new('RGB', (w, h), bg)
    d = ImageDraw.Draw(img)
    r = r or h // 6
    for i in range(n):
        x = (cx or w // 2) + (i - (n - 1) / 2) * r * 2.6
        y = cy or h // 2.6
        d.ellipse([x - r, y - r * 1.25, x + r, y + r * 1.25], fill=(224, 172, 138))
        d.ellipse([x - r * .45, y - r * .35, x - r * .2, y - r * .1], fill=(40, 30, 30))
        d.ellipse([x + r * .2, y - r * .35, x + r * .45, y - r * .1], fill=(40, 30, 30))
        d.arc([x - r * .4, y + r * .2, x + r * .4, y + r * .8], 10, 170, fill=(120, 60, 60), width=6)
        d.rectangle([x - r * 1.6, y + r * 1.3, x + r * 1.6, h], fill=(60, 60, 90))
    # texture so the frame has some sharpness to measure
    noise = (np.random.default_rng(1).random((h, w, 1)) * 24).astype(np.uint8)
    arr = np.clip(np.asarray(img).astype(np.int16) + noise - 12, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if dark:
        img = img.point(lambda v: v // 5)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


def _bytes(img, fmt='JPEG', **kw):
    b = io.BytesIO()
    img.save(b, fmt, **kw)
    return b.getvalue()


def _b64(img):
    import base64
    return base64.b64encode(_bytes(img)).decode()


@pytest.fixture(scope='module')
def set_id():
    s = E.new_set('pytest')
    E.add(s['id'], files=[
        {'name': 'good.jpg', 'data': _bytes(_face_like())},
        {'name': 'dup.jpg', 'data': _bytes(_face_like().resize((1180, 1475)))},
        {'name': 'dark.jpg', 'data': _bytes(_face_like(dark=True))},
        {'name': 'blur.jpg', 'data': _bytes(_face_like(blur=8))},
        {'name': 'tiny.jpg', 'data': _bytes(_face_like(400, 500, r=70))},
        {'name': 'wide.jpg', 'data': _bytes(_face_like(2000, 1200, r=60, cx=300, cy=400))},
        {'name': 'scene.png', 'data': _bytes(Image.effect_noise((900, 1200), 60).convert('RGB'), 'PNG')},
        {'name': 'group.jpg', 'data': _bytes(_face_like(2000, 1400, r=150, n=3))},
    ])
    return s['id']


def test_health_and_presets():
    h = E.health()
    assert h['ok'] and h['detector']['name'] in ('ultraface-rfb-320', 'skin-heuristic')
    assert set(E.PRESETS) >= {'tinder', 'hinge', 'bumble', 'square', 'story'}
    for p in E.PRESETS.values():
        rw, rh = p['ratio']
        assert abs(p['size'][0] / p['size'][1] - rw / rh) < 0.01


def test_add_dedupes_exact_and_rejects_junk(set_id):
    meta = E.get_set(set_id)
    assert len(meta['photos']) == 8
    r = E.add(set_id, files=[{'name': 'again.jpg', 'data': _bytes(_face_like())}])
    assert r['added'] == [] and r['skipped'][0]['why'] == 'already in the set'
    r = E.add(set_id, data=_b64(_face_like()) [:40] + 'AAAA', name='junk.jpg')
    assert r['skipped'] and 'not an image' in r['skipped'][0]['why']


def test_audit_measures_what_it_claims(set_id):
    a = {p['name']: p for p in E.audit(set_id)['photos']}
    assert a['good.jpg']['face_count'] >= 1
    assert a['good.jpg']['score'] > a['blur.jpg']['score']
    assert a['good.jpg']['score'] > a['dark.jpg']['score']
    assert any(i['code'] == 'blurry' for i in a['blur.jpg']['issues'])
    assert any(i['code'] == 'dark' for i in a['dark.jpg']['issues'])
    assert any(i['code'] == 'low-res' for i in a['tiny.jpg']['issues'])
    assert a['scene.png']['role'] == 'scene' and a['scene.png']['face_count'] == 0
    assert 0 <= a['good.jpg']['score'] <= 100
    for p in a.values():
        assert p['verdict']
        assert p['score'] == max(0, 100 - sum(i['cost'] for i in p['issues']))


def test_group_is_seen_as_group(set_id):
    a = {p['name']: p for p in E.audit(set_id)['photos']}
    if a['group.jpg']['face_count'] >= 2:
        assert a['group.jpg']['role'] == 'group'
        assert not a['group.jpg']['lead_ok']


def test_lineup_rules(set_id):
    lu = E.lineup(set_id, n=6)
    names = [s['name'] for s in lu['slots']]
    left = {l['name']: l['why'] for l in lu['left_out']}
    assert names, 'a lineup with photos in the set cannot be empty'
    assert names[0] != 'blur.jpg' and names[0] != 'dark.jpg'
    # exactly one of the near-identical pair survives
    assert ('dup.jpg' in left) != ('good.jpg' in left) or ('dup.jpg' in names) != ('good.jpg' in names)
    assert any('near-duplicate' in w for w in left.values())
    # group never in the first three
    for s in lu['slots'][:3]:
        assert s['role'] != 'group'
    assert lu['slots'][0]['slot'] == 1
    assert isinstance(lu['gaps'], list)


def test_render_crops_to_ratio_and_strips_metadata(set_id):
    lu = E.lineup(set_id, n=3)
    pid = lu['slots'][0]['photo']
    for preset in ('tinder', 'bumble', 'square', 'story'):
        r = E.render(set_id, photo=pid, preset=preset)['rendered'][0]
        spec = E.PRESETS[preset]
        assert r['size'] == spec['size']
        x0, y0, x1, y1 = r['crop']
        assert abs((x1 - x0) / (y1 - y0) - spec['ratio'][0] / spec['ratio'][1]) < 0.02
        im = Image.open(E.rendered_path(set_id, pid, preset))
        assert im.size == tuple(spec['size'])
        assert not im.info.get('exif') and not im.info.get('icc_profile')
        assert 'exif' in r['stripped'] and 'gps' in r['stripped']
    r2 = E.render(set_id, photo=pid, preset='tinder')['rendered'][0]
    assert r2.get('cached') is True
    custom = E.render(set_id, photo=pid, ratio='2:3', size='800x1200')['rendered'][0]
    assert custom['size'] == [800, 1200]


def test_face_aware_crop_keeps_the_face(set_id):
    a = {p['name']: p for p in E.audit(set_id)['photos']}
    wide = a['wide.jpg']
    if not wide['faces']:
        pytest.skip('detector found no face in the synthetic wide shot')
    r = E.render(set_id, photo=wide['photo'], preset='tinder')['rendered'][0]
    fx0, fy0, fx1, fy1 = wide['faces'][0]['box']
    cx0, cy0, cx1, cy1 = r['crop']
    assert cx0 <= fx0 and cx1 >= fx1 and cy0 <= fy0 and cy1 >= fy1
    assert r['crop_how'].startswith('face')


def test_polish_off_and_zoom_none(set_id):
    lu = E.lineup(set_id, n=1)
    pid = lu['slots'][0]['photo']
    r = E.render(set_id, photo=pid, preset='hinge', polish_mode='none', zoom='none')['rendered'][0]
    assert r['polish'] == [] and 'largest crop' in r['crop_how']


def test_export_zip_in_slot_order(set_id):
    x = E.export(set_id, preset='hinge', n=4)
    assert os.path.exists(x['zip'])
    with zipfile.ZipFile(x['zip']) as z:
        names = sorted(z.namelist())
        jpgs = [n for n in names if n.endswith('.jpg')]
        assert jpgs == sorted(jpgs) and jpgs[0].startswith('01-')
        rep = json.loads(z.read('report.json'))
        assert rep['preset']['name'] == 'hinge' and len(rep['renders']) == len(jpgs)
    assert E.zip_path(set_id, 'hinge') == x['zip']


def test_remove_and_delete(set_id):
    s = E.new_set('throwaway')
    E.add(s['id'], files=[{'name': 'a.jpg', 'data': _bytes(_face_like())}])
    pid = E.get_set(s['id'])['photos'][0]['id']
    assert E.remove(s['id'], pid)['photos'] == 0
    assert E.delete_set(s['id'])['deleted'] == s['id']
    with pytest.raises(E.WingmanError):
        E.get_set(s['id'])


def test_mcp_transport_and_tools(set_id):
    init = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['serverInfo']['name'] == 'wingman'
    tools = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert len(tools) == 12 and all(t['name'].startswith('wingman_') for t in tools)
    r = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                    'params': {'name': 'wingman_lineup', 'arguments': {'set': set_id, 'n': 3}}})
    assert not r['result']['isError'] and len(r['result']['structuredContent']['slots']) <= 3
    r = mcp.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                    'params': {'name': 'wingman_audit', 'arguments': {}}})
    assert r['result']['isError'] and 'needs set' in r['result']['content'][0]['text']
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_rest_routes_share_the_engine(set_id):
    assert api.route('GET', '/', '', {})['name'] == 'wingman'
    lu = api.route('GET', '/lineup', f'set={set_id}&n=2', {})
    assert len(lu['slots']) <= 2
    with pytest.raises(E.WingmanError) as e:
        api.route('GET', '/sets', '', {}, trusted=False)
    assert e.value.status == 403
    assert api.route('GET', '/sets', '', {}, trusted=True)['count'] >= 1
    with pytest.raises(E.WingmanError) as e:
        api.route('GET', '/nope', '', {})
    assert e.value.status == 404


def test_multipart_parser():
    body = (b'--xyz\r\ncontent-disposition: form-data; name="set"\r\n\r\nabc\r\n'
            b'--xyz\r\ncontent-disposition: form-data; name="files"; filename="p.jpg"\r\n'
            b'content-type: image/jpeg\r\n\r\n' + _bytes(_face_like(300, 300, r=40)) +
            b'\r\n--xyz--\r\n')
    files, fields = api._multipart('multipart/form-data; boundary=xyz', body)
    assert fields == {'set': 'abc'} and files[0]['name'] == 'p.jpg'
    assert Image.open(io.BytesIO(files[0]['data'])).size == (300, 300)


def test_ratio_parsing():
    assert E._parse_ratio('4:5') == (4.0, 5.0)
    assert E._parse_ratio('1080x1350') == (1080.0, 1350.0)
    assert E._parse_ratio([9, 16]) == (9.0, 16.0)
    with pytest.raises(E.WingmanError):
        E.preset_spec('nope')
