"""
leanland's own tests.

Two things are worth testing here and they are not the same thing.

The first is that the shipped library is right: it typechecks, and every
`#example` reproduces the number its source states. That is `verify`, and it
runs against the real lib/.

The second is that lowering preserves meaning — that the Rust and the JavaScript
compute what the reference interpreter computes. That is `parity`, and it is the
only test that can catch a bad emission template, because a template can be
wrong in a way that still compiles.

The elaboration loop is tested with a stub model rather than a live one. What is
being tested there is the gate, not the model: a proposal that does not
typecheck must not reach lib/, and the compiler's own error text must be what
goes back for the retry.
"""
import json
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

from leanland.src import chat, check, ir, lean, lower               # noqa: E402
from leanland.src.library import Library                            # noqa: E402


@pytest.fixture(scope='module')
def lib():
    return Library(ROOT)


@pytest.fixture
def scratch():
    """A library of its own, so a test that writes cannot touch lib/."""
    tmp = tempfile.mkdtemp(prefix='leanland-test-')
    os.makedirs(os.path.join(tmp, 'lib'))
    os.makedirs(os.path.join(tmp, 'lit'))
    yield Library(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- the language

def test_parse_typecheck_and_evaluate():
    src = '''/-- twice -/
@[convention]
def twice (x : Real) : Real :=
  2 * x
#example twice 2.5 = 5
'''
    (d,) = lean.parse(src)
    assert d.name == 'twice' and d.ret == 'Real'
    ir.check(d, {})
    assert ir.call(d, [2.5], {'twice': d}) == 5.0


def test_type_error_names_the_problem():
    (d,) = lean.parse('def bad (v : Vec Real) : Real := v + 1')
    with pytest.raises(TypeError) as e:
        ir.check(d, {})
    assert 'Vec Real' in str(e.value)


def test_unknown_function_is_rejected():
    (d,) = lean.parse('def bad (x : Real) : Real := gamma x')
    with pytest.raises(TypeError, match='gamma'):
        ir.check(d, {})


def test_recursion_is_rejected():
    (d,) = lean.parse('def loop (x : Real) : Real := loop x')
    with pytest.raises(TypeError, match='recursion'):
        ir.check(d, {'loop': d})


def test_application_stops_at_the_end_of_a_line():
    """`let n := len v` followed by a parenthesised body is two things, not a call."""
    (d,) = lean.parse('''def f (v : Vec Real) : Real :=
  let n := len v
  (v[0]) / n
''')
    ir.check(d, {})
    assert ir.call(d, [[4.0, 1.0]], {}) == 2.0


def test_render_round_trips():
    (d,) = lean.parse('def g (x : Real) (y : Real) : Real := (x + y) ^ 2 / (x - y)')
    ir.check(d, {})
    (d2,) = lean.parse(lean.render(d))
    ir.check(d2, {})
    env = {'x': 3.0, 'y': 1.0}
    assert ir.evaluate(d.body, env, {}) == ir.evaluate(d2.body, env, {})


# ---------------------------------------------------------------- the library

def test_shipped_library_verifies(lib):
    v = lib.verify()
    assert v['errors'] == [], v['errors']
    assert v['failed'] == [], v['failed']
    assert v['examples'] > 0
    assert v['unsourced'] == [], f'uncited definitions: {v["unsourced"]}'
    assert v['missing_lit'] == [], f'cites papers with no lit/ entry: {v["missing_lit"]}'


def test_every_definition_is_reachable_from_a_paper_or_marked_convention(lib):
    for d in lib.defs.values():
        assert d.source.get('key') or d.source.get('convention'), d.name


def test_add_rejects_a_definition_that_fails_its_own_example(scratch):
    r = scratch.add('def half (x : Real) : Real := x / 2\n#example half 4 = 3\n')
    assert not r['ok'] and r['stage'] == 'examples'
    assert scratch.files() == []


def test_add_then_replace_does_not_duplicate(scratch):
    src = '@[convention]\ndef half (x : Real) : Real :=\n  x / 2\n#example half 4 = 2\n'
    assert scratch.add(src, file='t.lean')['ok']
    better = ('/-- halve it -/\n@[convention]\ndef half (x : Real) : Real :=\n'
              '  x * 0.5\n#example half 4 = 2\n')
    assert scratch.add(better, file='t.lean')['ok']
    text = open(os.path.join(scratch.lib_dir, 't.lean')).read()
    assert text.count('def half') == 1
    assert '0.5' in text
    assert len(scratch.defs) == 1


def test_rm_refuses_to_orphan_a_caller(scratch):
    scratch.add('@[convention]\ndef base (x : Real) : Real := x + 1\n#example base 1 = 2\n')
    scratch.add('@[convention]\ndef user (x : Real) : Real := base x * 2\n#example user 1 = 4\n')
    with pytest.raises(ValueError, match='user'):
        scratch.rm('base')


# ---------------------------------------------------------------- lowering

def test_every_target_emits_for_every_definition(lib):
    defs = lib.defs
    for key in ('py', 'rs', 'ts', 'js'):
        tgt = lower.TARGETS[key]
        for d in lower.order(defs):
            assert lower.emit.function(d, defs, tgt)


def test_generated_python_passes_its_own_generated_tests(lib, tmp_path):
    defs = lib.defs
    (tmp_path / 'leanland_lib.py').write_text(lower.python(defs))
    (tmp_path / 'test_gen.py').write_text(lower.python_tests(defs))
    import subprocess
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tmp_path / 'test_gen.py')],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:]


