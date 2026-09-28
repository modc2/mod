"""hub app (app/server.js) — the real node server against the fake tree.

This is the PUBLIC surface, so this is where privacy must hold: a module
with an enabled record under {HOME}/.mod/build/private/ is simply absent.
The server is zero-dep; one process serves the whole file.

Run: pytest core/hub/test
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from conftest import build_fake_home

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "app", "server.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = build_fake_home(tmp_path_factory.mktemp("hub-app-home"))
    port = _free_port()
    env = dict(os.environ, PORT=str(port), BASE_PATH="/hub",
               HOME=str(home), MOD_REPO=str(home / "mod" / "mod"))
    proc = subprocess.Popen(["node", SERVER], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 10
        while True:
            try:
                with urllib.request.urlopen(base + "/hub/health", timeout=1) as r:
                    assert r.read() == b"ok"
                break
            except (urllib.error.URLError, ConnectionError):
                if proc.poll() is not None or time.time() > deadline:
                    raise RuntimeError("hub app did not come up")
                time.sleep(0.1)
        yield base, home
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        # r.headers is case-insensitive (node sends lowercase names).
        return r.status, r.headers, r.read()


def test_health(app):
    base, _ = app
    assert _get(base, "/hub/health")[0] == 200


def test_mods_hides_private_modules(app):
    base, _ = app
    _, headers, body = _get(base, "/hub/_mods")
    assert headers["Content-Type"] == "application/json"
    names = {m["name"] for m in json.loads(body)}
    assert "secret" not in names          # enabled private record → absent
    assert "alpha" in names               # enabled: false → public
    assert not any(n.startswith((".", "_")) for n in names)


def test_mods_row_shape(app):
    base, _ = app
    rows = {m["name"]: m for m in json.loads(_get(base, "/hub/_mods")[2])}
    alpha = rows["alpha"]
    assert alpha["group"] == "orbit"
    assert alpha["description"] == "First test module Alpha"
    assert alpha["readme"] and alpha["skill"]
    assert rows["beta"]["description"] == "Beta nested config"   # nested config.json


def test_doc_serves_readme_and_skill(app):
    base, _ = app
    doc = json.loads(_get(base, "/hub/_doc/alpha")[2])
    assert doc["group"] == "orbit"
    assert "Alpha readme body." in doc["readme"]
    assert "alpha skill body" in doc["skill"]
    assert json.loads(_get(base, "/hub/_doc/broken")[2])["readme"] is None


def test_doc_refuses_private_missing_and_traversal(app):
    base, _ = app
    for name in ("secret", "no-such-mod", "..%2F..%2Fetc", "_priv", ".hidden"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base, "/hub/_doc/" + name)
        assert e.value.code == 404


def test_whitepaper_variants(app):
    base, _ = app
    _, headers, body = _get(base, "/hub/_wp")
    assert headers["X-Wp-Variant"] == "simple"
    assert b"WP HUMAN" in body
    _, headers, body = _get(base, "/hub/_wp?v=full")
    assert headers["X-Wp-Variant"] == "full"
    assert b"WP ENGINEER" in body


def test_everything_else_serves_the_spa(app):
    base, _ = app
    for path in ("/hub", "/hub/", "/hub/anything/deep"):
        status, headers, body = _get(base, path)
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"<" in body


# Last on purpose: mutates the module-scoped tree, so it runs after every
# test that reads the whitepaper files.
def test_whitepaper_falls_back_when_docs_missing(app):
    base, home = app
    shutil.rmtree(home / "mod" / "mod" / "core" / "docs")
    _, headers, body = _get(base, "/hub/_wp?v=full")
    assert headers["X-Wp-Variant"] == "full"
    assert b"whitepaper lives in the docs module" in body
