"""
Tests for pm/firecracker module.

Usage:
    cd ~/mod && python -m pytest mod/core/server/pm/pm/firecracker/test/ -v
"""

import json
import os
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Load Mod class from mod.py directly (HAS_MOD=False since we don't
# mock the real mod package — tests mock everything at instance level)
import importlib.util

_mod_path = Path(__file__).resolve().parent.parent / 'src' / 'mod.py'
_spec = importlib.util.spec_from_file_location('firecracker_mod', _mod_path)
_fc_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fc_module)
Mod = _fc_module.Mod


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def fc(tmp_path):
    """Create a Firecracker Mod instance with mocked prerequisites."""
    instance = object.__new__(Mod)
    instance.module_dir = Path(__file__).resolve().parent.parent
    instance.config = {
        'name': 'pm/firecracker',
        'mode': 'local',
        'kernel_image': '/tmp/vmlinux.bin',
        'rootfs_image': '/tmp/rootfs.ext4',
        'mod_rootfs': None,
        'default_memory': 1024,
        'default_vcpus': 2,
        'network': {'tap_prefix': 'fc-tap', 'subnet': '172.16.0.0/24',
                     'gateway': '172.16.0.1'},
    }
    instance.mode = 'local'
    instance.remote_host = None
    instance.store_dir = tmp_path / '.firecracker'
    instance.store_dir.mkdir(parents=True)
    instance.registry_path = instance.store_dir / 'vms.json'
    instance.sockets_dir = instance.store_dir / 'sockets'
    instance.sockets_dir.mkdir()
    instance.log_dir = instance.store_dir / 'logs'
    instance.log_dir.mkdir()
    instance.kernel_image = '/tmp/vmlinux.bin'
    instance.rootfs_image = '/tmp/rootfs.ext4'
    instance.mod_rootfs = None
    instance.network_config = {
        'tap_prefix': 'fc-tap', 'subnet': '172.16.0.0/24',
        'gateway': '172.16.0.1'}
    instance.prerequisites = {
        'kvm': True, 'firecracker': True,
        'kernel': True, 'rootfs': True}
    instance.prerequisites_ok = True
    instance._ns_registry = MagicMock()
    instance._ns_registry.namespace.return_value = {}
    return instance


@pytest.fixture
def fc_remote(fc):
    """Remote-mode instance."""
    fc.mode = 'remote'
    fc.remote_host = '10.0.1.5'
    fc.prerequisites = {'ssh': True, 'firecracker': True}
    fc.prerequisites_ok = True
    return fc


def _seed_registry(fc, vms):
    """Write VMs dict to the registry file."""
    with open(fc.registry_path, 'w') as f:
        json.dump(vms, f)


# ── TestHealth ───────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, fc):
        r = fc.health()
        assert r['status'] == 'ok'
        assert r['module'] == 'pm/firecracker'
        assert r['mode'] == 'local'
        assert r['vms'] == 0

    def test_health_degraded(self, fc):
        fc.prerequisites_ok = False
        fc.prerequisites['kvm'] = False
        r = fc.health()
        assert r['status'] == 'degraded'
        assert r['prerequisites']['kvm'] is False

    def test_health_counts_vms(self, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'status': 'running'}})
        r = fc.health()
        assert r['vms'] == 1


# ── TestStatus ───────────────────────────────────────────────────────

class TestStatus:
    def test_status_empty(self, fc):
        r = fc.status()
        assert r['module'] == 'pm/firecracker'
        assert r['total_vms'] == 0
        assert r['running'] == 0
        assert r['stopped'] == 0

    def test_status_counts(self, fc):
        _seed_registry(fc, {
            'a': {'status': 'running'},
            'b': {'status': 'running'},
            'c': {'status': 'stopped'},
        })
        r = fc.status()
        assert r['total_vms'] == 3
        assert r['running'] == 2
        assert r['stopped'] == 1

    def test_status_remote_shows_host(self, fc_remote):
        r = fc_remote.status()
        assert r['remote_host'] == '10.0.1.5'

    def test_status_local_hides_host(self, fc):
        r = fc.status()
        assert r['remote_host'] is None


# ── TestForward (PM Backend) ────────────────────────────────────────

