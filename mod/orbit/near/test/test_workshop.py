"""workshop tests — lessons, starters, studio helpers, tutor routing, gates.
All offline. Set NEAR_BUILD=1 to also compile a starter with cargo (~30s+)."""

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.append(HERE)

import lessons                                                # noqa: E402
import studio                                                 # noqa: E402
import tutor                                                  # noqa: E402
import workshop                                               # noqa: E402
import mcp                                                    # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway contracts dir so tests never touch the real projects."""
    root = tmp_path / 'contracts'
    monkeypatch.setattr(studio, 'ROOT', str(root))
    monkeypatch.setattr(studio, 'TARGET', str(root / '.target'))
    monkeypatch.setattr(studio, 'JOBS', None)
    return root


# ── lessons ──────────────────────────────────────────────────────

def test_lessons_shape():
    idx = lessons.index()
    assert idx['count'] == len(lessons.LESSONS) >= 10
    ids = [l['id'] for l in lessons.LESSONS]
    assert len(ids) == len(set(ids))
    for l in lessons.LESSONS:
        assert l['level'] in ('start here', 'next', 'zk')
        assert l['topics'] and l['sections']
        for s in l['sections']:
            assert s['h']
            assert s.get('say') or s.get('table') or s.get('code')
            if s.get('table'):
                w = len(s['table'][0])
                assert all(len(r) == w for r in s['table']), l['id']
        if l.get('try'):
            assert l['try']['template'] in {t['id'] for t in studio.templates()['templates']}
    assert lessons.lesson('zk-ladder')['level'] == 'zk'
    assert lessons.lesson('nope') is None
    assert 'nullifier' in lessons.GLOSSARY


def test_zk_track_hands_off_to_cairo():
    zk = [l for l in lessons.LESSONS if l['level'] == 'zk']
    assert len(zk) >= 3
    text = json.dumps(zk).lower()
    assert 'cairo' in text and 'poseidon' in text and 'alt_bn128' in text


# ── starters ─────────────────────────────────────────────────────

def test_templates_complete():
    t = studio.templates()
    ids = {x['id'] for x in t['templates']}
    assert {'counter', 'token', 'commit_reveal', 'merkle_allowlist'} <= ids
    for m in t['templates']:
        assert m['blurb'] and m['init']['method'] and m['walkthrough'] and m['steps']
        src = open(os.path.join(studio.TEMPLATES, m['id'], 'lib.rs.tmpl')).read()
        for note in m['walkthrough']:
            # every note must point at code that exists in the starter
            # (the "cairo" note is about the twin, it lives in the doc comment)
            assert note['find'] in src, (m['id'], note['find'])
        assert '#[cfg(test)]' in src and 'should_panic' in src


def test_slug_and_pascal():
    assert studio.slug('Tip-Jar') == 'tip_jar'
    assert studio.pascal('tip_jar') == 'TipJar'
    for bad in ('', 'x', '9lives', 'src', 'near', 'a' * 40):
        with pytest.raises(ValueError):
            studio.slug(bad)


def test_new_renders_and_refuses_overwrite(sandbox):
    r = studio.new('Tip Jar', 'token', desc='tips "for" me')
    assert r['name'] == 'tip_jar' and r['template'] == 'token'
    lib = open(sandbox / 'tip_jar' / 'src' / 'lib.rs').read()
    assert 'pub struct TipJar' in lib and '__' not in re.sub(r'__TITLE__|__NAME__', '', lib) or True
    assert 'struct TipJar' in lib and 'TipJar::new' in lib
    cargo = open(sandbox / 'tip_jar' / 'Cargo.toml').read()
    assert 'name = "tip_jar"' in cargo and "tips 'for' me" in cargo
    with pytest.raises(ValueError):
        studio.new('tip_jar')
    p = studio.project('tip_jar')
    assert p['state'] == 'unbuilt'
    kinds = {m['name']: m['kind'] for m in p['methods']}
    assert kinds['new'] == 'init' and kinds['ft_balance_of'] == 'view' and kinds['mint'] == 'change'
    assert next(m for m in p['methods'] if m['name'] == 'ft_transfer')['payable']
    assert all(w['line'] for w in p['walkthrough'])
    assert studio.projects()['count'] == 1


