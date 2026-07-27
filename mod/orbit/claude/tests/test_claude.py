"""
Tests for the Claude module (v2).

Run with: pytest tests/test_claude.py -v -s
         pytest tests/test_claude.py -v -s -k "Unit"     # unit tests only

Tests are split into:
  - Unit tests:   Always run, no API/CLI/server needed
  - CLI tests:    Require claude binary + auth
  - Server tests: Require the Rust job server running
"""
import pytest
import os
import sys
import subprocess
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

# Add parent directory to path so we can import the module
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.mod as m
from src.mod import Mod


# ── Helpers ──────────────────────────────────────────────────────

def has_claude_cli():
    try:
        result = subprocess.run(["which", "claude"], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def has_auth():
    if os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN'):
        return True
    auth_file = Path.home() / '.claude' / '.credentials.json'
    return auth_file.exists()

def can_run_claude():
    return has_claude_cli() and has_auth()

def is_server_running():
    try:
        import urllib.request
        url = 'http://localhost:8820/health'
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


# The port the negative-connectivity tests assume is closed.
_PRESUMED_CLOSED_URL = 'http://127.0.0.1:59999'


def closed_port_is_reachable():
    """True when the port the negative-connectivity tests assume is *closed* is
    actually answering here.

    Those tests assert that a bad URL is unreachable, which only holds if the
    OS refuses the connection. In some environments that port is occupied (a
    stray dev server) or the sandbox intercepts localhost, so the connection
    succeeds — any HTTP response (even a 4xx/5xx) means the port answered, so the
    assertion can't hold and the test must skip. Only a refused/failed
    connection (URLError without an HTTP status) means it is truly closed.
    """
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f'{_PRESUMED_CLOSED_URL}/health', timeout=2):
            return True
    except urllib.error.HTTPError:
        return True  # something answered with an HTTP status — port is occupied
    except Exception:
        return False  # connection refused / unreachable — genuinely closed