class TestForward:
    @patch.object(Mod, 'exec', return_value={'success': True})
    @patch.object(Mod, '_setup_port_forward')
    @patch.object(Mod, 'deploy', return_value={'success': True})
    @patch.object(Mod, 'provision', return_value={'success': True})
    @patch.object(Mod, 'create')
    def test_forward_full_flow(self, mock_create, mock_prov, mock_deploy,
                                mock_fwd, mock_exec, fc):
        mock_create.return_value = {
            'success': True, 'vm_id': 'agent-123',
            'pid': 42, 'socket_path': '/tmp/s', 'ssh_port': 2222}

        r = fc.forward(mod='agent', port=8080)

        assert r['success'] is True
        assert r['mod'] == 'agent'
        assert r['port'] == 8080
        assert r['app_port'] == 8081
        assert r['vm_id'] == 'agent-123'
        assert r['url'] == 'http://0.0.0.0:8080'

        mock_create.assert_called_once()
        mock_prov.assert_called_once_with('agent-123')
        mock_deploy.assert_called_once_with('agent-123', 'agent')
        fc._ns_registry.reg.assert_called_with('agent', 'http://0.0.0.0:8080')

    @patch.object(Mod, 'create', return_value={'error': 'Prerequisites not met'})
    def test_forward_create_fails(self, mock_create, fc):
        r = fc.forward(mod='agent', port=8080)
        assert 'error' in r

    @patch.object(Mod, 'exec', return_value={'success': True})
    @patch.object(Mod, '_setup_port_forward')
    @patch.object(Mod, 'deploy', return_value={'success': True})
    @patch.object(Mod, 'provision', return_value={'error': 'SSH not available'})
    @patch.object(Mod, 'create')
    @patch.object(Mod, 'delete', return_value={'success': True})
    def test_forward_provision_fails(self, mock_delete, mock_create,
                                      mock_prov, mock_deploy, mock_fwd,
                                      mock_exec, fc):
        mock_create.return_value = {
            'success': True, 'vm_id': 'agent-123',
            'pid': 42, 'socket_path': '/tmp/s', 'ssh_port': 2222}

        r = fc.forward(mod='agent', port=8080)
        assert 'error' in r
        mock_delete.assert_called_once_with('agent-123')

    @patch.object(Mod, 'exec', return_value={'success': True})
    @patch.object(Mod, '_setup_port_forward')
    @patch.object(Mod, 'deploy', return_value={'error': 'Module not found'})
    @patch.object(Mod, 'provision', return_value={'success': True})
    @patch.object(Mod, 'create')
    @patch.object(Mod, 'delete', return_value={'success': True})
    def test_forward_deploy_fails(self, mock_delete, mock_create,
                                   mock_prov, mock_deploy, mock_fwd,
                                   mock_exec, fc):
        mock_create.return_value = {
            'success': True, 'vm_id': 'agent-123',
            'pid': 42, 'socket_path': '/tmp/s', 'ssh_port': 2222}

        r = fc.forward(mod='agent', port=8080)
        assert 'error' in r
        mock_delete.assert_called_once_with('agent-123')

    @patch.object(Mod, 'exec', return_value={'success': True})
    @patch.object(Mod, '_setup_port_forward')
    @patch.object(Mod, 'deploy', return_value={'success': True})
    @patch.object(Mod, 'server_exists', return_value=False)
    @patch.object(Mod, 'create')
    def test_forward_skips_provision_with_mod_rootfs(self, mock_create,
                                                      mock_exists,
                                                      mock_deploy, mock_fwd,
                                                      mock_exec, fc):
        fc.mod_rootfs = '/tmp/mod-rootfs.ext4'
        mock_create.return_value = {
            'success': True, 'vm_id': 'agent-123',
            'pid': 42, 'socket_path': '/tmp/s', 'ssh_port': 2222}
        # Seed registry so forward() can update it after create
        _seed_registry(fc, {
            'agent-123': {'id': 'agent-123', 'name': 'agent', 'status': 'running'}
        })

        with patch('pathlib.Path.exists', return_value=True):
            r = fc.forward(mod='agent', port=8080)

        assert r['success'] is True
        # No provision call since mod_rootfs is available

    @patch.object(Mod, 'exec', return_value={'success': True})
    @patch.object(Mod, '_setup_port_forward')
    @patch.object(Mod, 'deploy', return_value={'success': True})
    @patch.object(Mod, 'provision', return_value={'success': True})
    @patch.object(Mod, 'server_exists', return_value=False)
    @patch.object(Mod, 'create')
    def test_forward_stores_serve_metadata(self, mock_create, mock_exists,
                                            mock_prov, mock_deploy, mock_fwd,
                                            mock_exec, fc):
        mock_create.return_value = {
            'success': True, 'vm_id': 'agent-123',
            'pid': 42, 'socket_path': '/tmp/s', 'ssh_port': 2222}
        # Seed the registry so forward() can find the VM to update
        _seed_registry(fc, {
            'agent-123': {'id': 'agent-123', 'name': 'agent', 'status': 'running'}
        })

        r = fc.forward(mod='agent', port=8080)

        reg = fc._load_registry()
        assert reg['agent-123']['served_mod'] == 'agent'
        assert reg['agent-123']['port'] == 8080
        assert reg['agent-123']['app_port'] == 8081
        assert reg['agent-123']['serve_name'] == 'agent'


