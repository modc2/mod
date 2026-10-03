"""Offline tests — no server, no network. The store writes to a temp dir
via FABRICAS_DATA, so a test run never touches the real shop's books."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atelier  # noqa: E402
import store  # noqa: E402
from mod import Mod  # noqa: E402


@pytest.fixture(autouse=True)
def temp_books(tmp_path, monkeypatch):
    monkeypatch.setenv('FABRICAS_DATA', str(tmp_path))


DESIGN = {
    'garment': 'tee', 'color': 'indigo', 'name': 'night shift',
    'layers': [
        {'type': 'text', 'text': 'MOD', 'font': 'display', 'ink': 'gold',
         'x': 50, 'y': 30, 'size': 60},
        {'type': 'shape', 'shape': 'bolt', 'ink': 'white',
         'x': 50, 'y': 62, 'size': 45, 'rotate': 12},
    ],
}


# ── the cutting table ────────────────────────────────────────────────

def test_catalog_is_coherent():
    cat = atelier.catalog()
    for name, g in cat['garments'].items():
        assert g['base_price'] > 0 and g['sizes'] and g['path']
        pa = g['print']
        # print area sits inside the 400x400 viewBox
        assert 0 < pa['x'] and pa['x'] + pa['w'] < 400
        assert 0 < pa['y'] and pa['y'] + pa['h'] < 400
    assert cat['palette'] and cat['inks'] and cat['shapes'] and cat['fonts']


def test_validate_normalizes_and_clamps():
    d = atelier.validate({'garment': 'tee', 'layers': [
        {'type': 'shape', 'shape': 'star', 'x': 250, 'y': -10, 'size': 3}]})
    layer = d['layers'][0]
    assert layer['x'] == 100 and layer['y'] == 0 and layer['size'] == 5
    assert d['name'] == 'untitled' and d['color'] == 'bone'


@pytest.mark.parametrize('bad, reason', [
    ({'garment': 'gown'}, 'no cut'),
    ({'layers': [{'type': 'hologram'}]}, 'text or shape'),
    ({'layers': [{'type': 'text', 'text': ''}]}, 'no text'),
    ({'layers': [{'type': 'text', 'text': 'x' * 99}]}, 'caps'),
    ({'layers': [{'type': 'shape', 'shape': 'skull'}]}, 'no mark'),
    ({'layers': [{'type': 'text', 'text': 'hi', 'font': 'wingdings'}]}, 'no font'),
    ({'color': 'chartreuse'}, 'unknown color'),
    ({'layers': [{'type': 'shape'}] * 9}, 'layers'),
])
def test_validate_rejects_with_reason(bad, reason):
    with pytest.raises(ValueError, match=reason):
        atelier.validate(bad)


def test_quote_math():
    q = atelier.quote(DESIGN, size='M', qty=1)
    assert q['unit'] == 18.0 + 2.5 + 2.0 == q['total']
    q = atelier.quote(DESIGN, size='XXL', qty=10)
    unit = 18.0 + 2.0 + 4.5
    assert q['unit'] == unit
    assert q['total'] == round(unit * 10 * 0.9, 2)
    with pytest.raises(ValueError, match='comes in'):
        atelier.quote({'garment': 'tote'}, size='XL')


def test_render_is_svg_and_escapes():
    svg = atelier.render({'garment': 'hoodie', 'color': 'rust', 'layers': [
        {'type': 'text', 'text': '<b>&"cut"'}]})
    assert svg.startswith('<svg') and svg.endswith('</svg>')
    assert '&lt;b&gt;' in svg and '<b>' not in svg
    assert atelier.PALETTE['rust'] in svg


def test_render_accepts_raw_hex():
    assert '#abc' in atelier.render({'garment': 'cap', 'color': '#abc'})


# ── the books ────────────────────────────────────────────────────────

def test_design_roundtrip_is_content_addressed():
    clean = atelier.validate(DESIGN)
    a = store.save_design(clean, author='pinly')
    b = store.save_design(clean, author='someone-else')
    assert a == b  # same cloth, same id
    got = store.get_design(a)
    assert got['design'] == clean and got['author'] == 'pinly'
    with pytest.raises(LookupError):
        store.get_design('nope')


def test_gallery_and_author_filters():
    clean = atelier.validate(DESIGN)
    store.save_design(clean, author='pinly', public=True)
    store.save_design(atelier.validate({'garment': 'cap'}),
                      author='pinly', public=False)
    assert len(store.list_designs(public=True)) == 1
    assert len(store.list_designs(author='pinly')) == 2


def test_order_rail():
    did = store.save_design(atelier.validate(DESIGN))
    q = atelier.quote(DESIGN, 'L', 2)
    oid = store.save_order(did, 'L', 2, q, author='pinly')
    order = store.get_order(oid)
    assert order['status'] == 'placed' and order['quote']['total'] == q['total']

    for expect in ['cutting', 'printing', 'sewing', 'ready', 'shipped']:
        order = store.advance_order(oid)
        assert order['status'] == expect
    assert [h['status'] for h in order['log']] == \
        ['placed', 'cutting', 'printing', 'sewing', 'ready', 'shipped']
    with pytest.raises(ValueError, match='ends there'):
        store.advance_order(oid)


def test_order_rail_no_skips_but_cancel_ok():
    did = store.save_design(atelier.validate(DESIGN))
    oid = store.save_order(did, 'M', 1, atelier.quote(DESIGN))
    with pytest.raises(ValueError, match='skips'):
        store.advance_order(oid, 'shipped')
    assert store.advance_order(oid, 'cancelled')['status'] == 'cancelled'


def test_order_requires_saved_design():
    with pytest.raises(LookupError):
        store.save_order('ghost', 'M', 1, {})


# ── the anchor ───────────────────────────────────────────────────────

def test_mod_end_to_end():
    m = Mod()
    saved = m.save(design=json.dumps(DESIGN), author='pinly')
    assert saved['id'] and saved['design']['name'] == 'night shift'

    r = m.remix(id=saved['id'], author='guest')
    assert r['remix_of'] == saved['id']
    assert r['design']['name'].endswith('(remix)')

    order = m.order(id=saved['id'], size='L', qty=2, author='pinly')
    assert order['status'] == 'placed'
    advanced = m.advance(id=order['id'])
    assert advanced['status'] == 'cutting'

    assert m.render(id=saved['id']).startswith('<svg')
    assert m.health()['ok'] and m.health()['orders'] == 1
    assert m.gallery()[0]['id'] == saved['id']


def test_mod_answers_bad_input_with_reasons():
    m = Mod()
    with pytest.raises(ValueError, match='JSON'):
        m.save(design='{not json')
    with pytest.raises(ValueError, match='order what'):
        m.order()
    with pytest.raises(LookupError):
        m.design(id='ghost')