def server_is_open():
    """True when the job server answers an auth-gated endpoint WITHOUT a token —
    i.e. it's running in local/open mode, so the server read/write tests can run.
    A server running with auth on (the pm2/root deployment) returns 401 here, in
    which case those tests are skipped instead of failing on authorization."""
    if not is_server_running():
        return False
    import urllib.request
    try:
        req = urllib.request.Request('http://localhost:8820/jobs', method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


_CLI_AUTH_OK = None


def cli_auth_works():
    """One-shot, cached check that the Claude CLI can actually produce output.

    `has_auth()` only confirms a key/credentials file is *present*, not that it
    *works*. CLI tests invoke the real binary, so gate them on a single trivial
    call — skip cleanly when the configured auth is missing or rejected rather
    than failing every CLI test with a RuntimeError."""
    global _CLI_AUTH_OK
    if _CLI_AUTH_OK is not None:
        return _CLI_AUTH_OK
    if not can_run_claude():
        _CLI_AUTH_OK = False
        return False
    try:
        r = subprocess.run(
            ["claude", "--print", "--model", "haiku", "--output-format", "text", "ok"],
            capture_output=True, text=True, timeout=60,
        )
        _CLI_AUTH_OK = r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        _CLI_AUTH_OK = False
    return _CLI_AUTH_OK


skip_no_cli = pytest.mark.skipif(not has_claude_cli(), reason="Claude CLI not installed")
skip_no_auth = pytest.mark.skipif(not can_run_claude(), reason="No auth available")
skip_no_server = pytest.mark.skipif(not is_server_running(), reason="Job server not running")
skip_server_closed = pytest.mark.skipif(
    not server_is_open(), reason="Job server not in local/open mode (auth-gated)")
skip_network_intercepted = pytest.mark.skipif(
    closed_port_is_reachable(),
    reason="presumed-closed port is occupied/intercepted — connection-refused impossible")
skip_no_working_cli = pytest.mark.skipif(
    not cli_auth_works(), reason="Claude CLI auth not working in this environment")


# ── Unit Tests (always run, no dependencies) ─────────────────────

class TestUnit:
    """Core unit tests — always pass, no external deps."""

    def test_init_default(self):
        """Module initializes with defaults."""
        c = Mod()
        assert c.config is not None
        assert c.default_path is not None
        assert c.api_url.startswith('http')

    def test_init_custom_urls(self):
        """Custom API/app URLs are respected."""
        c = Mod(api_url='http://custom:9000', app_url='http://custom:9001')
        assert c.api_url == 'http://custom:9000'
        assert c.app_url == 'http://custom:9001'

    def test_init_with_default_path(self):
        """default_path parameter works."""
        c = Mod(default_path='/tmp/test')
        assert c.default_path == '/tmp/test'

    def test_config_loads(self):
        """Config loads from config.json."""
        c = Mod()
        assert 'name' in c.config
        assert c.config['name'] == 'claude'

    def test_description_set(self):
        """Module has description."""
        assert Mod.description is not None
        assert len(Mod.description) > 10

    def test_endpoints_list(self):
        """Module has endpoints list."""
        assert isinstance(Mod.endpoints, list)
        assert 'forward' in Mod.endpoints
        assert 'ask' in Mod.endpoints
        assert 'submit' in Mod.endpoints

    def test_module_dir(self):
        """_module_dir returns the claude package root."""
        c = Mod()
        d = c._module_dir()
        assert os.path.isdir(d)
        assert os.path.exists(os.path.join(d, 'config.json'))


class TestOwnership:
    """Permission / ownership system."""

    @pytest.fixture
    def clean_mod(self, tmp_path):
        """Create a Mod with isolated config to avoid mutating real config."""
        cfg_src = Path(__file__).parent.parent / 'config.json'
        cfg_dst = tmp_path / 'config.json'
        cfg_dst.write_text(cfg_src.read_text())
        c = Mod()
        c._history_dir = tmp_path / '.history'
        return c

    def test_owner_always_set(self):
        """Owner is always set — from config or defaulting to key address."""
        c = Mod()
        assert c._owner is not None

    def test_owner_defaults_to_key_when_no_config(self):
        """When config has no owner, owner defaults to the module's key address."""
        c = Mod()
        # Simulate no owner in config
        c._owner = None
        c._owner = c._owner or c.key.address.lower()
        assert c._owner == c.key.address.lower()

    def test_get_owner_returns_config_value(self):
        """get_owner returns the owner from config."""
        c = Mod()
        owner = c.get_owner()
        assert owner is not None

    def test_owner_info_dict(self):
        """owner() returns dict with has_owner and owner fields."""
        c = Mod()
        info = c.owner()
        assert 'owner' in info
        assert 'has_owner' in info
        assert info['has_owner'] is True

    def test_is_owner_no_owner_set(self):
        """When no owner set, everyone is owner."""
        c = Mod()
        c._owner = None
        assert c.is_owner("0xanyone") is True

    def test_is_owner_none_defaults_to_self_key(self):
        """is_owner(None) resolves to self.key. If self.key IS the owner, returns True."""
        c = Mod()
        c._owner = c.key.address.lower()  # align owner with default key
        assert c.is_owner(None) is True
        assert c.is_owner() is True

    def test_is_owner_match(self):
        """Owner matches correctly (case insensitive)."""
        c = Mod()
        c._owner = "0xabcdef1234567890abcdef1234567890abcdef12"
        assert c.is_owner("0xabcdef1234567890abcdef1234567890abcdef12") is True
        assert c.is_owner("0xABCDEF1234567890ABCDEF1234567890ABCDEF12") is True

    def test_is_owner_no_match(self):
        """Non-owner returns False."""
        c = Mod()
        c._owner = "0xabcdef1234567890abcdef1234567890abcdef12"
        assert c.is_owner("0x1111111111111111111111111111111111111111") is False

    def test_require_owner_no_key_defaults_to_owner(self):
        """require_owner() with no key defaults to self.key (the owner)."""
        c = Mod()
        c._owner = c.key.address.lower()  # align owner with default key
        c.require_owner(operation="test op")  # should not raise

    def test_require_owner_passes(self):
        """require_owner passes for matching address."""
        c = Mod()
        c._owner = "0xabc"
        c.require_owner("0xabc", "test op")  # should not raise

    def test_require_owner_raises(self):
        """require_owner raises PermissionError for wrong address."""
        c = Mod()
        c._owner = "0xabc"
        with pytest.raises(PermissionError, match="Permission denied"):
            c.require_owner("0xother", "test op")

    def test_require_owner_message_contains_operation(self):
        """PermissionError includes the operation name."""
        c = Mod()
        c._owner = "0xabc"
        with pytest.raises(PermissionError, match="edit_file"):
            c.require_owner("0xother", "edit_file")

    def test_require_owner_message_readable(self):
        """Error message is human-readable with truncated addresses."""
        c = Mod()
        c._owner = "0xeb0631ce3ec62ceed053c66eb6481753d0c812a8"
        c._bloctime_gate = False  # isolate owner-matching from live BlocTime holders
        try:
            c.require_owner("0xaF3e0796042aF79eA1642c919ac0ea6d165Bc6dB", "forward(test query...)")
            assert False, "Should have raised"
        except PermissionError as e:
            msg = str(e)
            assert "Permission denied" in msg
            assert "owner-only" in msg
            assert "caller:" in msg
            assert "owner:" in msg
            assert "0xaF3e0796..." in msg or "0xaf3e0796..." in msg.lower()

    def test_require_owner_handles_key_object(self):
        """require_owner handles Key objects with .address attribute."""
        c = Mod()
        c._owner = "0xeb0631ce3ec62ceed053c66eb6481753d0c812a8"
        c._bloctime_gate = False  # isolate owner-matching from live BlocTime holders
        key = MagicMock()
        key.address = "0xaF3e0796042aF79eA1642c919ac0ea6d165Bc6dB"
        key.__str__ = lambda self: f"Key({self.address}, type=ecdsa)"
        try:
            c.require_owner(key, "forward(test...)")
            assert False, "Should have raised"
        except PermissionError as e:
            msg = str(e)
            assert "Key(" not in msg
            assert "type=ecdsa" not in msg
            assert "caller:" in msg

    def test_is_owner_handles_key_object(self):
        """is_owner handles Key objects with .address attribute."""
        c = Mod()
        c._owner = "0xabc123"
        key = MagicMock()
        key.address = "0xabc123"
        assert c.is_owner(key) is True

    def test_is_owner_handles_key_object_no_match(self):
        """is_owner returns False for non-matching Key object."""
        c = Mod()
        c._owner = "0xabc123"
        key = MagicMock()
        key.address = "0xother"
        assert c.is_owner(key) is False

    def test_format_address_plain_hex(self):
        """_format_address truncates long hex addresses."""
        c = Mod()
        result = c._format_address("0xeb0631ce3ec62ceed053c66eb6481753d0c812a8")
        assert result == "0xeb0631ce...12a8"

    def test_format_address_key_repr(self):
        """_format_address parses Key(...) repr strings."""
        c = Mod()
        result = c._format_address("Key(0xaF3e0796042aF79eA1642c919ac0ea6d165Bc6dB, type=ecdsa)")
        assert "Key(" not in result
        assert "type=" not in result
        assert result.startswith("0xaF3e0796")

    def test_format_address_key_object(self):
        """_format_address uses .address from Key objects."""
        c = Mod()
        key = MagicMock()
        key.address = "0xaF3e0796042aF79eA1642c919ac0ea6d165Bc6dB"
        result = c._format_address(key)
        assert "Key(" not in result
        assert result.startswith("0xaF3e0796")

    def test_format_address_short(self):
        """_format_address doesn't truncate short addresses."""
        c = Mod()
        result = c._format_address("0xabc")
        assert result == "0xabc"


class TestTokenAuth:
    """Token-based authentication."""

    def test_has_auth_module(self):
        """Module initializes with auth module."""
        c = Mod()
        assert c.auth is not None
        assert hasattr(c.auth, 'token')
        assert hasattr(c.auth, 'verify')

    def test_generate_token(self):
        """token() generates a signed token string."""
        c = Mod()
        tok = c.token()
        assert isinstance(tok, str)
        assert len(tok) > 0

    def test_verify_token(self):
        """verify() decodes and validates a token."""
        c = Mod()
        tok = c.token()
        verified = c.verify(tok)
        assert 'key' in verified
        assert verified['key'] == c.key.address

    def test_token_with_custom_data(self):
        """token() can include custom data."""
        c = Mod()
        tok = c.token(data={'fn': 'forward', 'scope': 'write'})
        verified = c.verify(tok)
        assert verified['data']['fn'] == 'forward'

    def test_is_owner_with_token(self):
        """is_owner() accepts a signed token and verifies the caller."""
        c = Mod()
        c._owner = c.key.address.lower()
        tok = c.token()
        assert c.is_owner(tok) is True

    def test_is_owner_with_non_owner_token(self):
        """is_owner() rejects token from a non-owner key."""
        import mod
        c = Mod()
        other_key = mod.key('test.claude.nonowner')
        c._owner = c.key.address.lower()
        tok = c.token(key=other_key)
        assert c.is_owner(tok) is False

    def test_require_owner_with_token(self):
        """require_owner() passes with a valid owner token."""
        c = Mod()
        c._owner = c.key.address.lower()
        tok = c.token()
        c.require_owner(tok, "test op")  # should not raise

    def test_require_owner_rejects_non_owner_token(self):
        """require_owner() rejects token from non-owner."""
        import mod
        c = Mod()
        other_key = mod.key('test.claude.nonowner2')
        c._owner = c.key.address.lower()
        tok = c.token(key=other_key)
        with pytest.raises(PermissionError, match="Permission denied"):
            c.require_owner(tok, "edit_file")

    def test_resolve_address_none_returns_owner(self):
        """_resolve_address(None) returns self.key.address (the owner)."""
        c = Mod()
        addr = c._resolve_address(None)
        assert addr == c.key.address

    def test_resolve_address_key_object(self):
        """_resolve_address extracts .address from Key objects."""
        c = Mod()
        key = MagicMock()
        key.address = "0xdeadbeef"
        assert c._resolve_address(key) == "0xdeadbeef"

    def test_resolve_address_hex_string(self):
        """_resolve_address passes through hex addresses."""
        c = Mod()
        addr = "0xaF3e0796042aF79eA1642c919ac0ea6d165Bc6dB"
        assert c._resolve_address(addr) == addr

    def test_resolve_address_token(self):
        """_resolve_address decodes a valid token to get the address."""
        c = Mod()
        tok = c.token()
        addr = c._resolve_address(tok)
        assert addr == c.key.address


class TestHistory:
    """IPFS version history (local file-based)."""

    @pytest.fixture
    def c(self, tmp_path):
        mod = Mod()
        mod._history_dir = tmp_path / '.history'
        return mod

    def test_empty_history(self, c):
        """Fresh module has empty history."""
        assert c.get_history() == []

    def test_add_and_get_history(self, c):
        """Can add entries and retrieve them."""
        c._add_to_history("QmFirst", "First commit")
        c._add_to_history("QmSecond", "Second commit")
        h = c.get_history()
        assert len(h) == 2
        assert h[0]['cid'] == "QmSecond"  # newest first
        assert h[1]['cid'] == "QmFirst"

    def test_history_with_version(self, c):
        """Version label is stored."""
        c._add_to_history("QmVer", "Tagged release", version="v1.0")
        h = c.get_history()
        assert h[0]['version'] == "v1.0"

    def test_history_limit(self, c):
        """get_history respects limit."""
        for i in range(10):
            c._add_to_history(f"Qm{i}", f"Entry {i}")
        h = c.get_history(limit=3)
        assert len(h) == 3
        assert h[0]['cid'] == "Qm9"

    def test_get_latest_cid(self, c):
        """get_latest_cid returns most recent."""
        c._add_to_history("QmOld", "old")
        c._add_to_history("QmNew", "new")
        assert c.get_latest_cid() == "QmNew"

    def test_get_latest_cid_empty(self, c):
        """get_latest_cid returns None when empty."""
        assert c.get_latest_cid() is None

    def test_get_version_by_label(self, c):
        """Can retrieve entry by version label."""
        c._add_to_history("QmV1", "v1", version="v1.0")
        c._add_to_history("QmV2", "v2", version="v2.0")
        entry = c.get_version(version="v1.0")
        assert entry is not None
        assert entry['cid'] == "QmV1"

    def test_get_version_by_cid(self, c):
        """Can retrieve entry by CID."""
        c._add_to_history("QmSpecial", "special")
        entry = c.get_version(cid="QmSpecial")
        assert entry is not None
        assert entry['description'] == "special"

    def test_get_version_not_found(self, c):
        """Returns None for unknown version."""
        assert c.get_version(version="v999") is None

    def test_changelog_alias(self, c):
        """changelog() is an alias for get_history()."""
        c._add_to_history("Qm1", "one")
        c._add_to_history("Qm2", "two")
        cl = c.changelog(limit=1)
        assert len(cl) == 1
        assert cl[0]['cid'] == "Qm2"

    def test_show_changelog_empty(self, c, capsys):
        """show_changelog handles empty history."""
        c.show_changelog()
        out = capsys.readouterr().out
        assert "No history" in out

    def test_show_changelog_with_entries(self, c, capsys):
        """show_changelog displays entries."""
        c._add_to_history("QmShow", "Test display")
        c.show_changelog()
        out = capsys.readouterr().out
        assert "QmShow" in out
        assert "Test display" in out
        assert "IPFS CID HISTORY" in out

    def test_history_file_persists(self, c):
        """History persists across _load_history calls."""
        c._add_to_history("QmPersist", "persist test")
        # Force reload
        h = c._load_history()
        assert len(h) == 1
        assert h[0]['cid'] == "QmPersist"

    def test_history_entry_has_date(self, c):
        """History entries include date string."""
        c._add_to_history("QmDate", "date test")
        h = c.get_history()
        assert 'date' in h[0]
        assert 'timestamp' in h[0]


class TestBgJobs:
    """Local background job management (no server needed)."""

    def test_bg_status_dead_pid(self):
        """bg_status returns 'completed' for non-existent PID."""
        c = Mod()
        assert c.bg_status(999999999) == "completed"

    def test_bg_list_empty(self):
        """bg_list returns empty for nonexistent directory."""
        c = Mod()
        assert c.bg_list(log_dir="/tmp/nonexistent_claude_logs_xyz") == []

    def test_bg_list_finds_logs(self, tmp_path):
        """bg_list finds .log files."""
        (tmp_path / "job_1.log").write_text("output1")
        (tmp_path / "job_2.log").write_text("output2")
        c = Mod()
        logs = c.bg_list(log_dir=str(tmp_path))
        assert len(logs) == 2
        assert all('file' in l and 'size' in l and 'name' in l for l in logs)

    def test_bg_list_sorted_newest_first(self, tmp_path):
        """bg_list returns logs newest first."""
        f1 = tmp_path / "old.log"
        f1.write_text("old")
        time.sleep(0.05)
        f2 = tmp_path / "new.log"
        f2.write_text("new")
        c = Mod()
        logs = c.bg_list(log_dir=str(tmp_path))
        assert logs[0]['name'] == 'new.log'


class TestServerAvailability:
    """Server connectivity checks."""

    def test_server_available_returns_bool(self):
        """_server_available returns a boolean."""
        c = Mod()
        result = c._server_available()
        assert isinstance(result, bool)

    @skip_network_intercepted
    def test_server_available_bad_url(self):
        """_server_available returns False for bad URL."""
        c = Mod(api_url='http://127.0.0.1:59999')
        assert c._server_available() is False

    @skip_network_intercepted
    def test_request_raises_on_bad_server(self):
        """_request raises ConnectionError for unreachable server."""
        c = Mod(api_url='http://127.0.0.1:59999')
        # Prevent auto-start so it actually fails
        with patch.object(c, '_start_api', return_value=False):
            with pytest.raises(ConnectionError):
                c._request("GET", "/health", timeout=1)

    def test_modules_fallback(self):
        """modules() falls back to m.mods() when server is down."""
        c = Mod(api_url='http://127.0.0.1:59999')
        mods = c.modules()
        assert isinstance(mods, list)
        # should have found some modules via m.mods()
        assert len(mods) > 0


class TestCodeOps:
    """Code operation method signatures (mocked, no CLI needed)."""

    @pytest.fixture
    def c(self):
        mod = Mod()
        mod._find_claude = Mock(return_value='/usr/bin/echo')
        return mod

    def test_analyze_code_calls_cli(self, c):
        """analyze_code invokes _run_cli."""
        with patch.object(c, '_run_cli', return_value="analysis result") as mock:
            result = c.analyze_code(path="/tmp", focus="security")
            assert mock.called
            assert result == "analysis result"
            # check prompt contains focus
            call_args = mock.call_args
            assert "security" in call_args[0][0]

    def test_generate_code_requires_owner(self, c):
        """generate_code requires owner permission."""
        c._owner = "0xowner"
        with pytest.raises(PermissionError):
            c.generate_code("make a function", key="0xother")

    def test_generate_code_passes_with_owner(self, c):
        """generate_code works for the owner."""
        c._owner = "0xowner"
        with patch.object(c, '_run_cli', return_value="def foo(): pass") as mock:
            result = c.generate_code("make a function", key="0xowner")
            assert mock.called

    def test_generate_code_passes_default_owner(self):
        """generate_code works when no key passed (defaults to owner)."""
        c = Mod()
        c._find_claude = Mock(return_value='/usr/bin/echo')
        c._owner = c.key.address.lower()
        with patch.object(c, '_run_cli', return_value="def foo(): pass") as mock:
            result = c.generate_code("make a function")
            assert mock.called

    def test_refactor_requires_owner(self, c):
        """refactor requires owner permission."""
        c._owner = "0xowner"
        with pytest.raises(PermissionError):
            c.refactor("/tmp", key="0xother")

    def test_debug_no_owner_required(self, c):
        """debug is read-only, no owner needed."""
        c._owner = "0xowner"
        with patch.object(c, '_run_cli', return_value="found the bug") as mock:
            result = c.debug("/tmp", error="TypeError")
            assert mock.called
            assert "TypeError" in mock.call_args[0][0]

    def test_edit_file_requires_owner(self, c):
        """edit_file requires owner permission."""
        c._owner = "0xowner"
        with pytest.raises(PermissionError):
            c.edit_file("test.py", "add docstring", key="0xother")

    def test_run_task_calls_cli(self, c):
        """run_task invokes _run_cli."""
        with patch.object(c, '_run_cli', return_value="done") as mock:
            result = c.run_task("list files", path="/tmp")
            assert mock.called
            assert result == "done"

    def test_batch_process_returns_list(self, c):
        """batch_process returns a list of results."""
        with patch.object(c, '_run_cli', return_value="processed") as mock:
            results = c.batch_process(["a", "b", "c"], "summarize")
            assert len(results) == 3
            assert all(r['status'] == 'ok' for r in results)

    def test_batch_process_handles_errors(self, c):
        """batch_process captures errors per item."""
        with patch.object(c, '_run_cli', side_effect=RuntimeError("fail")) as mock:
            results = c.batch_process(["a"], "summarize")
            assert results[0]['status'] == 'error'
            assert 'fail' in results[0]['error']


class TestForwardPermissions:
    """Forward write-operation permission checks."""

    @pytest.fixture
    def c(self):
        mod = Mod()
        mod._owner = "0xowner"
        return mod

    def test_forward_edit_requires_owner(self, c):
        """Forward with edit keyword requires owner."""
        with pytest.raises(PermissionError):
            c.forward("Edit the login page", key="0xother", background=False)

    def test_forward_modify_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.forward("Modify the auth flow", key="0xother", background=False)

    def test_forward_fix_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.forward("Fix the bug in main.py", key="0xother", background=False)

    def test_forward_add_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.forward("Add a new feature", key="0xother", background=False)

    def test_forward_remove_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.forward("Remove the deprecated function", key="0xother", background=False)

    def test_forward_read_rejected_for_non_owner(self, c):
        """All forward calls require owner — even read-only queries."""
        with pytest.raises(PermissionError):
            c.forward("Explain the code structure", key="0xother")

    def test_forward_write_ok_for_owner_default(self):
        """Owner can call write operations without passing key (default)."""
        c = Mod()
        c._owner = c.key.address.lower()  # align owner with default key
        with patch.object(c, '_request', return_value={"id": "test-job"}) as mock:
            result = c.forward("Edit the login page")
            assert mock.called

    def test_forward_write_ok_with_owner_token(self):
        """Owner can call write operations by passing a signed token."""
        c = Mod()
        c._owner = c.key.address.lower()
        tok = c.token()
        with patch.object(c, '_request', return_value={"id": "test-job"}) as mock:
            result = c.forward("Edit the login page", key=tok)
            assert mock.called


class TestWhitelist:
    """Off-chain edit whitelist — owner-delegated trusted editors."""

    @pytest.fixture
    def c(self, tmp_path):
        mod = Mod()
        mod._owner = "0xowner000000000000000000000000000000owner"
        # Isolate the whitelist file from the real ~/.mod/claude/whitelist.json
        wl = tmp_path / 'whitelist.json'
        mod._whitelist_path = lambda: wl
        return mod

    EDITOR = "0x1111111111111111111111111111111111111111"
    OTHER = "0x2222222222222222222222222222222222222222"

    def test_whitelist_empty_by_default(self, c):
        assert c.editors() == []
        assert c.is_whitelisted(self.EDITOR) is False

    def test_add_editor_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.add_editor(self.EDITOR, key=self.OTHER)

    def test_add_editor_persists_and_is_whitelisted(self, c):
        res = c.add_editor(self.EDITOR, key="0xowner000000000000000000000000000000owner")
        assert self.EDITOR in res['whitelist']
        assert c.is_whitelisted(self.EDITOR) is True
        # persisted to disk and reloadable
        assert self.EDITOR in c._load_whitelist()

    def test_add_editor_rejects_bad_address(self, c):
        with pytest.raises(ValueError):
            c.add_editor("not-an-address", key="0xowner000000000000000000000000000000owner")

    def test_add_editor_idempotent(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        c.add_editor(self.EDITOR, key=owner)
        c.add_editor(self.EDITOR, key=owner)
        assert c._load_whitelist().count(self.EDITOR) == 1

    def test_remove_editor(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        c.add_editor(self.EDITOR, key=owner)
        res = c.remove_editor(self.EDITOR, key=owner)
        assert res['removed'] == self.EDITOR
        assert c.is_whitelisted(self.EDITOR) is False

    def test_remove_editor_requires_owner(self, c):
        with pytest.raises(PermissionError):
            c.remove_editor(self.EDITOR, key=self.OTHER)

    def test_is_trusted_owner_and_editor(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        assert c.is_trusted(owner) is True
        assert c.is_trusted(self.EDITOR) is False
        c.add_editor(self.EDITOR, key=owner)
        assert c.is_trusted(self.EDITOR) is True
        assert c.is_trusted(self.OTHER) is False

    def test_editor_is_not_owner(self, c):
        """Whitelisted editor gets edit access but is NOT the owner."""
        owner = "0xowner000000000000000000000000000000owner"
        c.add_editor(self.EDITOR, key=owner)
        assert c.is_owner(self.EDITOR) is False  # owner-only powers stay gated
        with pytest.raises(PermissionError):
            c.require_owner(self.EDITOR, "set_owner")

    def test_require_permission_allows_editor(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        # before whitelisting: confined to portal → denied for an orbit module
        with pytest.raises(PermissionError):
            c.require_permission(self.EDITOR, module="polymarket", operation="edit")
        c.add_editor(self.EDITOR, key=owner)
        # after whitelisting: full orbit edit access (no raise)
        c.require_permission(self.EDITOR, module="polymarket", operation="edit")

    def test_permissions_reports_editor_role(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        c.add_editor(self.EDITOR, key=owner)
        perms = c.permissions(self.EDITOR)
        assert perms['role'] == 'editor'
        assert perms['can_edit'] == 'all'
        assert perms['via'] == 'whitelist'

    def test_forward_works_for_editor(self, c):
        owner = "0xowner000000000000000000000000000000owner"
        c.add_editor(self.EDITOR, key=owner)
        with patch.object(c, '_request', return_value={"id": "job"}) as mock:
            c.forward("Edit the login page", key=self.EDITOR)
            assert mock.called


class TestSelfTest:
    """Built-in test() method."""

    def test_self_test_returns_dict(self):
        """test() returns structured results."""
        c = Mod()
        result = c.test()
        assert 'passed' in result
        assert 'failed' in result
        assert 'total' in result
        assert 'tests' in result
        assert result['total'] == result['passed'] + result['failed']

    @skip_server_closed
    @skip_no_working_cli
    def test_self_test_passes(self):
        """Built-in tests should pass.

        The built-in suite includes a job_lifecycle check that submits a real
        job, so it needs both an open job server and working CLI auth — skip
        cleanly when either is unavailable rather than reporting a failure that
        is really an environment gap."""
        c = Mod()
        result = c.test()
        assert result['passed'] > 0
        assert result['failed'] == 0, f"Self-test failures: {[t for t in result['tests'] if t['status'] == 'fail']}"


class TestEnsureEnv:
    """ensure_env() dependency checking."""

    @pytest.fixture
    def c(self):
        mod = Mod()
        return mod

    def test_ensure_env_skips_when_cached(self, c, tmp_path):
        """ensure_env reports cached when node_modules exist."""
        pkg = tmp_path / 'package.json'
        pkg.write_text('{}')
        (tmp_path / 'node_modules').mkdir()
        result = c.ensure_env(app_dir=str(tmp_path), api_dir=str(tmp_path))
        assert result['app_install']['ok'] is True
        assert result['app_install']['cached'] is True

    def test_ensure_env_runs_npm_install(self, c, tmp_path):
        """ensure_env runs npm install when node_modules missing."""
        pkg = tmp_path / 'package.json'
        pkg.write_text('{}')
        with patch('subprocess.run', return_value=MagicMock(returncode=0)) as mock_run:
            result = c.ensure_env(app_dir=str(tmp_path), api_dir=str(tmp_path))
        assert result['app_install']['ok'] is True
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ['npm', 'install']

    def test_ensure_env_reports_npm_failure(self, c, tmp_path):
        """ensure_env captures npm install errors."""
        pkg = tmp_path / 'package.json'
        pkg.write_text('{}')
        with patch('subprocess.run', return_value=MagicMock(returncode=1, stderr='ERESOLVE')):
            result = c.ensure_env(app_dir=str(tmp_path), api_dir=str(tmp_path))
        assert result['app_install']['ok'] is False
        assert 'ERESOLVE' in result['app_install']['error']

    def test_ensure_env_builds_rust_binary(self, c, tmp_path):
        """ensure_env runs cargo build when binary missing."""
        cargo = tmp_path / 'Cargo.toml'
        cargo.write_text('[package]\nname = "test"')
        (tmp_path / 'target' / 'release').mkdir(parents=True)
        # binary does NOT exist
        with patch('subprocess.run', return_value=MagicMock(returncode=0)) as mock_run:
            result = c.ensure_env(app_dir=str(tmp_path), api_dir=str(tmp_path))
        assert result['api_build']['ok'] is True
        # ensure_env resolves cargo via shutil.which, so the first arg is the
        # cargo binary (an absolute path like /root/.cargo/bin/cargo, or bare
        # 'cargo' when unresolved) — assert on the resolved binary + its args.
        cmd = mock_run.call_args[0][0]
        assert os.path.basename(cmd[0]) == 'cargo'
        assert cmd[1:] == ['build', '--release']

    def test_ensure_env_skips_build_when_binary_exists(self, c, tmp_path):
        """ensure_env skips cargo build when binary already exists."""
        cargo = tmp_path / 'Cargo.toml'
        cargo.write_text('[package]\nname = "test"')
        (tmp_path / 'target' / 'release').mkdir(parents=True)
        (tmp_path / 'target' / 'release' / 'claude-jobs').write_text('bin')
        result = c.ensure_env(app_dir=str(tmp_path), api_dir=str(tmp_path))
        assert result['api_build']['ok'] is True
        assert result['api_build']['cached'] is True


class TestCheckService:
    """_check_service() liveness polling."""

    def test_check_service_success(self):
        """Returns True when service responds."""
        c = Mod()
        with patch('urllib.request.urlopen') as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            assert c._check_service('http://localhost:9999', retries=1, interval=0) is True

    def test_check_service_failure(self):
        """Returns False when service never responds."""
        c = Mod()
        with patch('urllib.request.urlopen', side_effect=Exception('refused')):
            assert c._check_service('http://localhost:9999', retries=2, interval=0) is False

    def test_check_service_retries(self):
        """Retries until success."""
        c = Mod()
        call_count = [0]
        def flaky_open(req, timeout=2):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception('not ready')
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        with patch('urllib.request.urlopen', side_effect=flaky_open):
            assert c._check_service('http://localhost:9999', retries=5, interval=0) is True
        assert call_count[0] == 3


class TestServe:
    """serve() namespace registration, basePath, and health checks."""

    @pytest.fixture
    def c(self):
        mod = Mod()
        mod._owner = mod.key.address.lower()
        return mod

    def _serve_patches(self, c, mock_registry=None, popen_side_effect=None):
        """Common patch context for serve() tests.

        These tests exercise the Popen launch path + namespace registration, so
        we force `_serve_pm2` to report "not launched" — otherwise serve() takes
        the preferred pm2 path (which shells out via subprocess.run against the
        host's real pm2) and the Popen-oriented assertions never get a chance to
        run. The pm2 path has its own dedicated coverage in test_serve_uses_pm2.
        """
        if mock_registry is None:
            mock_registry = MagicMock()
        popen = popen_side_effect or MagicMock()
        return (
            patch.object(c, 'kill'),
            patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}, 'api_build': {'ok': True, 'cached': True}}),
            patch.object(c, '_check_service', return_value=True),
            patch.object(c, 'snapshot', return_value={'cid': 'QmTest'}),
            patch('subprocess.Popen', popen),
            patch('src.mod.m.mod', return_value=lambda: mock_registry),
            patch.object(c, '_serve_pm2', return_value=False),
        )

    def test_serve_registers_in_namespace(self, c):
        """serve() registers claude in both API and app namespaces."""
        mock_registry = MagicMock()
        patches = self._serve_patches(c, mock_registry=mock_registry)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            c.serve()
        # Derive expected URLs from config so this survives port changes
        # (e.g. app_port moved 8821 → 8823) rather than hardcoding.
        api_url = f"http://localhost:{c.config.get('port', 8820)}"
        app_url = f"http://localhost:{c.config.get('app_port', 8821)}"
        mock_registry.reg.assert_called_once_with('claude', api_url)
        mock_registry.reg_app.assert_called_once_with(
            'claude', app_url,
            owner=c.key.address, api_url=api_url)

    def test_serve_sets_base_path_env(self, c):
        """serve() passes NEXT_PUBLIC_BASE_PATH=/claude to the Next.js app."""
        popen_calls = []

        def capture_popen(*args, **kwargs):
            popen_calls.append(kwargs)
            return MagicMock()

        patches = self._serve_patches(c, popen_side_effect=capture_popen)
        with patches[0], patches[1], patches[2], patches[3], \
             patch('subprocess.Popen', side_effect=capture_popen), patches[5], patches[6]:
            c.serve()

        app_call = [call for call in popen_calls if call.get('env', {}).get('NEXT_PUBLIC_BASE_PATH')]
        assert len(app_call) == 1, "Expected one Popen call with NEXT_PUBLIC_BASE_PATH"
        assert app_call[0]['env']['NEXT_PUBLIC_BASE_PATH'] == '/claude'
        assert app_call[0]['env']['NEXT_PUBLIC_API_PORT'] == '8820'

    def test_serve_custom_ports(self, c):
        """serve() respects custom port args for registration."""
        mock_registry = MagicMock()
        patches = self._serve_patches(c, mock_registry=mock_registry)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            c.serve(api_port=9000, app_port=9001)
        mock_registry.reg.assert_called_once_with('claude', 'http://localhost:9000')
        mock_registry.reg_app.assert_called_once_with(
            'claude', 'http://localhost:9001',
            owner=c.key.address, api_url='http://localhost:9000')

    def test_serve_calls_ensure_env(self, c):
        """serve() calls ensure_env before starting services."""
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={}) as mock_env, \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'snapshot', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', return_value=lambda: MagicMock()):
            c.serve()
        mock_env.assert_called_once()

    def test_serve_checks_liveness(self, c):
        """serve() verifies API and app are live after starting."""
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}, 'api_build': {'ok': True, 'cached': True}}), \
             patch.object(c, '_check_service', return_value=True) as mock_check, \
             patch.object(c, 'snapshot', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        assert mock_check.call_count == 2  # once for API, once for app
        assert result['checks']['api']['live'] is True
        assert result['checks']['app']['live'] is True

    def test_serve_publishes_cid_when_live(self, c):
        """serve() snapshots and registers CID when all services are live."""
        mock_registry = MagicMock()
        mock_mod_registry = MagicMock()

        def mock_mod(name):
            if 'registry' in name and 'namespace' not in name:
                return lambda: mock_mod_registry
            return lambda: mock_registry

        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}, 'api_build': {'ok': True, 'cached': True}}), \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'snapshot', return_value={'cid': 'QmDeployCid'}) as mock_snap, \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', side_effect=mock_mod):
            result = c.serve()
        mock_snap.assert_called_once_with(description='deploy')
        assert result['cid'] == 'QmDeployCid'
        mock_mod_registry.register.assert_called_once()

    def test_serve_skips_cid_when_down(self, c):
        """serve() does not snapshot when services fail."""
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True}, 'api_build': {'ok': True}}), \
             patch.object(c, '_check_service', return_value=False), \
             patch.object(c, '_tail_log', return_value='Error'), \
             patch.object(c, 'snapshot') as mock_snap, \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        mock_snap.assert_not_called()
        assert 'cid' not in result

    def test_serve_reports_failed_service(self, c):
        """serve() reports check failures in results."""
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True}, 'api_build': {'ok': True}}), \
             patch.object(c, '_check_service', return_value=False), \
             patch.object(c, '_tail_log', return_value='Error: EADDRINUSE'), \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        assert result['checks']['api']['live'] is False
        assert 'EADDRINUSE' in result['checks']['api']['error']

    def test_serve_namespace_failure_non_fatal(self, c):
        """serve() continues even if namespace registration fails."""
        def failing_mod(name):
            raise RuntimeError("namespace unavailable")

        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={}), \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'snapshot', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', MagicMock()), \
             patch.object(c, '_serve_pm2', return_value=False), \
             patch('src.mod.m.mod', side_effect=failing_mod):
            result = c.serve()
        assert 'logs' in result

    def test_serve_uses_pm2_when_available(self, c):
        """serve() prefers the pm2 path: when _serve_pm2 launches, the Popen
        fallback is skipped and namespace registration still happens."""
        mock_registry = MagicMock()
        popen = MagicMock()

        def fake_pm2(api_port, app_port, dev, results):
            results['api'] = f'http://localhost:{api_port}'
            results['app'] = f'http://localhost:{app_port}'
            results['pm2'] = True
            return True

        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}, 'api_build': {'ok': True, 'cached': True}}), \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'snapshot', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', popen), \
             patch('src.mod.m.mod', return_value=lambda: mock_registry), \
             patch.object(c, '_serve_pm2', side_effect=fake_pm2) as mock_pm2:
            result = c.serve()
        mock_pm2.assert_called_once()
        # pm2 owns the processes — serve() must NOT also spawn via Popen.
        popen.assert_not_called()
        assert result.get('pm2') is True
        mock_registry.reg.assert_called_once_with('claude', 'http://localhost:8820')


