"""
Firecracker microVM management for the mod framework.

Full lifecycle management of Firecracker microVMs with local (Linux/KVM)
and remote (SSH) execution modes. Serves as a pluggable PM backend for
running mod modules (API + app) inside lightweight VMs.

Usage:
    m pm/firecracker                                    # status
    m pm/firecracker/create name=test                   # create VM
    m pm/firecracker/serve mod=agent port=8080          # serve module in VM
    m pm/firecracker/list                               # list VMs
    m pm/firecracker/exec vm_id=abc command=ls          # run command in VM
    m pm/firecracker/stop vm_id=abc                     # stop VM
    m pm/firecracker/delete vm_id=abc                   # delete VM
    m pm/firecracker/provision vm_id=abc                # install mod protocol
    m pm/firecracker/build_rootfs                       # build mod-ready rootfs
"""

import fcntl
import json
import os
import random
import signal
import socket
import subprocess
import time
from pathlib import Path

try:
    import mod as m
    HAS_MOD = True
except ImportError:
    HAS_MOD = False


class Mod:
    """Firecracker microVM manager and PM backend.

    Serves as both a standalone VM lifecycle manager and a pluggable
    PM backend for the mod serve system. Runs modules inside lightweight
    microVMs with full mod protocol support (Python, Node.js, SSH, mod CLI).
    """

    description = 'Firecracker microVM lifecycle management'

    # ── Init ─────────────────────────────────────────────────────────

    def __init__(self, config=None, mode=None, remote_host=None,
                 store_dir=None, registry='server.namespace'):
        self.module_dir = Path(__file__).parent.parent
        self.config = config or self._load_config()

        self.mode = mode or self.config.get('mode', 'local')
        self.remote_host = remote_host or self.config.get('remote_host')

        self.store_dir = Path(store_dir) if store_dir else Path.home() / '.firecracker'
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.store_dir / 'vms.json'
        self.sockets_dir = self.store_dir / 'sockets'
        self.sockets_dir.mkdir(exist_ok=True)
        self.log_dir = self.store_dir / 'logs'
        self.log_dir.mkdir(exist_ok=True)

        self.kernel_image = self.config.get(
            'kernel_image', '/usr/local/share/firecracker/vmlinux.bin')
        self.rootfs_image = self.config.get(
            'rootfs_image', '/usr/local/share/firecracker/rootfs.ext4')
        self.mod_rootfs = self.config.get('mod_rootfs')
        self.network_config = self.config.get('network', {
            'tap_prefix': 'fc-tap',
            'subnet': '172.16.0.0/24',
            'gateway': '172.16.0.1',
        })

        # Namespace registry for service discovery (Routy/Caddy)
        self._ns_registry = None
        if HAS_MOD:
            try:
                self._ns_registry = m.mod(registry)()
            except Exception:
                pass

        self.prerequisites = self._check_prerequisites()
        self.prerequisites_ok = all(self.prerequisites.values())

        if self.mode == 'local':
            self._cleanup_dead_vms()

    # ── PM Backend Interface ────────────────────────────────────────
    # Matches Docker/PM2 backend contract so firecracker is a drop-in
    # replacement:  m.serve('agent', pm='pm/firecracker')

    def forward(self, mod='api', port=None, params=None, key=None,
                memory=None, vcpus=None, name=None, app_port=None,
                **kwargs):
        """Serve a mod module inside a Firecracker microVM.

        Creates a VM, provisions the mod framework (if needed), deploys
        the module code, sets up port forwarding, and runs `m serve`
        inside. Registers the service in the namespace for gateway/proxy
        discovery.

        Args:
            mod:      Module to serve (e.g. 'agent', 'bridge')
            port:     API port on host
            params:   Extra params forwarded to m serve
            key:      Namespace registration key
            memory:   VM memory in MiB (default from config or 1024)
            vcpus:    VM vCPU count (default from config or 2)
            name:     Service name (defaults to mod name)
            app_port: Next.js app port (defaults to port+1)
        """
        params = params or {}
        name = name or mod
        memory = memory or self.config.get('default_memory', 1024)
        vcpus = vcpus or self.config.get('default_vcpus', 2)

        if HAS_MOD:
            port = port or m.free_port()
        else:
            port = port or self._find_free_port()
        app_port = app_port or (port + 1)

        # Kill existing VM for this module
        if self.server_exists(name):
            self.kill(name)

        # Prefer mod-ready rootfs if available
        rootfs = None
        if self.mod_rootfs and Path(self.mod_rootfs).exists():
            rootfs = self.mod_rootfs

        # Create VM
        result = self.create(
            name=name, memory=memory, vcpus=vcpus, rootfs=rootfs)
        if 'error' in result:
            return result

        vm_id = result['vm_id']
        ssh_port = result['ssh_port']

        # Provision mod framework if rootfs isn't pre-built
        if not rootfs or rootfs == self.rootfs_image:
            prov = self.provision(vm_id)
            if prov.get('error'):
                self.delete(vm_id)
                return prov

        # Deploy module files into VM
        dep = self.deploy(vm_id, mod)
        if dep.get('error'):
            self.delete(vm_id)
            return dep

        # Port forwarding via SSH tunnels (host:port → VM:port)
        self._setup_port_forward(ssh_port, port)
        self._setup_port_forward(ssh_port, app_port)

        # Build and run serve command inside VM
        serve_params = {'port': port, 'remote': 'False'}
        serve_params.update(params)
        if key:
            serve_params['key'] = key
        param_str = ' '.join(f'{k}={v}' for k, v in serve_params.items()
                             if v is not None)
        serve_cmd = (f'nohup m serve {mod} {param_str} '
                     f'> /tmp/{name}_serve.log 2>&1 &')
        self.exec(vm_id, serve_cmd)

        # Register in namespace for gateway discovery
        url = f'http://0.0.0.0:{port}'
        if self._ns_registry:
            self._ns_registry.reg(name, url)

        # Store serve metadata in VM registry
        reg = self._load_registry()
        if vm_id in reg:
            reg[vm_id].update({
                'served_mod': mod,
                'port': port,
                'app_port': app_port,
                'serve_name': name,
            })
            self._save_registry(reg)

        return {
            'success': True,
            'vm_id': vm_id,
            'name': name,
            'mod': mod,
            'port': port,
            'app_port': app_port,
            'ssh_port': ssh_port,
            'url': url,
        }

    # Alias for serve system compatibility
    serve = forward

    def ps(self, search=None):
        """List names of running VMs (PM interface)."""
        registry = self._load_registry()
        names = []
        for vm in registry.values():
            if vm.get('status') == 'running':
                names.append(vm.get('serve_name') or vm.get('name'))
        if search:
            names = [n for n in names if search in n]
        return names

    def servers(self, search=None, **kwargs):
        """List server VMs (alias for ps)."""
        return self.ps(search=search)

    def server_exists(self, name):
        """Check if a VM serving this module exists and is running."""
        return name in self.ps()

    exists = server_exists

    def kill(self, name, update=True, prefix=False):
        """Stop VM by serve name, cleanup tunnels, deregister."""
        if prefix:
            return self.kill_prefix(name)
        if name == 'all':
            return self.kill_all()

        vm_id = self._find_vm_by_name(name)
        if not vm_id:
            return {'status': 'not_found', 'name': name}

        result = self.delete(vm_id)
        if self._ns_registry:
            self._ns_registry.dereg(name)
        return result

    def kill_prefix(self, prefix):
        """Kill all VMs whose serve name starts with prefix."""
        matches = [n for n in self.ps() if n.startswith(prefix)]
        killed = []
        for n in matches:
            self.kill(n)
            killed.append(n)
        return {'status': 'killed', 'prefix': prefix, 'killed': killed}

    def kill_all(self):
        """Stop and remove all VMs, deregister all."""
        registry = self._load_registry()
        for vm_id in list(registry.keys()):
            vm = registry[vm_id]
            serve_name = vm.get('serve_name')
            self.delete(vm_id)
            if serve_name and self._ns_registry:
                self._ns_registry.dereg(serve_name)
        return {'status': 'all_vms_killed'}

    def namespace(self, **kwargs):
        """Get registered server namespace."""
        if self._ns_registry:
            return self._ns_registry.namespace(**kwargs)
        return {}

    def restart(self, name):
        """Restart a served module VM (kill + re-serve)."""
        registry = self._load_registry()
        vm_id = self._find_vm_by_name(name)
        if not vm_id or vm_id not in registry:
            return {'status': 'not_found', 'name': name}

        vm = registry[vm_id]
        mod = vm.get('served_mod')
        port = vm.get('port')
        app_port = vm.get('app_port')
        memory = vm.get('memory', 1024)
        vcpus = vm.get('vcpus', 2)

        if not mod:
            return {'error': f'VM {name} has no served module to restart'}

        self.kill(name)
        return self.forward(
            mod=mod, port=port, app_port=app_port, name=name,
            memory=memory, vcpus=vcpus)

    # ── Module Deployment ───────────────────────────────────────────

    def provision(self, vm_id, timeout=60):
        """Install mod framework into a VM for full mod protocol support.

        Installs Python 3, Node.js, pip, git, and the mod framework.
        After provisioning, the VM can run `m serve <module>` and
        supports the full mod CLI / module loading system.
        """
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}

        vm = registry[vm_id]
        if vm.get('provisioned'):
            return {'success': True, 'vm_id': vm_id, 'cached': True}

        if not self._wait_for_ssh(vm_id, timeout=timeout):
            return {'error': f'SSH not available for VM {vm_id}'}

        commands = [
            # System packages
            ('apt-get update -qq && apt-get install -y -qq '
             'python3 python3-pip git curl nodejs npm openssh-server '
             '> /dev/null 2>&1'),
            # Ensure PATH includes pip bin dir
            'echo "export PATH=\\$PATH:/usr/local/bin:/root/.local/bin" '
            '>> /root/.bashrc',
            # Install mod framework
            ('pip3 install --quiet mod-protocol 2>/dev/null || '
             '(cd /opt && git clone https://github.com/modc2/mod.git '
             '&& cd mod && pip3 install -e . --quiet)'),
            # Standard mod directories
            'mkdir -p /tmp/mod ~/.mod /root/mod/mod/orbit',
            # Verify installation
            'python3 -c "import mod; print(mod.__file__)"',
        ]

        for cmd in commands:
            r = self.exec(vm_id, cmd)
            # Only fail on the verification step
            if not r.get('success') and 'import mod' in cmd:
                return {'error': f'Mod provision failed in VM {vm_id}',
                        'detail': r}

        # Mark VM as provisioned
        registry = self._load_registry()
        if vm_id in registry:
            registry[vm_id]['provisioned'] = True
            self._save_registry(registry)

        return {'success': True, 'vm_id': vm_id}

    def deploy(self, vm_id, mod):
        """Copy a module's files into a running VM and install deps.

        Transfers the module directory from the host into the VM at
        /root/mod/mod/orbit/<mod>/, then installs Python requirements
        and Node.js app dependencies if present.
        """
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}

        if not HAS_MOD:
            return {'error': 'mod framework not available on host'}

        mod_dir = m.dirpath(mod)
        if not mod_dir or not os.path.exists(mod_dir):
            return {'error': f'Module not found: {mod}'}

        vm = registry[vm_id]
        ssh_port = vm.get('ssh_port')
        host = self.remote_host if self.mode == 'remote' else 'localhost'

        # Target path inside VM (follows mod orbit layout)
        target = f'/root/mod/mod/orbit/{mod}'
        self.exec(vm_id, f'mkdir -p {target}')

        # Copy module files via SCP
        scp_cmd = [
            'scp', '-r', '-P', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            f'{mod_dir}/.', f'root@{host}:{target}/',
        ]
        result = subprocess.run(
            scp_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return {'error': f'SCP failed: {result.stderr}'}

        # Install Python deps if present
        self.exec(vm_id,
                  f'test -f {target}/requirements.txt && '
                  f'pip3 install -r {target}/requirements.txt --quiet '
                  f'|| true')

        # Install Node.js app deps if present
        self.exec(vm_id,
                  f'test -f {target}/app/package.json && '
                  f'(cd {target}/app && npm install --silent) || true')

        return {
            'success': True,
            'vm_id': vm_id,
            'mod': mod,
            'target': target,
        }

    def build_rootfs(self, output=None, base=None, size_mb=2048):
        """Build a rootfs ext4 image pre-loaded with mod protocol.

        Creates a ready-to-boot image with Python 3, Node.js, SSH server,
        and the mod framework pre-installed. VMs booted from this image
        can immediately run `m serve <module>` without provisioning.

        Args:
            output:   Output path (default: ~/.firecracker/mod-rootfs.ext4)
            base:     Base rootfs to copy from (or creates fresh)
            size_mb:  Image size in MB for fresh images
        """
        output = output or str(self.store_dir / 'mod-rootfs.ext4')
        base = base or self.rootfs_image

        if base and Path(base).exists():
            subprocess.run(['cp', base, output], check=True)
        else:
            subprocess.run(
                ['dd', 'if=/dev/zero', f'of={output}',
                 'bs=1M', f'count={size_mb}'],
                check=True, capture_output=True)
            subprocess.run(['mkfs.ext4', output],
                           check=True, capture_output=True)

        mount = '/tmp/fc-rootfs-build'
        os.makedirs(mount, exist_ok=True)

        try:
            subprocess.run(['sudo', 'mount', output, mount], check=True)
            provision_cmds = [
                f'chroot {mount} apt-get update -qq',
                (f'chroot {mount} apt-get install -y -qq '
                 'python3 python3-pip git curl openssh-server nodejs npm'),
                (f'chroot {mount} pip3 install mod-protocol 2>/dev/null || '
                 f'chroot {mount} bash -c '
                 '"cd /opt && git clone https://github.com/modc2/mod.git '
                 '&& cd mod && pip3 install -e ."'),
                (f'chroot {mount} mkdir -p /root/.ssh /run/sshd '
                 '/root/mod/mod/orbit /tmp/mod /root/.mod'),
                (f'chroot {mount} bash -c '
                 '"echo PermitRootLogin yes >> /etc/ssh/sshd_config"'),
                f'chroot {mount} bash -c "echo root:mod | chpasswd"',
                (f'chroot {mount} bash -c '
                 '"echo export PATH=\\$PATH:/usr/local/bin:/root/.local/bin '
                 '>> /root/.bashrc"'),
                (f'chroot {mount} systemctl enable ssh 2>/dev/null '
                 '|| true'),
            ]
            for cmd in provision_cmds:
                subprocess.run(
                    cmd, shell=True, check=True, capture_output=True)
        finally:
            subprocess.run(['sudo', 'umount', mount], capture_output=True)

        return {'success': True, 'rootfs': output, 'size_mb': size_mb}

    # ── VM Lifecycle (core API) ─────────────────────────────────────

    def health(self):
        return {
            'status': 'ok' if self.prerequisites_ok else 'degraded',
            'module': 'pm/firecracker',
            'mode': self.mode,
            'prerequisites': self.prerequisites,
            'vms': len(self._load_registry()),
        }

    def status(self):
        registry = self._load_registry()
        running = sum(1 for vm in registry.values()
                      if vm.get('status') == 'running')
        return {
            'module': 'pm/firecracker',
            'total_vms': len(registry),
            'running': running,
            'stopped': len(registry) - running,
            'mode': self.mode,
            'remote_host': self.remote_host if self.mode == 'remote' else None,
            'prerequisites': self.prerequisites,
        }

    def create(self, name, memory=512, vcpus=1, kernel=None, rootfs=None,
               ssh_port=None):
        """Create and start a new Firecracker microVM.

        Uses mod-ready rootfs by default if configured (mod_rootfs in
        config.json), falling back to the base rootfs_image.
        """
        if not self.prerequisites_ok:
            return {'error': 'Prerequisites not met', 'checks': self.prerequisites}

        kernel = kernel or self.kernel_image
        rootfs = rootfs or self.mod_rootfs or self.rootfs_image

        # Validate images
        if self.mode == 'local':
            if not Path(kernel).exists():
                return {'error': f'Kernel not found: {kernel}'}
            if not Path(rootfs).exists():
                return {'error': f'Rootfs not found: {rootfs}'}

        vm_id = f'{name}-{int(time.time())}'
        socket_path = str(self.sockets_dir / f'{vm_id}.sock')
        log_path = str(self.log_dir / f'{vm_id}.log')

        if not ssh_port:
            ssh_port = self._find_free_port(start=2222)

        # Start firecracker process
        log_file = open(log_path, 'w')
        cmd = ['firecracker', '--api-sock', socket_path]
        if self.mode == 'remote':
            cmd = ['ssh', self.remote_host] + cmd

        try:
            proc = subprocess.Popen(
                cmd, stdout=log_file, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            log_file.close()
            return {'error': 'firecracker binary not found'}

        if not self._wait_for_socket(socket_path, timeout=5):
            proc.kill()
            log_file.close()
            return {'error': 'Firecracker socket did not appear'}

        # Configure VM via API
        cfg_result = self._configure_vm(
            socket_path, memory, vcpus, kernel, rootfs, ssh_port)
        if 'error' in cfg_result:
            proc.kill()
            log_file.close()
            return cfg_result

        # Start VM
        start = self._api_call(
            socket_path, 'PUT', '/actions', {'action_type': 'InstanceStart'})
        if 'error' in start:
            proc.kill()
            log_file.close()
            return {'error': f'Failed to start VM: {start["error"]}'}

        # Register
        registry = self._load_registry()
        registry[vm_id] = {
            'id': vm_id,
            'name': name,
            'pid': proc.pid,
            'socket_path': socket_path,
            'log_path': log_path,
            'status': 'running',
            'created': time.time(),
            'memory': memory,
            'vcpus': vcpus,
            'kernel': kernel,
            'rootfs': rootfs,
            'ssh_port': ssh_port,
        }
        self._save_registry(registry)

        return {
            'success': True,
            'vm_id': vm_id,
            'pid': proc.pid,
            'socket_path': socket_path,
            'ssh_port': ssh_port,
        }

    def list(self):
        registry = self._load_registry()
        return {'vms': list(registry.values()), 'count': len(registry)}

    def get(self, vm_id):
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}
        return registry[vm_id]

    def stop(self, vm_id):
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}

        vm = registry[vm_id]
        pid = vm.get('pid')

        if pid and vm.get('status') == 'running':
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

        # Cleanup SSH tunnel processes for this VM
        self._cleanup_tunnels(vm)

        vm['status'] = 'stopped'
        vm['stopped_at'] = time.time()
        registry[vm_id] = vm
        self._save_registry(registry)

        return {'success': True, 'vm_id': vm_id}

    def delete(self, vm_id):
        self.stop(vm_id)

        registry = self._load_registry()
        if vm_id not in registry:
            return {'success': True, 'vm_id': vm_id}

        vm = registry[vm_id]
        sock = vm.get('socket_path')
        if sock and Path(sock).exists():
            Path(sock).unlink()
        log = vm.get('log_path')
        if log and Path(log).exists():
            Path(log).unlink()

        del registry[vm_id]
        self._save_registry(registry)

        return {'success': True, 'vm_id': vm_id}

    def exec(self, vm_id, command):
        """Execute command in VM via SSH."""
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}

        vm = registry[vm_id]
        ssh_port = vm.get('ssh_port')
        if not ssh_port:
            return {'error': 'VM has no SSH port configured'}

        host = self.remote_host if self.mode == 'remote' else 'localhost'
        ssh_cmd = [
            'ssh', '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=5',
            f'root@{host}', command,
        ]

        result = subprocess.run(ssh_cmd, capture_output=True, text=True,
                                timeout=30)
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
        }

    def logs(self, vm_id, lines=50):
        registry = self._load_registry()
        if vm_id not in registry:
            return {'error': f'VM not found: {vm_id}'}

        log_path = registry[vm_id].get('log_path')
        if not log_path or not Path(log_path).exists():
            return {'vm_id': vm_id, 'logs': ''}

        with open(log_path) as f:
            all_lines = f.readlines()
        return {
            'vm_id': vm_id,
            'logs': ''.join(all_lines[-lines:]),
        }

    # ── Config ───────────────────────────────────────────────────────

    def _load_config(self):
        cfg = self.module_dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                data = json.load(f)
            return data.get('data', data) if 'data' in data else data
        return {}

    # ── Prerequisites ────────────────────────────────────────────────

    def _check_prerequisites(self):
        checks = {}
        if self.mode == 'local':
            checks['kvm'] = os.path.exists('/dev/kvm')
            checks['firecracker'] = self._which('firecracker') is not None
            checks['kernel'] = Path(self.kernel_image).exists()
            checks['rootfs'] = Path(self.rootfs_image).exists()
        else:
            checks['ssh'] = self._test_ssh()
            if checks['ssh']:
                r = self._remote_run(['which', 'firecracker'])
                checks['firecracker'] = r.returncode == 0
            else:
                checks['firecracker'] = False
        return checks

    # ── Registry ─────────────────────────────────────────────────────

    def _load_registry(self):
        if not self.registry_path.exists():
            return {}
        with open(self.registry_path, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return data

    def _save_registry(self, registry):
        with open(self.registry_path, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(registry, f, indent=2, default=str)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _cleanup_dead_vms(self):
        registry = self._load_registry()
        dead = []
        for vm_id, vm in registry.items():
            pid = vm.get('pid')
            if pid and vm.get('status') == 'running':
                try:
                    os.kill(pid, 0)
                except (OSError, ProcessLookupError):
                    dead.append(vm_id)
        if dead:
            for vm_id in dead:
                registry[vm_id]['status'] = 'stopped'
            self._save_registry(registry)

    # ── Execution Layer ──────────────────────────────────────────────

    def _remote_run(self, cmd, **kw):
        return subprocess.run(
            ['ssh', self.remote_host] + cmd,
            capture_output=True, text=True, **kw)

    def _api_call(self, socket_path, method, endpoint, data=None):
        """HTTP call to Firecracker API via curl over Unix socket."""
        url = f'http://localhost{endpoint}'
        cmd = ['curl', '-s', '-X', method, '--unix-socket', socket_path, url]
        if data:
            cmd += ['-H', 'Content-Type: application/json',
                    '-d', json.dumps(data)]

        if self.mode == 'remote':
            cmd = ['ssh', self.remote_host] + cmd

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {'error': result.stderr or 'curl failed'}
        if result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {'raw': result.stdout}
        return {'success': True}

    def _configure_vm(self, socket_path, memory, vcpus, kernel, rootfs,
                      ssh_port):
        """Sequence Firecracker API calls to configure a VM."""
        # Machine config
        r = self._api_call(socket_path, 'PUT', '/machine-config', {
            'vcpu_count': vcpus, 'mem_size_mib': memory})
        if 'error' in r:
            return r

        # Boot source
        r = self._api_call(socket_path, 'PUT', '/boot-source', {
            'kernel_image_path': kernel,
            'boot_args': 'console=ttyS0 reboot=k panic=1 pci=off'})
        if 'error' in r:
            return r

        # Root drive
        r = self._api_call(socket_path, 'PUT', '/drives/rootfs', {
            'drive_id': 'rootfs',
            'path_on_host': rootfs,
            'is_root_device': True,
            'is_read_only': False})
        if 'error' in r:
            return r

        # Network interface
        tap = f'{self.network_config.get("tap_prefix", "fc-tap")}-{ssh_port}'
        self._api_call(socket_path, 'PUT', '/network-interfaces/eth0', {
            'iface_id': 'eth0',
            'host_dev_name': tap,
            'guest_mac': self._generate_mac()})

        return {'success': True}

    # ── Helpers ──────────────────────────────────────────────────────

    def _which(self, cmd):
        r = subprocess.run(['which', cmd], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def _test_ssh(self):
        if not self.remote_host:
            return False
        try:
            r = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=3', self.remote_host,
                 'echo', 'ok'],
                capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _wait_for_socket(self, socket_path, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if Path(socket_path).exists():
                return True
            time.sleep(0.1)
        return False

    def _find_free_port(self, start=2222):
        for port in range(start, start + 1000):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(('', port))
                    return port
                except OSError:
                    continue
        raise RuntimeError('No free ports available')

    def _generate_mac(self):
        mac = [0x02, 0x00, 0x00,
               random.randint(0x00, 0xff),
               random.randint(0x00, 0xff),
               random.randint(0x00, 0xff)]
        return ':'.join(f'{b:02x}' for b in mac)

    def _find_vm_by_name(self, name):
        """Find a VM ID by serve name or VM name."""
        registry = self._load_registry()
        for vm_id, vm in registry.items():
            if vm.get('serve_name') == name or vm.get('name') == name:
                return vm_id
        return None

    def _wait_for_ssh(self, vm_id, timeout=60):
        """Wait until SSH is responsive inside the VM."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.exec(vm_id, 'echo ready')
            if r.get('success'):
                return True
            time.sleep(2)
        return False

    def _setup_port_forward(self, ssh_port, target_port):
        """Create an SSH local port forward: host:target_port → VM:target_port."""
        host = self.remote_host if self.mode == 'remote' else 'localhost'
        cmd = [
            'ssh', '-fN',
            '-L', f'{target_port}:localhost:{target_port}',
            '-p', str(ssh_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ExitOnForwardFailure=yes',
            f'root@{host}',
        ]
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _cleanup_tunnels(self, vm):
        """Kill SSH tunnel processes for a VM's forwarded ports."""
        ssh_port = vm.get('ssh_port')
        if not ssh_port:
            return
        try:
            r = subprocess.run(
                ['pgrep', '-f', f'ssh.*-p {ssh_port}.*-L'],
                capture_output=True, text=True)
            for pid_str in r.stdout.strip().split('\n'):
                pid_str = pid_str.strip()
                if pid_str:
                    try:
                        os.kill(int(pid_str), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
        except FileNotFoundError:
            pass
