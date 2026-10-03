"""Live smoke — the running services, skipped entirely when they're down.

Read-only checks against the real deployment: hub-api (:50520, loopback)
and hub-app (:50521). Everything hermetic lives in the other files; these
only confirm the deployed processes still honor the same contracts.

Run: pytest core/hub/test
"""
import json
import socket
import urllib.error
import urllib.request

import pytest

API = "http://127.0.0.1:50520"
APP = "http://127.0.0.1:50521"


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _get_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


@pytest.mark.skipif(not _up(50520), reason="hub-api not running on :50520")
class TestLiveApi:
    def test_health(self):
        assert _get_json(API + "/health")["ok"] is True

    def test_catalog_knows_itself(self):
        out = _get_json(API + "/modules?q=hub")
        assert any(m["name"] == "hub" for m in out["modules"])

    def test_catalog_is_nonempty(self):
        assert _get_json(API + "/modules")["count"] > 50

    def test_probe_validates(self):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get_json(API + "/probe?ports=abc")
        assert e.value.code == 400
        # The api's own port is in use, by the process answering this.
        out = _get_json(API + "/probe?ports=50520")
        assert out["ports"]["50520"] is True


@pytest.mark.skipif(not _up(50521), reason="hub-app not running on :50521")
class TestLiveApp:
    def test_health(self):
        with urllib.request.urlopen(APP + "/hub/health", timeout=5) as r:
            assert r.read() == b"ok"

    def test_mods_and_privacy(self):
        with urllib.request.urlopen(APP + "/hub/_mods", timeout=10) as r:
            names = {m["name"] for m in json.load(r)}
        assert "hub" in names and "docs" in names
        # Nothing with an enabled private record may appear (same rule the
        # caddy router applies).
        import os
        priv_dir = os.path.expanduser("~/.mod/build/private")
        for fn in (os.listdir(priv_dir) if os.path.isdir(priv_dir) else []):
            try:
                rec = json.load(open(os.path.join(priv_dir, fn)))
            except Exception:
                continue
            if rec.get("enabled"):
                assert fn[:-5] not in names, f"private module {fn[:-5]} leaked into /hub/_mods"

    def test_wp_variant_header(self):
        with urllib.request.urlopen(APP + "/hub/_wp?v=full", timeout=10) as r:
            assert r.headers["X-Wp-Variant"] == "full"
            assert len(r.read()) > 500