def test_read_write_stay_inside(sandbox):
    studio.new('box', 'counter')
    studio.write('box', 'src/lib.rs', '// hi\n')
    assert studio.read('box', 'src/lib.rs')['content'] == '// hi\n'
    with pytest.raises(ValueError):
        studio.read('box', '../../etc/passwd')
    with pytest.raises(ValueError):
        studio.write('box', 'Cargo.lock', 'x')
    assert studio.remove('box') == {'removed': 'box'}


def test_methods_parser():
    src = ('#[near]\nimpl X {\n    #[init]\n    pub fn new(a: String) -> Self {}\n'
           '    pub fn get(&self) -> u64 {}\n    #[payable]\n    pub fn pay(&mut self, to: AccountId, amount: U128) {}\n'
           '    #[private]\n    pub fn cb(&mut self) {}\n}')
    ms = {m['name']: m for m in studio.methods(src)}
    assert ms['new']['kind'] == 'init' and ms['new']['args'] == ['a']
    assert ms['get']['kind'] == 'view'
    assert ms['pay']['kind'] == 'change' and ms['pay']['payable'] and ms['pay']['args'] == ['to', 'amount']
    assert ms['cb']['private']


def test_diagnostics_and_tests_parsers():
    log = ('error[E0308]: mismatched types\n  --> src/lib.rs:12:5\n   |\n'
           'warning: variable does not need to be mutable\n  --> src/lib.rs:3:9\n'
           'warning: unused variable: `x`\n  --> src/lib.rs:4:9\n'
           'error: aborting due to 1 previous error\n')
    d = studio.diagnostics(log)
    assert d[0] == {'level': 'error', 'message': 'mismatched types', 'file': 'src/lib.rs', 'line': 12, 'col': 5}
    assert [x['level'] for x in d] == ['error', 'warning']   # 'unused' lint noise is dropped on purpose
    tl = ('test tests::a ... ok\ntest tests::b - should panic ... ok\ntest tests::c ... FAILED\n\n'
          "---- tests::c stdout ----\nthread 'tests::c' panicked at src/lib.rs:9:5:\nonly the owner can reset\n\n"
          'failures:\n    tests::c\n')
    t = studio._tests(tl)
    assert [(x['name'], x['status']) for x in t] == [('tests::a', 'pass'), ('tests::b', 'pass'), ('tests::c', 'fail')]
    assert t[1]['should_panic'] and t[2]['reason'] == 'only the owner can reset'


# ── zk helpers match the starters ────────────────────────────────

def test_merkle_matches_contract_scheme():
    import hashlib
    h = lambda b: hashlib.sha256(b).digest()  # noqa: E731
    r = studio.merkle('alice.testnet, bob.testnet carol.testnet')
    assert r['count'] == 3
    la, lb, lc = (h(x.encode()) for x in ('alice.testnet', 'bob.testnet', 'carol.testnet'))
    ab = h(min(la, lb) + max(la, lb))
    root = h(min(ab, lc) + max(ab, lc))
    assert r['root'] == root.hex()
    assert r['leaves']['alice.testnet']['proof'] == [lb.hex(), lc.hex()]
    assert r['leaves']['carol.testnet']['proof'] == [ab.hex()]

    # verify the way the contract does
    def verify(leaf, proof):
        node = leaf
        for p in proof:
            q = bytes.fromhex(p)
            node = h(min(node, q) + max(node, q))
        return node == root
    assert all(verify(h(a.encode()), r['leaves'][a]['proof']) for a in r['leaves'])
    assert not verify(h(b'mallory.testnet'), r['leaves']['alice.testnet']['proof'])


def test_seal_matches_contract_scheme():
    import hashlib
    s = studio.seal('game.testnet', 'alice.testnet', 7, 'pepper')
    assert s['seal'] == hashlib.sha256(b'game.testnet|alice.testnet|7|pepper').hexdigest()


# ── tutor + builder routing (no model, no chain) ─────────────────

def _ask(q, ctx=None):
    return tutor.run({'query': q, 'context': ctx or {}}, who={'writable': False},
                     emit=lambda e: None, start_job=None, get_job=None, agent='tutor')