# ── TestPSServers ───────────────────────────────────────────────────

class TestPSServers:
    def test_ps_empty(self, fc):
        assert fc.ps() == []

    def test_ps_running_only(self, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent', 'status': 'running'},
            'vm2': {'name': 'bridge', 'status': 'stopped'},
            'vm3': {'name': 'query', 'status': 'running'},
        })
        names = fc.ps()
        assert sorted(names) == ['agent', 'query']

    def test_ps_prefers_serve_name(self, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent-123', 'serve_name': 'agent',
                    'status': 'running'},
        })
        assert fc.ps() == ['agent']

    def test_ps_search(self, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent', 'status': 'running'},
            'vm2': {'name': 'bridge', 'status': 'running'},
        })
        assert fc.ps(search='age') == ['agent']

    def test_servers_alias(self, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent', 'status': 'running'},
        })
        assert fc.servers() == fc.ps()

    def test_server_exists_true(self, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent', 'status': 'running'},
        })
        assert fc.server_exists('agent') is True

    def test_server_exists_false(self, fc):
        assert fc.server_exists('agent') is False

    def test_exists_alias(self, fc):
        assert fc.exists == fc.server_exists


# ── TestKillPM ──────────────────────────────────────────────────────

class TestKillPM:
    @patch.object(Mod, 'delete', return_value={'success': True})
    def test_kill_by_name(self, mock_delete, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'name': 'agent', 'serve_name': 'agent',
                    'status': 'running', 'pid': 123}
        })
        r = fc.kill('agent')
        mock_delete.assert_called_with('vm1')
        fc._ns_registry.dereg.assert_called_with('agent')

    def test_kill_not_found(self, fc):
        r = fc.kill('nonexistent')
        assert r['status'] == 'not_found'

    @patch.object(Mod, 'delete', return_value={'success': True})
    def test_kill_prefix(self, mock_delete, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'agent', 'serve_name': 'agent',
                    'status': 'running', 'pid': 1},
            'vm2': {'name': 'agent-2', 'serve_name': 'agent-2',
                    'status': 'running', 'pid': 2},
            'vm3': {'name': 'bridge', 'serve_name': 'bridge',
                    'status': 'running', 'pid': 3},
        })
        r = fc.kill('agent', prefix=True)
        assert 'agent' in r['killed']
        assert 'agent-2' in r['killed']
        assert 'bridge' not in r['killed']

    @patch.object(Mod, 'delete', return_value={'success': True})
    def test_kill_all(self, mock_delete, fc):
        _seed_registry(fc, {
            'vm1': {'name': 'a', 'status': 'running', 'pid': 1},
            'vm2': {'name': 'b', 'status': 'running', 'pid': 2},
        })
        r = fc.kill('all')
        assert r['status'] == 'all_vms_killed'
        assert mock_delete.call_count == 2


# ── TestNamespace ───────────────────────────────────────────────────

class TestNamespace:
    def test_namespace_returns_registry(self, fc):
        fc._ns_registry.namespace.return_value = {'agent': 'http://0.0.0.0:8080'}
        r = fc.namespace()
        assert r == {'agent': 'http://0.0.0.0:8080'}

    def test_namespace_no_registry(self, fc):
        fc._ns_registry = None
        assert fc.namespace() == {}


# ── TestRestart ─────────────────────────────────────────────────────

