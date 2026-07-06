"""
Tests for the basebuilder module.

Run: pytest tests/test_basebuilder.py -v -s
"""
import pytest
import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
from basebuilder.mod import Mod


class TestInit:
    """Basic initialization."""

    def test_config_loads(self):
        c = Mod()
        assert c.config is not None
        assert c.name == 'basebuilder'

    def test_ports(self):
        c = Mod()
        assert c.port == 50200
        assert c.app_port == 50201

    def test_module_dir(self):
        c = Mod()
        d = c._module_dir()
        assert os.path.isdir(d)
        assert os.path.exists(os.path.join(d, 'config.json'))

    def test_info(self):
        c = Mod()
        info = c.info()
        assert info['name'] == 'basebuilder'
        assert 'port' in info
        assert 'app_port' in info

    def test_repr(self):
        c = Mod()
        r = repr(c)
        assert 'basebuilder' in r
        assert '50200' in r


class TestEnsureEnv:
    """Environment checking."""

    def test_skips_when_cached(self, tmp_path):
        c = Mod()
        app_dir = tmp_path / 'app'
        app_dir.mkdir()
        (app_dir / 'package.json').write_text('{}')
        (app_dir / 'node_modules').mkdir()
        with patch.object(c, '_find_app_dir', return_value=app_dir):
            result = c.ensure_env()
        assert result['app_install']['ok'] is True
        assert result['app_install']['cached'] is True

    def test_runs_npm_install(self, tmp_path):
        c = Mod()
        app_dir = tmp_path / 'app'
        app_dir.mkdir()
        (app_dir / 'package.json').write_text('{}')
        with patch.object(c, '_find_app_dir', return_value=app_dir), \
             patch('subprocess.run', return_value=MagicMock(returncode=0)) as mock_run:
            result = c.ensure_env()
        assert result['app_install']['ok'] is True
        mock_run.assert_called_once()

    def test_reports_failure(self, tmp_path):
        c = Mod()
        app_dir = tmp_path / 'app'
        app_dir.mkdir()
        (app_dir / 'package.json').write_text('{}')
        with patch.object(c, '_find_app_dir', return_value=app_dir), \
             patch('subprocess.run', return_value=MagicMock(returncode=1, stderr='ERR')):
            result = c.ensure_env()
        assert result['app_install']['ok'] is False


class TestCheckService:
    """Liveness polling."""

    def test_success(self):
        c = Mod()
        with patch('urllib.request.urlopen') as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            assert c._check_service('http://localhost:9999', retries=1, interval=0) is True

    def test_failure(self):
        c = Mod()
        with patch('urllib.request.urlopen', side_effect=Exception('refused')):
            assert c._check_service('http://localhost:9999', retries=2, interval=0) is False


class TestPublish:
    """CID snapshot and registry."""

    def test_collects_sources(self):
        c = Mod()
        sources = c._collect_sources()
        assert isinstance(sources, dict)
        assert 'config.json' in sources
        assert 'basebuilder/mod.py' in sources

    def test_publish_with_mock_ipfs(self):
        c = Mod()
        mock_ipfs = MagicMock()
        mock_ipfs.put.return_value = 'QmTestCid123'
        mock_reg = MagicMock()
        with patch('mod.mod', side_effect=lambda name: (lambda: mock_ipfs) if 'ipfs' in name else (lambda: mock_reg)):
            result = c.publish(description='test publish')
        assert result['cid'] == 'QmTestCid123'
        assert result['files'] > 0
        mock_ipfs.put.assert_called_once()


class TestServe:
    """Serve with health checks."""

    @pytest.fixture
    def c(self):
        return Mod()

    def test_serve_calls_ensure_env(self, c):
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={}) as mock_env, \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'publish', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', MagicMock()), \
             patch('mod.mod', return_value=lambda: MagicMock()):
            c.serve()
        mock_env.assert_called_once()

    def test_serve_checks_liveness(self, c):
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}}), \
             patch.object(c, '_check_service', return_value=True) as mock_check, \
             patch.object(c, 'publish', return_value={'cid': 'QmTest'}), \
             patch('subprocess.Popen', MagicMock()), \
             patch('mod.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        assert result['checks']['app']['live'] is True

    def test_serve_publishes_cid_when_live(self, c):
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True, 'cached': True}}), \
             patch.object(c, '_check_service', return_value=True), \
             patch.object(c, 'publish', return_value={'cid': 'QmDeployed'}) as mock_pub, \
             patch('subprocess.Popen', MagicMock()), \
             patch('mod.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        mock_pub.assert_called_once()
        assert result['cid'] == 'QmDeployed'

    def test_serve_skips_publish_when_down(self, c):
        with patch.object(c, 'kill'), \
             patch.object(c, 'ensure_env', return_value={'app_install': {'ok': True}}), \
             patch.object(c, '_check_service', return_value=False), \
             patch.object(c, '_tail_log', return_value='Error'), \
             patch.object(c, 'publish') as mock_pub, \
             patch('subprocess.Popen', MagicMock()), \
             patch('mod.mod', return_value=lambda: MagicMock()):
            result = c.serve()
        mock_pub.assert_not_called()
        assert 'cid' not in result


class TestKill:
    """Process termination."""

    def test_kill_returns_status(self):
        c = Mod()
        with patch('subprocess.run', return_value=MagicMock(stdout='')), \
             patch('mod.mod', return_value=lambda: MagicMock()):
            result = c.kill()
        assert result['status'] == 'killed'
        assert result['name'] == 'basebuilder'


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