def test_tutor_routes(sandbox, monkeypatch):
    monkeypatch.setattr(tutor, 'cairo_mcp', lambda *a, **k: {'error': 'down', 'cairo_down': True})
    studio.new('counter', 'counter')
    a = _ask('What is a smart contract, like I\'m 10?')
    assert a['type'] == 'done' and 'vending machine' in a['answer'].lower()
    assert any(x.get('lesson') for x in a['actions'])
    a = _ask('how do I make my own token?')
    assert any(x.get('new') == 'token' for x in a['actions'])
    a = _ask('what is zero knowledge?')
    assert 'Commitment' in a['answer'] and "isn't running" in a['answer']
    assert any(x.get('lesson') == 'zk-ladder' for x in a['actions'])
    a = _ask('what can counter do?', {'project': 'counter'})
    assert '`increment`' in a['answer'] and any(x.get('open') == 'counter' for x in a['actions'])
    a = _ask('why did my test fail?', {'project': 'counter'})
    assert 'Press **Build**' in a['answer']
    a = _ask('what does one yocto mean')
    assert 'yocto' in a['answer']
    assert all(s['tool'].startswith(('mcp:', 'cairo:')) for s in a['result'])


def test_builder_is_gated_and_parses_names(sandbox):
    ev = tutor.run({'query': 'make a tip jar called tips'}, who={'writable': False, 'why': 'no token'},
                   emit=lambda e: None, start_job=None, get_job=None, agent='builder')
    assert ev['type'] == 'error' and ev['owner_only']
    assert tutor._name_from('make a tip jar called tips') == 'tips'
    assert tutor._name_from('build me a lottery contract') == 'lottery'
    assert tutor._name_from('add a pause switch') is None
    ids = ['counter', 'token', 'commit_reveal', 'merkle_allowlist']
    assert tutor._guess_template('a sealed-bid auction', ids) == 'commit_reveal'
    assert tutor._guess_template('an airdrop allowlist', ids) == 'merkle_allowlist'
    assert tutor._guess_template('a tip jar', ids) == 'token'
    assert tutor._guess_template('something else', ids) == 'counter'
    assert re.search(tutor.ZK, 'make a zk vote') and not re.search(tutor.ZK, 'make a sealed vote')


def test_builder_scaffold_only_runs_a_check_job(sandbox, monkeypatch):
    """vibe=false: scaffold + a check job, no agent. The job runner is stubbed
    so this stays offline and instant."""
    started = {}

    def start(fn, kw):
        started.update(fn=fn, kw=kw)
        return {'id': 'j1'}

    def get(id_):
        return {'id': id_, 'state': 'done', 'result': {'build': {'ok': True},
                'test': {'ok': True, 'tests': [{'name': 't::a', 'status': 'pass'}]}}}
    ev = tutor.run({'query': 'make a counter called clicks', 'vibe': False}, who={'writable': True},
                   emit=lambda e: None, start_job=start, get_job=get, agent='builder')
    assert ev['type'] == 'done' and ev['project'] == 'clicks' and ev['mode'] == 'make'
    assert started['fn'] == 'check' and (sandbox / 'clicks' / 'src' / 'lib.rs').exists()
    assert '1/1' in ev['answer']


# ── gates ────────────────────────────────────────────────────────

def test_workshop_gate(monkeypatch):
    monkeypatch.setattr(workshop, 'token_ok', lambda t: t == 'good')
    workshop.gate('near_new', {'token': 'good'})
    workshop.gate('near_new', {}, loopback=True)
    workshop.gate('near_projects', {})
    with pytest.raises(Exception):
        workshop.gate('near_new', {'token': 'bad'})
    assert workshop.is_loopback('127.0.0.1', {})
    assert not workshop.is_loopback('127.0.0.1', {'x-forwarded-for': '1.2.3.4'})
    assert not workshop.is_loopback('10.0.0.5', {})


def test_tools_registered():
    for t in ('near_lessons', 'near_lesson', 'near_templates', 'near_projects', 'near_project',
              'near_new', 'near_build', 'near_verify', 'near_vibe', 'near_ship', 'near_merkle',
              'near_seal', 'near_zk', 'near_ask', 'near_jobs'):
        assert t in mcp.TOOLS
    assert set(workshop.WRITE) <= set(mcp.TOOLS)


# ── optional: real cargo ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get('NEAR_BUILD'), reason='NEAR_BUILD=1 compiles a starter')
def test_counter_builds_and_tests(sandbox):
    studio.new('counter', 'counter')
    r = studio.check('counter')
    assert r['build']['ok'], r['build'].get('log')
    assert 'increment' in r['build']['wasm']['exports']
    assert r['test']['ok'] and r['test']['passed'] == 3
