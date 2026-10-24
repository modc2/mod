"""Offline tests: every one runs in a throwaway store under tmp_path and
never touches the network or the claudecode agent."""

import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import mcp_server  # noqa: E402
import rules  # noqa: E402
import store  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return store.Store(str(tmp_path / 'r.db'))


def _anchor(tmp_path):
    spec = importlib.util.spec_from_file_location('reviewer_anchor', os.path.join(MODULE_DIR, 'mod.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Mod(path=str(tmp_path / 'a.db'))


def _module(tmp_path, name='good', cfg=None, anchor=None, readme=True, tests=True):
    d = tmp_path / name
    d.mkdir()
    c = {'name': name, 'version': '0.1.0', 'description': 'x', 'anchor': 'mod.py',
         'port': 59990, 'urls': {'app': 'http://localhost:59990/'},
         'fns': ['info', 'health', 'go'], 'api_fns': ['info', 'go'],
         'endpoints': {'GET /go': 'go'}}
    if cfg is not None:
        c = cfg
    (d / 'config.json').write_text(json.dumps(c))
    (d / 'mod.py').write_text(anchor if anchor is not None else
                              'class Mod:\n    def info(self): pass\n    def health(self): pass\n    def go(self): pass\n')
    if readme:
        (d / 'README.md').write_text('# x\n')
    if tests:
        (d / 'tests').mkdir()
    return str(d)


def test_config_parses_and_matches():
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'reviewer' and cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 51430
    for fn in cfg['api_fns']:
        assert fn in cfg['fns']


def test_anchor_loads_by_path_and_reviews_itself(tmp_path):
    m = _anchor(tmp_path)
    assert m.health(probe_agent=False)['ok']
    r = m.review(target='reviewer')
    assert r['id'] and r['grade'] in 'ABCDF'
    assert not [f for f in r['findings'] if f['severity'] == 'high'], r['findings']
    assert m.get(r['id'])['id'] == r['id']
    assert m.list()['reviews'][0]['id'] == r['id']
    assert 'reviewer' in {t['name'] for t in m.targets('review')['targets']}
    # every declared fn is a Mod method
    with open(os.path.join(MODULE_DIR, 'config.json')) as f:
        for fn in json.load(f)['fns']:
            assert callable(getattr(m, fn)), fn


def test_clean_module_scores_a(tmp_path):
    findings, meta = rules.review_dir(_module(tmp_path))
    assert meta['kind'] == 'module' and meta['name'] == 'good'
    assert findings == [], findings
    assert rules.score(findings)['grade'] == 'A'


def test_protocol_findings(tmp_path):
    d = _module(tmp_path, 'bad', cfg={'name': 'other', 'fns': ['info', 'nope'], 'api_fns': ['ghost'], 'port': 59990},
                anchor='import sys\nsys.path.insert(0, "x")\nclass Mod:  # reviewer: ignore\n    def info(self): pass\n    def extra(self): pass\n',
                readme=False, tests=False)
    _module(tmp_path, 'twin')  # same port 59990
    findings, _ = rules.review_dir(d)
    ids = {f['rule'] for f in findings}
    for want in ('protocol.name_mismatch', 'protocol.config_field', 'protocol.fn_not_method',
                 'protocol.method_not_declared', 'protocol.api_fn_not_in_fns', 'protocol.no_info_health',
                 'protocol.port_collision', 'protocol.port_without_urls', 'protocol.readme_missing',
                 'protocol.tests_missing', 'fleet.sys_path_prepend'):
        assert want in ids, want
    assert rules.score(findings)['grade'] in 'DF'


def test_missing_config_and_anchor(tmp_path):
    d = tmp_path / 'empty'
    d.mkdir()
    (d / 'x.py').write_text('print(1)\n')
    findings, meta = rules.review_dir(str(d))
    ids = {f['rule'] for f in findings}
    assert 'protocol.config_missing' in ids and 'protocol.anchor_missing' in ids
    assert meta['kind'] == 'dir'


def test_line_rules_and_pragmas():
    # reviewer: off
    code = '\n'.join([
        'import subprocess',
        '"""',
        'docs may say pkill -f and git stash and shell=True without penalty',
        '"""',
        'subprocess.run("pkill -f node", shell=True)',
        'x = eval(data)',
        'try:',
        '    pass',
        'except:',
        '    pass',
        'os.system("git stash")  # reviewer: ignore',
        '# reviewer' + ': off',  # built in two pieces so the outer scan does not toggle
        'os.system("killall python")',
        '# reviewer' + ': on',
        'API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"',
        'EXAMPLE_KEY = "sk-ant-api03-example-abcdefghijklmnopqrstuvwxyz"',
        '# TODO: tidy',
        'return self._json({"error": "x"}, 500)',
    ])
    # reviewer: on
    fs = rules.review_code(code, 'x.py')[0]
    by = {}
    for f in fs:
        by.setdefault(f['rule'], []).append(f['line'])
    assert by['fleet.pattern_kill'] == [5]
    assert by['py.shell_true'] == [5]
    assert by['py.eval_exec'] == [6]
    assert by['py.bare_except'] == [9]
    assert 'fleet.git_stash' not in by
    assert by['fleet.secret_in_source'] == [15]
    assert by['hygiene.todo'] == [17]
    assert by['fleet.server_5xx'] == [18]
    assert 'py.syntax_error' not in by


def test_syntax_error_and_js_rules():
    fs = rules.review_code('def x(:\n', 'bad.py')[0]
    assert fs[0]['rule'] == 'py.syntax_error' and fs[0]['severity'] == 'high'
    # reviewer: off
    js = 'if (confirm("sure?")) alert(1);\nel.innerHTML = user;\nel.innerHTML = "<b>static</b>";\n'
    # reviewer: on
    by = {}
    for f in rules.review_code(js, 'app.js')[0]:
        by.setdefault(f['rule'], []).append(f['line'])
    assert by['fleet.native_dialog'] == [1]
    assert by['js.innerhtml_dynamic'] == [2]


def test_diff_reviews_added_lines_only():
    # reviewer: off
    diff = '\n'.join([
        'diff --git a/s.py b/s.py', '--- a/s.py', '+++ b/s.py', '@@ -10,3 +10,5 @@',
        ' keep', '-os.system("pkill -f old")', '+os.system("pkill -f new")', '+ok = 1', ' keep',
    ])
    # reviewer: on
    fs, meta = rules.review_diff(diff)
    assert meta['files'] == 1 and meta['added_lines'] == 2
    assert [(f['rule'], f['file'], f['line']) for f in fs] == [('fleet.pattern_kill', 's.py', 11)]
    fs, _ = rules.review_diff('--- a/x\n+++ b/x\n@@ -1 +1 @@\n-gone\n')
    assert fs[0]['rule'] == 'diff.no_added_lines'


def test_store_verdicts_rescore(db):
    fs = rules.review_code('os.system("pkill -f x")\n# TODO: y\n', 'z.py')[0]  # reviewer: ignore
    r = db.save('z.py', 'code', 'static', fs)
    assert r['score'] == 85 and r['grade'] == 'B'
    r = db.verdict(r['id'], 0, 'fixed', 'done')
    assert r['score'] == 100 and r['findings'][0]['verdict'] == 'fixed'
    assert db.list()[0]['score'] == 100
    r = db.verdict(r['id'], 0, 'open')
    assert r['score'] == 85
    with pytest.raises(ValueError):
        db.verdict(r['id'], 0, 'maybe')
    with pytest.raises(ValueError):
        db.verdict(r['id'], 9, 'fixed')
    assert db.delete(r['id'])['deleted'] and db.get(r['id']) is None
    assert db.stats()['reviews'] == 0


def test_agent_engine_unreachable_is_reported(tmp_path, monkeypatch):
    import agent
    monkeypatch.setattr(agent, 'AGENT_URL', 'http://127.0.0.1:1')
    m = _anchor(tmp_path)
    r = m.review(path=_module(tmp_path), engine='agent', timeout=30)
    assert r['engine'] == 'agent' and r['summary'] is None
    assert 'unreachable' in r['meta']['agent_error']
    with pytest.raises(ValueError):
        m.review(code='x = 1', engine='agent')
    assert agent.parse_score('blah\nScore: 87/100') == 87
    assert agent.parse_score('no score') is None


def test_mcp_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, '_mod', _anchor(tmp_path))
    init = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-03-26'}})
    assert init['result']['protocolVersion'] == '2025-03-26'
    assert mcp_server.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    names = {t['name'] for t in mcp_server.handle_message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']}
    assert {'reviewer_review', 'reviewer_get', 'reviewer_list', 'reviewer_rules', 'reviewer_targets', 'reviewer_status', 'reviewer_verdict'} <= names
    call = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                      'params': {'name': 'reviewer_review',
                                                 'arguments': {'code': 'os.system("killall x")', 'filename': 'a.py'}}})  # reviewer: ignore
    res = call['result']['structuredContent']
    assert not call['result']['isError'] and res['findings'][0]['rule'] == 'fleet.pattern_kill'
    # agent engine from a remote caller is refused
    rem = mcp_server.handle_message({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                     'params': {'name': 'reviewer_review', 'arguments': {'target': 'reviewer', 'engine': 'agent'}}},
                                    local=False)
    assert rem['result']['isError'] and 'loopback' in rem['result']['content'][0]['text']
    assert mcp_server.handle_message({'jsonrpc': '2.0', 'id': 5, 'method': 'nope'})['error']['code'] == -32601


def test_remote_callers_confined_to_fleet(tmp_path, monkeypatch):
    """Over HTTP/MCP a non-loopback caller may only review fleet modules;
    an arbitrary box path is refused before it is read."""
    import serve
    monkeypatch.setattr(serve, '_MOD', None, raising=False)
    m = serve.the_mod()
    secret = tmp_path / 'secret.py'
    secret.write_text('x = 1\n')
    with pytest.raises(PermissionError):
        serve.api('review', {'path': str(secret)}, local=False)
    with pytest.raises(PermissionError):
        serve.api('resolve', {'target': str(secret)}, local=False)
    # the same path is fine from the box itself
    assert serve.api('resolve', {'target': str(secret)}, local=True)['path'] == str(secret)
    # and a fleet module is fine from anywhere
    assert m.in_fleet(os.path.join(os.path.dirname(serve.MODULE_DIR), 'jokes'))
    r = serve.api('review', {'path': serve.MODULE_DIR, 'save': '0'}, local=False)
    assert 'score' in r