class TestRestart:
    def test_restart_not_found(self, fc):
        r = fc.restart('nonexistent')
        assert r['status'] == 'not_found'

    @patch.object(Mod, 'forward', return_value={'success': True})
    @patch.object(Mod, 'kill', return_value={'success': True})
    def test_restart_re_serves(self, mock_kill, mock_forward, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'name': 'agent', 'serve_name': 'agent',
                    'served_mod': 'agent', 'port': 8080, 'app_port': 8081,
                    'memory': 1024, 'vcpus': 2, 'status': 'running'}
        })
        r = fc.restart('agent')
        mock_kill.assert_called_with('agent')
        mock_forward.assert_called_once()
        kwargs = mock_forward.call_args[1]
        assert kwargs['mod'] == 'agent'
        assert kwargs['port'] == 8080

    def test_restart_no_served_mod(self, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'name': 'test', 'status': 'running'}
        })
        r = fc.restart('test')
        assert 'error' in r


# ── TestProvision ───────────────────────────────────────────────────

class TestProvision:
    def test_provision_vm_not_found(self, fc):
        r = fc.provision('nonexistent')
        assert 'error' in r

    @patch.object(Mod, '_wait_for_ssh', return_value=False)
    def test_provision_ssh_timeout(self, mock_ssh, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'status': 'running'}})
        r = fc.provision('vm1', timeout=1)
        assert 'error' in r
        assert 'SSH' in r['error']

    @patch.object(Mod, 'exec', return_value={'success': True, 'stdout': '', 'stderr': '', 'returncode': 0})
    @patch.object(Mod, '_wait_for_ssh', return_value=True)
    def test_provision_success(self, mock_ssh, mock_exec, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'status': 'running'}})
        r = fc.provision('vm1')
        assert r['success'] is True
        # Should have run multiple provision commands
        assert mock_exec.call_count >= 4

        # VM should be marked provisioned
        reg = fc._load_registry()
        assert reg['vm1']['provisioned'] is True

    @patch.object(Mod, 'exec', return_value={'success': True, 'stdout': '', 'stderr': '', 'returncode': 0})
    @patch.object(Mod, '_wait_for_ssh', return_value=True)
    def test_provision_cached(self, mock_ssh, mock_exec, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'status': 'running', 'provisioned': True}
        })
        r = fc.provision('vm1')
        assert r['success'] is True
        assert r['cached'] is True
        mock_exec.assert_not_called()


# ── TestDeploy ──────────────────────────────────────────────────────

class TestDeploy:
    def test_deploy_vm_not_found(self, fc):
        r = fc.deploy('nonexistent', 'agent')
        assert 'error' in r

    @patch('subprocess.run')
    @patch.object(Mod, 'exec', return_value={'success': True, 'stdout': '', 'stderr': '', 'returncode': 0})
    def test_deploy_success(self, mock_exec, mock_run, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'ssh_port': 2222, 'status': 'running'}
        })
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')

        with patch('os.path.exists', return_value=True):
            r = fc.deploy('vm1', 'agent')

        assert r['success'] is True
        assert r['mod'] == 'agent'
        assert r['target'] == '/root/mod/mod/orbit/agent'
        # SCP should have been called
        scp_call = mock_run.call_args[0][0]
        assert scp_call[0] == 'scp'

    @patch('subprocess.run')
    @patch.object(Mod, 'exec', return_value={'success': True, 'stdout': '', 'stderr': '', 'returncode': 0})
    def test_deploy_scp_failure(self, mock_exec, mock_run, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'ssh_port': 2222, 'status': 'running'}
        })
        mock_run.return_value = MagicMock(
            returncode=1, stdout='', stderr='Permission denied')

        with patch('os.path.exists', return_value=True):
            r = fc.deploy('vm1', 'agent')

        assert 'error' in r
        assert 'SCP failed' in r['error']

    @patch.object(Mod, 'exec', return_value={'success': True, 'stdout': '', 'stderr': '', 'returncode': 0})
    def test_deploy_module_not_found(self, mock_exec, fc):
        """deploy() fails gracefully when module dir doesn't exist."""
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'ssh_port': 2222, 'status': 'running'}
        })
        # dirpath returns None → triggers "Module not found"
        with patch.object(_fc_module, 'm') as mock_m:
            mock_m.dirpath.return_value = None
            r = fc.deploy('vm1', 'nonexistent_module')
        assert 'error' in r
        assert 'Module not found' in r['error']


# ── TestRegistry ─────────────────────────────────────────────────────

