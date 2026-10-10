"""Offline tests: fake bt, temp root — no network, no crontab."""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('subnets_mod', os.path.join(HERE, 'mod.py'))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

ROWS = [{'netuid': 0, 'name': 'root', 'symbol': 'T', 'price': 1.0, 'owner': 'A'},
        {'netuid': 7, 'name': 'Seven', 'symbol': 's', 'price': 0.5, 'owner': 'B',
         'description': 'does things.', 'github': 'https://github.com/x/y', 'url': 'seven.ai'}]


def fake(rows):
    def call(tool, timeout=None, **a):
        if tool == 'bt_screener':
            return {'rows': [dict(r) for r in rows], 'block': 100}
        if tool == 'bt_trades':
            return {'summary': {'trades': 3, 'buys': 2, 'sells': 1, 'buy_tao': 1, 'sell_tao': 1, 'net_tao': 0, 'traders': 2}}
        if tool == 'bt_news':
            return {'items': [
                {'ts': 1790000000, 'kind': 'news', 'title': 'Seven | ships', 'url': 'u', 'publisher': 'p'},
                {'ts': 1790000001, 'kind': 'news', 'title': 'No link story', 'url': None, 'publisher': 'q'},
            ]}
    return call


def run(tmp, monkeypatch, rows, **kw):
    monkeypatch.setattr(M, 'bt_call', fake(rows))
    monkeypatch.setattr(M, 'STATE', str(tmp / 'state'))
    monkeypatch.setattr(M, 'LOCK', str(tmp / 'state' / 'lock'))
    root = tmp / 'root'; root.mkdir(exist_ok=True)
    return M.Mod(root=str(root)).sync(verbose=False, **kw), root


def test_generates_every_subnet(tmp_path, monkeypatch):
    out, root = run(tmp_path, monkeypatch, ROWS)
    assert out['new'] == 2 and not out['errors']
    cfg = json.load(open(root / 'sn7' / 'config.json'))
    assert cfg['name'] == 'sn7' and cfg['netuid'] == 7 and cfg['active']
    assert 'does things —' not in cfg['description'] and '..' not in cfg['description']
    readme = open(root / 'sn7' / 'README.md').read()
    assert 'Seven \\| ships' in readme and 'https://seven.ai' in readme
    assert 'No link story' in readme and '(None)' not in readme
    assert open(root / 'sn7' / 'mod.py').read().startswith(M.GENERATED)


def test_unchanged_skips_and_owned_mod_py_kept(tmp_path, monkeypatch):
    _, root = run(tmp_path, monkeypatch, ROWS)
    (root / 'sn7' / 'mod.py').write_text('# mine\n')
    out, _ = run(tmp_path, monkeypatch, ROWS)
    assert out['unchanged'] == 2
    ROWS2 = [ROWS[0], dict(ROWS[1], price=0.6)]
    out, _ = run(tmp_path, monkeypatch, ROWS2)
    assert out['written'] == 1
    assert (root / 'sn7' / 'mod.py').read_text() == '# mine\n'


def test_dereg_and_reregistration(tmp_path, monkeypatch):
    _, root = run(tmp_path, monkeypatch, ROWS)
    out, _ = run(tmp_path, monkeypatch, ROWS[:1])
    assert out['inactive'] == 1
    assert json.load(open(root / 'sn7' / 'config.json'))['active'] is False
    assert 'Inactive' in open(root / 'sn7' / 'README.md').read()
    run(tmp_path, monkeypatch, [ROWS[0], dict(ROWS[1], owner='C', name='New')])
    cfg = json.load(open(root / 'sn7' / 'config.json'))
    assert cfg['active'] and cfg['owner'] == 'C' and cfg['previous'][0]['owner'] == 'B'


def test_empty_screener_refuses(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(RuntimeError):
        run(tmp_path, monkeypatch, [])


def test_sanitize_url_strips_duplicate_protocol(tmp_path, monkeypatch):
    rows = ROWS + [{'netuid': 116, 'name': 'Carb', 'symbol': 'C', 'price': 0.1, 'owner': 'D',
                    'url': 'https://https://example.com', 'github': 'http://https://github.com/x/y'}]
    _, root = run(tmp_path, monkeypatch, rows)
    cfg = json.load(open(root / 'sn116' / 'config.json'))
    assert cfg['url'] == 'https://example.com'
    assert cfg['github'] == 'https://github.com/x/y'
    readme = open(root / 'sn116' / 'README.md').read()
    assert 'https://https://' not in readme


def test_child_shim_falls_back_to_snapshot(tmp_path, monkeypatch):
    _, root = run(tmp_path, monkeypatch, ROWS)
    sys.modules.pop('_subnets_base', None)
    (root / 'base.py').write_text(open(os.path.join(HERE, 'base.py')).read())
    s = importlib.util.spec_from_file_location('c', root / 'sn7' / 'mod.py')
    c = importlib.util.module_from_spec(s); s.loader.exec_module(c)
    monkeypatch.setenv('BT_URL', 'http://127.0.0.1:9')
    mod = c.Mod()
    c._b.BT_URL = 'http://127.0.0.1:9'
    info = mod.info()
    assert info['stale'] and info['netuid'] == 7 and info['name'] == 'Seven'
