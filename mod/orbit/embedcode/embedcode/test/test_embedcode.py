"""
tests for embedcode module

Usage:
    cd ~/mod/mod/orbit/embedcode/embedcode
    python -m pytest test/ -v
"""
import os
import pytest

from embedcode_mod import Mod, collect_files, chunk_text, VectorStore


# ── fixtures ──

@pytest.fixture
def tmp_code(tmp_path):
    (tmp_path / "main.py").write_text(
        "def hello():\n    print('hello world')\n\ndef add(a, b):\n    return a + b\n"
    )
    (tmp_path / "app.js").write_text(
        "function greet(name) {\n  return `Hello, ${name}`;\n}\nmodule.exports = { greet };\n"
    )
    sub = tmp_path / "utils"
    sub.mkdir()
    (sub / "helpers.py").write_text(
        "def sanitize(s):\n    return s.strip().lower()\n\ndef validate_email(email):\n    return '@' in email\n"
    )
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("// junk")
    (tmp_path / "image.png").write_bytes(b'\x89PNG')
    return tmp_path


@pytest.fixture
def mod_instance(tmp_path):
    return Mod(store_dir=str(tmp_path / "store"))


# ── collect_files ──

def test_collect_walks_directory(tmp_code):
    files = collect_files(str(tmp_code))
    paths = [f['path'] for f in files]
    assert any('main.py' in p for p in paths)
    assert any('app.js' in p for p in paths)
    assert any('helpers.py' in p for p in paths)


def test_collect_ignores_node_modules(tmp_code):
    files = collect_files(str(tmp_code))
    paths = [f['path'] for f in files]
    assert not any('/node_modules/' in p for p in paths)


def test_collect_ignores_non_code(tmp_code):
    files = collect_files(str(tmp_code))
    paths = [f['path'] for f in files]
    assert not any(p.endswith('.png') for p in paths)


def test_collect_single_file(tmp_code):
    files = collect_files(str(tmp_code / "main.py"))
    assert len(files) == 1
    assert 'hello' in files[0]['content']


def test_collect_custom_extensions(tmp_code):
    files = collect_files(str(tmp_code), extensions={'.js'})
    assert len(files) == 1
    assert 'app.js' in files[0]['path']


# ── chunk_text ──

def test_chunk_small():
    chunks = chunk_text("line1\nline2\nline3", chunk_size=10, overlap=0)
    assert len(chunks) == 1
    assert "line1" in chunks[0]


def test_chunk_large():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert len(chunks) > 1


def test_chunk_empty():
    chunks = chunk_text("")
    assert len(chunks) == 1


# ── VectorStore ──

def test_store_add_and_search():
    store = VectorStore()
    store.add(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}],
    )
    results = store.search([1.0, 0.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0]['id'] == 'a'
    assert results[0]['score'] == pytest.approx(1.0)


def test_store_save_load(tmp_path):
    path = str(tmp_path / "test_store")
    store = VectorStore(path)
    store.add([[1.0, 0.0]], [{'id': 'x'}])
    store.save()

    loaded = VectorStore(path)
    assert loaded.count == 1
    assert loaded.metadata[0]['id'] == 'x'


def test_store_empty_search():
    store = VectorStore()
    results = store.search([1.0, 0.0], top_k=5)
    assert results == []


def test_store_clear():
    store = VectorStore()
    store.add([[1.0]], [{'id': 'a'}])
    assert store.count == 1
    store.clear()
    assert store.count == 0


# ── Mod (no model needed) ──

def test_mod_info(mod_instance):
    info = mod_instance.info()
    assert info['module'] == 'embedcode'


def test_forward_dispatch(mod_instance):
    result = mod_instance.forward()
    assert result['module'] == 'embedcode'
    assert 'embed' in result['fns']


def test_forward_unknown_fn(mod_instance):
    with pytest.raises(ValueError, match="unknown fn"):
        mod_instance.forward('nonexistent')


def test_mod_status(mod_instance):
    s = mod_instance.status()
    assert s['module'] == 'embedcode'


def test_collections_empty(mod_instance):
    cols = mod_instance.collections()
    assert cols['collections'] == []


def test_delete_missing(mod_instance):
    result = mod_instance.delete('nonexistent')
    assert 'error' in result


# ── Mod integration (requires sentence-transformers) ──

@pytest.fixture
def mod_with_model(tmp_path):
    try:
        import sentence_transformers
    except ImportError:
        pytest.skip("sentence-transformers not installed")
    return Mod(store_dir=str(tmp_path / "store"))


def test_embed_and_search(tmp_code, mod_with_model):
    result = mod_with_model.embed(path=str(tmp_code), collection='test')
    assert result['collection'] == 'test'
    assert result['files'] >= 3
    assert result['chunks'] >= 3

    results = mod_with_model.search('print hello world', collection='test', top_k=3)
    assert len(results) > 0
    assert results[0]['score'] > 0


def test_mod_collections(tmp_code, mod_with_model):
    mod_with_model.embed(path=str(tmp_code), collection='mycode')
    cols = mod_with_model.collections()
    assert 'mycode' in cols['collections']


def test_mod_delete(tmp_code, mod_with_model):
    mod_with_model.embed(path=str(tmp_code), collection='del_me')
    result = mod_with_model.delete('del_me')
    assert result == {'deleted': 'del_me'}
    cols = mod_with_model.collections()
    assert 'del_me' not in cols['collections']


def test_embed_empty_dir(tmp_path, mod_with_model):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = mod_with_model.embed(path=str(empty))
    assert 'error' in result