class TestRepr:
    """String representation."""

    def test_repr(self):
        c = Mod()
        r = repr(c)
        assert 'Claude' in r
        assert 'api=' in r


# ── CLI Tests (require claude binary + auth) ────────────────────

class TestCLI:
    """Tests that invoke the actual claude CLI."""

    @pytest.fixture
    def c(self):
        return Mod()

    @skip_no_cli
    def test_find_claude(self, c):
        """Claude binary is found."""
        path = c._find_claude()
        assert os.path.exists(path)

    @skip_no_working_cli
    def test_forward_text(self, c, tmp_path):
        """Forward with text output."""
        result = c.forward(
            query="Say 'test passed' and nothing else",
            path=str(tmp_path),
            background=False,
            output_format="text",
            stream_output=False,
        )
        assert result is not None

    @skip_no_working_cli
    def test_analyze_code(self, c, tmp_path):
        """analyze_code runs."""
        (tmp_path / "test.py").write_text("def hello(): print('hi')\n")
        result = c.analyze_code(path=str(tmp_path), focus="code quality")
        assert result is not None

    @skip_no_auth
    def test_bg_spawns(self, c, tmp_path):
        """bg() spawns a process."""
        job = c.bg(
            prompt="Say 'bg test' and nothing else",
            path=str(tmp_path),
            log_dir=str(tmp_path / "logs"),
        )
        assert 'pid' in job
        assert 'log_file' in job
        assert os.path.exists(job['log_file'])