def test_notebooks_are_valid_and_self_contained(lib):
    defs = lib.defs
    papers = lib.lit.all()
    for d in lower.order(defs):
        nb = lower.notebook(d, defs, papers)
        assert nb['nbformat'] == 4
        json.dumps(nb)                                  # must be serialisable
        code = '\n'.join(''.join(c['source']) for c in nb['cells']
                         if c['cell_type'] == 'code')
        for dep in d.deps:                              # dependencies travel with it
            assert f'def {dep}(' in code, (d.name, dep)


def test_parity_python_and_js(lib):
    """The reference, the generated Python and the generated web code agree."""
    r = check.parity(lib.defs, targets=['python', 'js'])
    assert not r['skipped'], r['skipped']
    assert r['mismatches'] == [], r['mismatches']
    assert r['worst_delta'] < 1e-12


@pytest.mark.skipif(not shutil.which('rustc'), reason='no rustc on this box')
def test_parity_rust(lib):
    r = check.parity(lib.defs, targets=['rust'])
    assert not r['skipped'], r['skipped']
    assert r['mismatches'] == [], r['mismatches']
    assert r['worst_delta'] < 1e-12


def test_build_is_deterministic_and_drift_free(lib, tmp_path):
    once = lib.artifacts()
    twice = lib.artifacts()
    assert once == twice, 'generation is not deterministic'
    assert any(p.startswith('notebooks/') for p in once)
    assert 'rust/src/lib.rs' in once and 'nextjs/lib/leanland.ts' in once


def test_drift_notices_a_hand_edit(scratch):
    scratch.add('@[convention]\ndef half (x : Real) : Real := x / 2\n#example half 4 = 2\n')
    scratch.build(['python'])
    assert scratch.drift()['clean']
    path = os.path.join(scratch.out_dir, 'python', 'leanland_lib.py')
    with open(path, 'a') as f:
        f.write('\n# someone edited the generated file\n')
    d = scratch.drift()
    assert not d['clean'] and 'python/leanland_lib.py' in d['edited']


# ---------------------------------------------------------------- elaboration

def test_elaborate_retries_with_the_compiler_error_and_only_files_what_passes(
        scratch, monkeypatch):
    replies = [
        # 1: not even parseable
        '```lean\ndef broken (x : Real) : Real :=\n```',
        # 2: parses, does not typecheck — `gamma` is not a primitive
        '```lean\n@[convention]\ndef half (x : Real) : Real :=\n  gamma x\n'
        '#example half 4 = 2\n```',
        # 3: typechecks but does not reproduce its own example
        '```lean\n@[convention]\ndef half (x : Real) : Real :=\n  x / 3\n'
        '#example half 4 = 2\n```',
        # 4: right
        '```lean\n/-- halve it -/\n@[convention]\ndef half (x : Real) : Real :=\n'
        '  x / 2\n#example half 4 = 2\n```',
    ]
    seen = []

    def fake_ask(message, system='', model=None, history=None, provider=None):
        seen.append(message)
        return replies[len(seen) - 1]

    monkeypatch.setattr(chat, 'ask', fake_ask)
    r = chat.elaborate(scratch, 'halve a number', tries=4, file='t.lean')

    assert r['ok'] and r['tries'] == 4
    assert [a['stage'] for a in r['attempts']] == ['parse', 'typecheck', 'examples', 'accepted']
    # the compiler's own words are what the model is asked to fix
    assert 'gamma' in seen[2]
    assert 'does not reproduce its own #example' in seen[3]
    assert 'half' in scratch.defs
    assert open(os.path.join(scratch.lib_dir, 't.lean')).read().count('def half') == 1


def test_elaborate_writes_nothing_when_it_never_passes(scratch, monkeypatch):
    monkeypatch.setattr(chat, 'ask',
                        lambda *a, **k: '```lean\ndef nope (x : Real) : Real := gamma x\n```')
    r = chat.elaborate(scratch, 'something impossible', tries=2, file='t.lean')
    assert not r['ok'] and len(r['attempts']) == 2
    assert scratch.defs == {}
    assert not os.path.exists(os.path.join(scratch.lib_dir, 't.lean'))


def test_elaborate_demands_an_example(scratch, monkeypatch):
    monkeypatch.setattr(chat, 'ask', lambda *a, **k:
                        '```lean\n@[convention]\ndef half (x : Real) : Real := x / 2\n```')
    r = chat.elaborate(scratch, 'halve', tries=1)
    assert not r['ok'] and 'no #example' in r['attempts'][0]['error']


def test_language_reference_lists_every_primitive():
    ref = chat.language_reference()
    for prim in ir.PRIMS:
        assert prim in ref, prim


def test_provider_that_answers_with_a_dict_is_a_miss():
    assert chat._text({'module': 'agent', 'actions': []}) is None
    assert chat._text('  hello ') == 'hello'
    assert chat._text({'answer': 'hi'}) == 'hi'