class TestRegistry:
    def test_empty_registry(self, fc):
        assert fc._load_registry() == {}

    def test_save_and_load(self, fc):
        data = {'vm1': {'id': 'vm1', 'name': 'test', 'status': 'running'}}
        fc._save_registry(data)
        loaded = fc._load_registry()
        assert loaded == data

    def test_corrupt_registry(self, fc):
        fc.registry_path.write_text('not json')
        assert fc._load_registry() == {}

    def test_cleanup_dead_vms(self, fc):
        _seed_registry(fc, {
            'alive': {'pid': os.getpid(), 'status': 'running'},
            'dead': {'pid': 99999999, 'status': 'running'},
        })
        fc._cleanup_dead_vms()
        reg = fc._load_registry()
        assert reg['alive']['status'] == 'running'
        assert reg['dead']['status'] == 'stopped'

    def test_cleanup_skips_stopped(self, fc):
        _seed_registry(fc, {
            'stopped': {'pid': 99999999, 'status': 'stopped'},
        })
        fc._cleanup_dead_vms()
        reg = fc._load_registry()
        assert reg['stopped']['status'] == 'stopped'


# ── TestCreate ───────────────────────────────────────────────────────

class TestCreate:
    def test_create_fails_without_prerequisites(self, fc):
        fc.prerequisites_ok = False
        fc.prerequisites['kvm'] = False
        r = fc.create(name='test')
        assert 'error' in r
        assert 'Prerequisites' in r['error']

    @patch('builtins.open', create=True)
    @patch.object(Mod, '_find_free_port', return_value=2222)
    @patch.object(Mod, '_api_call', return_value={'success': True})
    @patch.object(Mod, '_wait_for_socket', return_value=True)
    @patch.object(Mod, '_save_registry')
    @patch.object(Mod, '_load_registry', return_value={})
    @patch('subprocess.Popen')
    def test_create_success(self, mock_popen, mock_load, mock_save,
                            mock_wait, mock_api, mock_port, mock_open, fc):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        # Kernel and rootfs exist
        with patch('pathlib.Path.exists', return_value=True):
            r = fc.create(name='web', memory=1024, vcpus=2)

        assert r['success'] is True
        assert r['pid'] == 42
        assert r['ssh_port'] == 2222
        assert 'web-' in r['vm_id']

        # Verify registry was saved
        assert mock_save.called
        saved = mock_save.call_args[0][0]
        vm_id = r['vm_id']
        assert saved[vm_id]['name'] == 'web'
        assert saved[vm_id]['memory'] == 1024
        assert saved[vm_id]['vcpus'] == 2

    def test_create_missing_kernel(self, fc):
        fc.kernel_image = '/nonexistent/vmlinux.bin'
        r = fc.create(name='test')
        assert 'error' in r
        assert 'Kernel not found' in r['error']

    def test_create_missing_rootfs(self, fc):
        fc.rootfs_image = '/nonexistent/rootfs.ext4'
        with patch('pathlib.Path.exists', side_effect=lambda: True):
            # kernel exists, rootfs doesn't
            def exists_side(self=None):
                return True
            with patch.object(Path, 'exists') as mock_exists:
                mock_exists.side_effect = [True, False]  # kernel ok, rootfs not
                r = fc.create(name='test', kernel='/tmp/vmlinux.bin',
                              rootfs='/nonexistent/rootfs.ext4')
        assert 'error' in r
        assert 'Rootfs not found' in r['error']

    @patch('builtins.open', create=True)
    @patch.object(Mod, '_find_free_port', return_value=2222)
    @patch.object(Mod, '_wait_for_socket', return_value=False)
    @patch('subprocess.Popen')
    def test_create_socket_timeout(self, mock_popen, mock_wait,
                                   mock_port, mock_open, fc):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        with patch('pathlib.Path.exists', return_value=True):
            r = fc.create(name='test')

        assert 'error' in r
        assert 'socket' in r['error'].lower()
        mock_proc.kill.assert_called_once()

    @patch('builtins.open', create=True)
    @patch.object(Mod, '_find_free_port', return_value=2222)
    @patch.object(Mod, '_configure_vm', return_value={'error': 'config fail'})
    @patch.object(Mod, '_wait_for_socket', return_value=True)
    @patch('subprocess.Popen')
    def test_create_config_failure(self, mock_popen, mock_wait,
                                   mock_cfg, mock_port, mock_open, fc):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        with patch('pathlib.Path.exists', return_value=True):
            r = fc.create(name='test')

        assert 'error' in r
        assert r['error'] == 'config fail'
        mock_proc.kill.assert_called_once()

    @patch('builtins.open', create=True)
    @patch.object(Mod, '_find_free_port', return_value=2222)
    @patch.object(Mod, '_api_call', return_value={'success': True})
    @patch.object(Mod, '_wait_for_socket', return_value=True)
    @patch.object(Mod, '_save_registry')
    @patch.object(Mod, '_load_registry', return_value={})
    @patch('subprocess.Popen')
    def test_create_uses_mod_rootfs(self, mock_popen, mock_load, mock_save,
                                     mock_wait, mock_api, mock_port,
                                     mock_open, fc):
        """create() should prefer mod_rootfs when available."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc
        fc.mod_rootfs = '/tmp/mod-rootfs.ext4'

        with patch('pathlib.Path.exists', return_value=True):
            r = fc.create(name='test')

        assert r['success'] is True
        saved = mock_save.call_args[0][0]
        vm_id = r['vm_id']
        assert saved[vm_id]['rootfs'] == '/tmp/mod-rootfs.ext4'


# ── TestList ─────────────────────────────────────────────────────────

class TestList:
    def test_list_empty(self, fc):
        r = fc.list()
        assert r['count'] == 0
        assert r['vms'] == []

    def test_list_populated(self, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'name': 'a'},
            'vm2': {'id': 'vm2', 'name': 'b'},
        })
        r = fc.list()
        assert r['count'] == 2
        assert len(r['vms']) == 2


# ── TestGet ──────────────────────────────────────────────────────────

class TestGet:
    def test_get_found(self, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'name': 'web'}})
        r = fc.get('vm1')
        assert r['name'] == 'web'

    def test_get_not_found(self, fc):
        r = fc.get('nonexistent')
        assert 'error' in r


# ── TestStop ─────────────────────────────────────────────────────────

class TestStop:
    def test_stop_not_found(self, fc):
        r = fc.stop('nonexistent')
        assert 'error' in r

    @patch('subprocess.run', return_value=MagicMock(stdout='', stderr=''))
    @patch('os.kill')
    def test_stop_running(self, mock_kill, mock_run, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'pid': 12345, 'status': 'running'}
        })
        r = fc.stop('vm1')
        assert r['success'] is True
        reg = fc._load_registry()
        assert reg['vm1']['status'] == 'stopped'
        assert 'stopped_at' in reg['vm1']

    def test_stop_already_stopped(self, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'pid': 12345, 'status': 'stopped'}
        })
        r = fc.stop('vm1')
        assert r['success'] is True

    @patch('subprocess.run', return_value=MagicMock(stdout='', stderr=''))
    @patch('os.kill', side_effect=ProcessLookupError)
    def test_stop_dead_process(self, mock_kill, mock_run, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'pid': 99999999, 'status': 'running'}
        })
        r = fc.stop('vm1')
        assert r['success'] is True


# ── TestDelete ───────────────────────────────────────────────────────

class TestDelete:
    @patch('subprocess.run', return_value=MagicMock(stdout='', stderr=''))
    @patch('os.kill')
    def test_delete_removes_from_registry(self, mock_kill, mock_run, fc):
        sock = fc.sockets_dir / 'vm1.sock'
        sock.touch()
        log = fc.log_dir / 'vm1.log'
        log.touch()
        _seed_registry(fc, {
            'vm1': {
                'id': 'vm1', 'pid': 12345, 'status': 'running',
                'socket_path': str(sock), 'log_path': str(log)
            }
        })
        r = fc.delete('vm1')
        assert r['success'] is True
        assert fc._load_registry() == {}
        assert not sock.exists()
        assert not log.exists()

    def test_delete_nonexistent(self, fc):
        r = fc.delete('nonexistent')
        assert r['success'] is True

    @patch('subprocess.run', return_value=MagicMock(stdout='', stderr=''))
    @patch('os.kill')
    def test_delete_no_socket_file(self, mock_kill, mock_run, fc):
        _seed_registry(fc, {
            'vm1': {
                'id': 'vm1', 'pid': 12345, 'status': 'stopped',
                'socket_path': '/tmp/gone.sock', 'log_path': '/tmp/gone.log'
            }
        })
        r = fc.delete('vm1')
        assert r['success'] is True
        assert fc._load_registry() == {}


# ── TestExec ─────────────────────────────────────────────────────────

class TestExec:
    def test_exec_not_found(self, fc):
        r = fc.exec('nonexistent', 'ls')
        assert 'error' in r

    def test_exec_no_ssh_port(self, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1'}})
        r = fc.exec('vm1', 'ls')
        assert 'error' in r
        assert 'SSH port' in r['error']

    @patch('subprocess.run')
    def test_exec_success(self, mock_run, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'ssh_port': 2222}})
        mock_run.return_value = MagicMock(
            returncode=0, stdout='hello\n', stderr='')
        r = fc.exec('vm1', 'echo hello')
        assert r['success'] is True
        assert r['stdout'] == 'hello\n'
        assert r['returncode'] == 0

        # Verify SSH command
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'ssh'
        assert '-p' in cmd
        assert '2222' in cmd
        assert 'root@localhost' in cmd
        assert 'echo hello' in cmd

    @patch('subprocess.run')
    def test_exec_failure(self, mock_run, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'ssh_port': 2222}})
        mock_run.return_value = MagicMock(
            returncode=1, stdout='', stderr='command not found')
        r = fc.exec('vm1', 'badcmd')
        assert r['success'] is False
        assert r['returncode'] == 1

    @patch('subprocess.run')
    def test_exec_remote_mode(self, mock_run, fc_remote):
        _seed_registry(fc_remote, {'vm1': {'id': 'vm1', 'ssh_port': 2222}})
        mock_run.return_value = MagicMock(
            returncode=0, stdout='ok', stderr='')
        r = fc_remote.exec('vm1', 'uname')
        cmd = mock_run.call_args[0][0]
        assert 'root@10.0.1.5' in cmd


# ── TestLogs ─────────────────────────────────────────────────────────

class TestLogs:
    def test_logs_not_found(self, fc):
        r = fc.logs('nonexistent')
        assert 'error' in r

    def test_logs_no_file(self, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'log_path': '/tmp/nope.log'}})
        r = fc.logs('vm1')
        assert r['logs'] == ''

    def test_logs_reads_file(self, fc):
        log = fc.log_dir / 'vm1.log'
        log.write_text('line1\nline2\nline3\nline4\nline5\n')
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'log_path': str(log)}})
        r = fc.logs('vm1', lines=3)
        assert 'line3' in r['logs']
        assert 'line4' in r['logs']
        assert 'line5' in r['logs']


# ── TestRemoteMode ───────────────────────────────────────────────────

class TestRemoteMode:
    @patch('subprocess.run')
    def test_remote_run(self, mock_run, fc_remote):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='ok', stderr='')
        r = fc_remote._remote_run(['which', 'firecracker'])
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'ssh'
        assert cmd[1] == '10.0.1.5'
        assert cmd[2:] == ['which', 'firecracker']

    @patch('subprocess.run')
    def test_api_call_remote(self, mock_run, fc_remote):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"success": true}', stderr='')
        r = fc_remote._api_call('/tmp/test.sock', 'GET', '/machine-config')
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'ssh'
        assert cmd[1] == '10.0.1.5'
        assert 'curl' in cmd

    @patch('subprocess.run')
    def test_ssh_connection_ok(self, mock_run, fc_remote):
        mock_run.return_value = MagicMock(returncode=0, stdout='ok', stderr='')
        assert fc_remote._test_ssh() is True

    @patch('subprocess.run')
    def test_ssh_connection_fail(self, mock_run, fc_remote):
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='err')
        assert fc_remote._test_ssh() is False

    def test_ssh_no_host(self, fc):
        fc.mode = 'remote'
        fc.remote_host = None
        assert fc._test_ssh() is False


# ── TestHelpers ──────────────────────────────────────────────────────

class TestHelpers:
    def test_generate_mac(self, fc):
        mac = fc._generate_mac()
        parts = mac.split(':')
        assert len(parts) == 6
        assert parts[0] == '02'  # locally administered
        assert all(len(p) == 2 for p in parts)

    def test_generate_mac_unique(self, fc):
        macs = {fc._generate_mac() for _ in range(50)}
        assert len(macs) > 1  # statistically should be all unique

    def test_wait_for_socket_exists(self, fc):
        sock = fc.sockets_dir / 'test.sock'
        sock.touch()
        assert fc._wait_for_socket(str(sock), timeout=1) is True

    def test_wait_for_socket_timeout(self, fc):
        assert fc._wait_for_socket('/tmp/noexist.sock', timeout=0.2) is False

    @patch('socket.socket')
    def test_find_free_port(self, mock_socket_cls, fc):
        mock_sock = MagicMock()
        mock_socket_cls.return_value.__enter__ = MagicMock(
            return_value=mock_sock)
        mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_sock.bind.return_value = None

        port = fc._find_free_port(start=3000)
        assert port == 3000

    @patch('subprocess.run')
    def test_which_found(self, mock_run, fc):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='/usr/bin/firecracker')
        assert fc._which('firecracker') == '/usr/bin/firecracker'

    @patch('subprocess.run')
    def test_which_not_found(self, mock_run, fc):
        mock_run.return_value = MagicMock(returncode=1, stdout='')
        assert fc._which('firecracker') is None

    def test_find_vm_by_name(self, fc):
        _seed_registry(fc, {
            'vm1': {'id': 'vm1', 'name': 'agent', 'serve_name': 'agent'},
            'vm2': {'id': 'vm2', 'name': 'bridge'},
        })
        assert fc._find_vm_by_name('agent') == 'vm1'
        assert fc._find_vm_by_name('bridge') == 'vm2'
        assert fc._find_vm_by_name('nope') is None

    @patch.object(Mod, 'exec')
    def test_wait_for_ssh_success(self, mock_exec, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'ssh_port': 2222}})
        mock_exec.return_value = {'success': True}
        assert fc._wait_for_ssh('vm1', timeout=5) is True

    @patch.object(Mod, 'exec')
    def test_wait_for_ssh_timeout(self, mock_exec, fc):
        _seed_registry(fc, {'vm1': {'id': 'vm1', 'ssh_port': 2222}})
        mock_exec.return_value = {'success': False}
        assert fc._wait_for_ssh('vm1', timeout=1) is False

    @patch('subprocess.Popen')
    def test_setup_port_forward(self, mock_popen, fc):
        fc._setup_port_forward(2222, 8080)
        cmd = mock_popen.call_args[0][0]
        assert 'ssh' in cmd
        assert '-fN' in cmd
        assert '8080:localhost:8080' in cmd[cmd.index('-L') + 1]

    @patch('subprocess.run')
    @patch('os.kill')
    def test_cleanup_tunnels(self, mock_kill, mock_run, fc):
        mock_run.return_value = MagicMock(stdout='12345\n', stderr='')
        fc._cleanup_tunnels({'ssh_port': 2222})
        mock_kill.assert_called_with(12345, 15)  # SIGTERM


# ── TestAPICall ──────────────────────────────────────────────────────

class TestAPICall:
    @patch('subprocess.run')
    def test_api_call_success(self, mock_run, fc):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"status": "ok"}', stderr='')
        r = fc._api_call('/tmp/test.sock', 'GET', '/machine-config')
        assert r == {'status': 'ok'}

    @patch('subprocess.run')
    def test_api_call_with_data(self, mock_run, fc):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='', stderr='')
        r = fc._api_call('/tmp/test.sock', 'PUT', '/machine-config',
                         {'vcpu_count': 2, 'mem_size_mib': 512})
        assert r == {'success': True}
        cmd = mock_run.call_args[0][0]
        assert '-d' in cmd

    @patch('subprocess.run')
    def test_api_call_curl_error(self, mock_run, fc):
        mock_run.return_value = MagicMock(
            returncode=7, stdout='', stderr='Connection refused')
        r = fc._api_call('/tmp/test.sock', 'GET', '/machine-config')
        assert 'error' in r

    @patch('subprocess.run')
    def test_api_call_non_json(self, mock_run, fc):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='not json', stderr='')
        r = fc._api_call('/tmp/test.sock', 'GET', '/something')
        assert r == {'raw': 'not json'}


# ── TestConfigureVM ──────────────────────────────────────────────────

class TestConfigureVM:
    @patch.object(Mod, '_api_call', return_value={'success': True})
    def test_configure_all_steps(self, mock_api, fc):
        r = fc._configure_vm('/tmp/test.sock', 512, 2,
                             '/tmp/kernel', '/tmp/rootfs', 2222)
        assert r == {'success': True}
        assert mock_api.call_count == 4  # machine, boot, drive, network

        calls = [c[0] for c in mock_api.call_args_list]
        assert calls[0][2] == '/machine-config'
        assert calls[1][2] == '/boot-source'
        assert calls[2][2] == '/drives/rootfs'
        assert calls[3][2] == '/network-interfaces/eth0'

    @patch.object(Mod, '_api_call')
    def test_configure_stops_on_error(self, mock_api, fc):
        mock_api.side_effect = [
            {'success': True},       # machine-config ok
            {'error': 'bad kernel'},  # boot-source fails
        ]
        r = fc._configure_vm('/tmp/test.sock', 512, 2,
                             '/tmp/kernel', '/tmp/rootfs', 2222)
        assert r == {'error': 'bad kernel'}
        assert mock_api.call_count == 2  # stopped after boot-source