# ── Server Tests (require Rust job server) ───────────────────────

class TestServer:
    """Tests for the Rust job server integration."""

    @skip_no_server
    def test_health(self):
        c = Mod()
        h = c.health()
        assert h.get('status') == 'ok'

    @skip_server_closed
    def test_list_jobs(self):
        c = Mod()
        j = c.jobs()
        assert isinstance(j, list)

    @skip_server_closed
    @skip_no_working_cli
    def test_submit_and_check(self):
        c = Mod()
        job = c.submit(prompt="Say 'server test'", model="haiku")
        assert 'id' in job
        found = c.job(job['id'])
        assert found['id'] == job['id']


class TestPrompts:
    """Shareable prompt catalog (localfs / IPFS CID)."""

    @pytest.fixture
    def c(self, tmp_path):
        mod = Mod()
        mod._prompts_dir = tmp_path / 'prompts'
        return mod

    class _FakeIpfs:
        """Minimal in-memory localfs stand-in: put(text)->cid, get(cid)->text."""
        def __init__(self):
            self.store = {}
        def put(self, text):
            cid = "Qm" + hashlib_sha(text)
            self.store[cid] = text
            return cid
        def get(self, cid):
            return self.store[cid]

    @pytest.fixture
    def with_ipfs(self, monkeypatch):
        fake = TestPrompts._FakeIpfs()
        orig = m.m.mod
        def fake_mod(name, *a, **k):
            if name == 'ipfs':
                return lambda *aa, **kk: fake
            return orig(name, *a, **k)
        monkeypatch.setattr(m.m, 'mod', fake_mod)
        return fake

    def test_default_prompt_is_nice_and_showable(self, c):
        d = c.default_prompt()
        assert d['id'] == 'default'
        assert d['builtin'] is True
        assert len(d['body']) > 100          # a real prompt, not a stub
        assert 'Steve' in d['body']          # make Steve proud

    def test_empty_catalog(self, c):
        assert c.list_prompts() == []

    def test_save_and_list(self, c):
        e = c.save_prompt("My Reviewer", "You review code.", kind="system")
        assert e['id'].startswith('p_')
        assert e['title'] == "My Reviewer"
        got = c.list_prompts()
        assert len(got) == 1 and got[0]['id'] == e['id']

    def test_save_requires_title(self, c):
        with pytest.raises(ValueError):
            c.save_prompt("", "body")

    def test_save_tags_from_csv(self, c):
        e = c.save_prompt("Tagged", "body", tags="a, b ,c")
        assert e['tags'] == ["a", "b", "c"]

    def test_resave_same_title_updates_in_place(self, c):
        a = c.save_prompt("Dup", "v1")
        b = c.save_prompt("Dup", "v2")
        assert a['id'] == b['id']
        assert len(c.list_prompts()) == 1
        assert c.get_prompt(id=a['id'])['body'] == "v2"

    def test_list_filter_by_kind(self, c):
        c.save_prompt("sys", "s", kind="system")
        c.save_prompt("task", "t", kind="task")
        assert len(c.list_prompts(kind="system")) == 1
        assert len(c.list_prompts(kind="task")) == 1

    def test_get_by_id(self, c):
        e = c.save_prompt("Findme", "body")
        assert c.get_prompt(id=e['id'])['title'] == "Findme"
        assert c.get_prompt(id="nope") is None

    def test_save_gets_cid_with_localfs(self, c, with_ipfs):
        e = c.save_prompt("Shared", "You are shared.")
        assert e['cid'] and e['cid'].startswith("Qm")

    def test_share_returns_cid_and_gateway(self, c, with_ipfs):
        e = c.save_prompt("Shareme", "body text")
        s = c.share_prompt(e['id'])
        assert s['cid'] == e['cid']
        assert s['gateway'].endswith(e['cid'])

    def test_import_roundtrip(self, c, with_ipfs):
        e = c.save_prompt("Original", "portable body")
        # A fresh catalog imports it purely by CID.
        c2 = Mod()
        c2._prompts_dir = c._prompts_dir.parent / 'other'
        imported = c2.import_prompt(e['cid'], title="Copied")
        assert imported['body'] == "portable body"
        assert len(c2.list_prompts()) == 1

    def test_import_existing_cid_is_idempotent(self, c, with_ipfs):
        e = c.save_prompt("Once", "body")
        again = c.import_prompt(e['cid'])
        assert again['id'] == e['id']
        assert len(c.list_prompts()) == 1

    def test_get_by_cid_falls_back_to_localfs(self, c, with_ipfs):
        cid = c._localfs_put("floating body")
        got = c.get_prompt(cid=cid)
        assert got['body'] == "floating body"
        assert got['imported'] is True

    def test_delete_by_author(self, c):
        e = c.save_prompt("Trash", "body")          # author defaults to owner key
        res = c.delete_prompt(e['id'])
        assert res['deleted'] == e['id']
        assert c.list_prompts() == []

    def test_delete_missing(self, c):
        assert 'error' in c.delete_prompt("p_nope")

    def test_persists_across_instances(self, c):
        c.save_prompt("Sticky", "body")
        c2 = Mod()
        c2._prompts_dir = c._prompts_dir
        assert len(c2.list_prompts()) == 1


def hashlib_sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
