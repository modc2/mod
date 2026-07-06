"""
tests for embedcode module

Usage:
    cd ~/mod/mod/orbit/embedcode/embedcode
    python -m pytest test/ -v
"""
import os
import sys
import json
import shutil
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mod import Mod, collect_files, chunk_text, EmbeddingEngine, VectorStore


# ── fixtures ──

@pytest.fixture
def tmp_code(tmp_path):
    """Create a temp dir with sample code files."""
    # python file
    (tmp_path / "main.py").write_text(
        "def hello():\n    print('hello world')\n\ndef add(a, b):\n    return a + b\n"
    )
    # js file
    (tmp_path / "app.js").write_text(
        "function greet(name) {\n  return `Hello, ${name}`;\n}\nmodule.exports = { greet };\n"
    )
    # nested dir
    sub = tmp_path / "utils"
    sub.mkdir()
    (sub / "helpers.py").write_text(
        "def sanitize(s):\n    return s.strip().lower()\n\ndef validate_email(email):\n    return '@' in email\n"
    )
    # should be ignored
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("// junk")
    # non-code file
    (tmp_path / "image.png").write_bytes(b'\x89PNG')
    return tmp_path


@pytest.fixture
def tmp_store(tmp_path):
    return str(tmp_path / "store")


@pytest.fixture
def mod_instance(tmp_store):
    return Mod(store_dir=tmp_store)


# ── collect_files ──

class TestCollectFiles:
    def test_walks_directory(self, tmp_code):
        files = collect_files(str(tmp_code))
        paths = [f['path'] for f in files]
        assert any('main.py' in p for p in paths)
        assert any('app.js' in p for p in paths)
        assert any('helpers.py' in p for p in paths)

    def test_ignores_node_modules(self, tmp_code):
        files = collect_files(str(tmp_code))
        paths = [f['path'] for f in files]
        assert not any('node_modules' in p for p in paths)

    def test_ignores_non_code(self, tmp_code):
        files = collect_files(str(tmp_code))
        paths = [f['path'] for f in files]
        assert not any('.png' in p for p in paths)

    def test_single_file(self, tmp_code):
        files = collect_files(str(tmp_code / "main.py"))
        assert len(files) == 1
        assert 'hello' in files[0]['content']

    def test_custom_extensions(self, tmp_code):
        files = collect_files(str(tmp_code), extensions={'.js'})
        assert len(files) == 1
        assert 'app.js' in files[0]['path']


# ── chunk_text ──

class TestChunkText:
    def test_small_text(self):
        chunks = chunk_text("line1\nline2\nline3", chunk_size=10)
        assert len(chunks) == 1
        assert "line1" in chunks[0]

    def test_large_text(self):
        text = "\n".join(f"line {i}" for i in range(100))
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        assert len(chunks) > 1
        # chunks should overlap
        assert chunks[0].split('\n')[-1] in chunks[1]

    def test_empty_text(self):
        chunks = chunk_text("")
        assert len(chunks) == 1


# ── VectorStore ──

class TestVectorStore:
    def test_add_and_search(self):
        store = VectorStore()
        # simple 3-dim vectors
        store.add(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}],
        )
        results = store.search([1.0, 0.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0]['id'] == 'a'
        assert results[0]['score'] == pytest.approx(1.0)

    def test_save_load(self, tmp_path):
        path = str(tmp_path / "test_store")
        store = VectorStore(path)
        store.add([[1.0, 0.0]], [{'id': 'x'}])
        store.save()

        loaded = VectorStore(path)
        assert loaded.count == 1
        assert loaded.metadata[0]['id'] == 'x'

    def test_empty_search(self):
        store = VectorStore()
        results = store.search([1.0, 0.0], top_k=5)
        assert results == []

    def test_clear(self):
        store = VectorStore()
        store.add([[1.0]], [{'id': 'a'}])
        assert store.count == 1
        store.clear()
        assert store.count == 0


# ── EmbeddingEngine ──

class TestEmbeddingEngine:
    @pytest.fixture(autouse=True)
    def skip_if_no_model(self):
        try:
            import sentence_transformers
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_embed_batch(self):
        engine = EmbeddingEngine()
        vecs = engine.embed(["hello world", "foo bar"])
        assert len(vecs) == 2
        assert len(vecs[0]) == engine.dim

    def test_embed_one(self):
        engine = EmbeddingEngine()
        vec = engine.embed_one("test")
        assert len(vec) == engine.dim


# ── Mod (integration) ──

class TestMod:
    @pytest.fixture(autouse=True)
    def skip_if_no_model(self):
        try:
            import sentence_transformers
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_embed_and_search(self, tmp_code, mod_instance):
        result = mod_instance.embed(path=str(tmp_code), collection='test')
        assert result['collection'] == 'test'
        assert result['files'] >= 3
        assert result['chunks'] >= 3

        results = mod_instance.search('print hello world', collection='test', top_k=3)
        assert len(results) > 0
        assert results[0]['score'] > 0
        # best result should be from main.py
        assert 'main.py' in results[0]['path']

    def test_collections(self, tmp_code, mod_instance):
        mod_instance.embed(path=str(tmp_code), collection='mycode')
        cols = mod_instance.collections()
        assert 'mycode' in cols['collections']

    def test_delete(self, tmp_code, mod_instance):
        mod_instance.embed(path=str(tmp_code), collection='del_me')
        result = mod_instance.delete('del_me')
        assert result == {'deleted': 'del_me'}
        cols = mod_instance.collections()
        assert 'del_me' not in cols['collections']

    def test_info(self, mod_instance):
        info = mod_instance.info()
        assert info['module'] == 'embedcode'

    def test_forward_dispatch(self, mod_instance):
        result = mod_instance.forward()
        assert result['module'] == 'embedcode'
        assert 'embed' in result['fns']

    def test_forward_unknown_fn(self, mod_instance):
        with pytest.raises(ValueError, match="unknown fn"):
            mod_instance.forward('nonexistent')

    def test_embed_empty_dir(self, tmp_path, mod_instance):
        result = mod_instance.embed(path=str(tmp_path))
        assert 'error' in result

    def test_status(self, mod_instance):
        s = mod_instance.status()
        assert s['module'] == 'embedcode'
