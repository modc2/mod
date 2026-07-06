# pm/firecracker

Firecracker microVM lifecycle management and PM backend. Runs mod modules
(API + Next.js app) inside lightweight microVMs with full mod protocol support.

## Capabilities

- **PM Backend**: Drop-in replacement for Docker/PM2 — `m.serve('agent', pm='pm/firecracker')`
- **Module Serving**: Run any mod module as API + app inside an isolated VM
- **Auto-Provisioning**: Install mod framework (Python, Node.js, SSH, mod CLI) into VMs
- **Module Deployment**: SCP module files + install deps automatically
- **Rootfs Builder**: Build pre-provisioned rootfs images for instant boot
- **VM Lifecycle**: Create, start, stop, delete Firecracker microVMs
- **SSH Exec**: Execute commands inside VMs via SSH
- **Port Forwarding**: SSH tunnels for API/app port access from host
- **Namespace Registration**: Auto-register services for Routy/Caddy discovery
- **Local + Remote**: Direct KVM or SSH-tunneled to a Linux host

## Prerequisites

### Local mode (Linux)
- `/dev/kvm` accessible (Intel VT-x or AMD-V)
- `firecracker` binary in PATH
- Kernel image (uncompressed vmlinux)
- Root filesystem (ext4 image with SSH server)

### Remote mode (macOS → Linux)
- SSH access to a Linux host with Firecracker installed
- Configure `remote_host` in config.json or pass at init

## Usage

### Serve a module in a VM (PM backend)
```python
import mod as m

# Via the serve system (recommended)
m.serve('agent', pm='pm/firecracker')

# Direct usage
fc = m.mod('pm/firecracker')()
fc.forward(mod='agent', port=8080)                # serve with API
fc.forward(mod='bridge', port=8090, memory=2048)   # custom resources
fc.kill('agent')                                    # stop + cleanup
```

### VM lifecycle
```python
fc = m.mod('pm/firecracker')()

fc.health()                                          # check prerequisites
fc.create(name='web', memory=1024, vcpus=2)          # create VM
fc.provision(vm_id='web-1234567')                    # install mod protocol
fc.deploy(vm_id='web-1234567', mod='agent')          # copy module files
fc.exec(vm_id='web-1234567', command='m info agent') # run command
fc.stop(vm_id='web-1234567')                         # stop VM
fc.delete(vm_id='web-1234567')                       # delete VM
fc.logs(vm_id='web-1234567', lines=100)              # view logs
```

### Build a mod-ready rootfs
```python
fc = m.mod('pm/firecracker')()
fc.build_rootfs()  # → ~/.firecracker/mod-rootfs.ext4
# Then set mod_rootfs in config.json to skip provisioning on every boot
```

### CLI
```bash
# PM backend (serve modules)
m pm/firecracker/forward mod=agent port=8080         # serve module in VM
m pm/firecracker/servers                              # list running servers
m pm/firecracker/kill name=agent                      # stop + remove

# VM lifecycle
m pm/firecracker                                      # status
m pm/firecracker/health                               # prerequisites check
m pm/firecracker/create name=web memory=1024 vcpus=2  # create VM
m pm/firecracker/provision vm_id=web-1234567          # install mod protocol
m pm/firecracker/deploy vm_id=web-1234567 mod=agent   # deploy module
m pm/firecracker/exec vm_id=web-1234567 command=ls    # exec in VM
m pm/firecracker/stop vm_id=web-1234567               # stop
m pm/firecracker/delete vm_id=web-1234567             # delete
m pm/firecracker/logs vm_id=web-1234567               # logs

# Build rootfs
m pm/firecracker/build_rootfs                         # build mod-ready image
```

## API

### PM Backend Interface

| Function | Args | Description |
|----------|------|-------------|
| `forward` | mod, port, params, key, memory, vcpus, name, app_port | Serve module in VM |
| `serve` | (alias for forward) | Serve module in VM |
| `ps` | search | List running VM names |
| `servers` | search | List server VMs (alias for ps) |
| `exists` | name | Check if served module VM is running |
| `kill` | name, prefix | Stop VM + deregister from namespace |
| `kill_all` | — | Stop all VMs |
| `namespace` | — | Get registered server namespace |
| `restart` | name | Kill + re-serve a module VM |

### Module Deployment

| Function | Args | Description |
|----------|------|-------------|
| `provision` | vm_id, timeout=60 | Install mod framework into VM |
| `deploy` | vm_id, mod | Copy module files + install deps |
| `build_rootfs` | output, base, size_mb=2048 | Build mod-ready rootfs image |

### VM Lifecycle

| Function | Args | Description |
|----------|------|-------------|
| `health` | — | Prerequisites + VM count |
| `status` | — | Aggregate VM stats |
| `create` | name, memory=512, vcpus=1, kernel, rootfs, ssh_port | Create & start VM |
| `list` | — | List all VMs |
| `get` | vm_id | Single VM details |
| `stop` | vm_id | Stop running VM |
| `delete` | vm_id | Stop + remove VM |
| `exec` | vm_id, command | Run command in VM via SSH |
| `logs` | vm_id, lines=50 | View VM console logs |

## Config

```json
{
  "name": "pm/firecracker",
  "mode": "local",
  "remote_host": null,
  "kernel_image": "/usr/local/share/firecracker/vmlinux.bin",
  "rootfs_image": "/usr/local/share/firecracker/rootfs.ext4",
  "mod_rootfs": null,
  "default_memory": 1024,
  "default_vcpus": 2,
  "network": {
    "tap_prefix": "fc-tap",
    "subnet": "172.16.0.0/24",
    "gateway": "172.16.0.1"
  }
}
```

- `mod_rootfs`: Path to a pre-built rootfs with mod installed. Set after running `build_rootfs()` to skip provisioning on every VM boot.
- `default_memory`: Default VM memory in MiB for `forward()`
- `default_vcpus`: Default vCPU count for `forward()`

## Structure

```
mod/core/server/pm/pm/firecracker/
├── mod.py           # Mod class (PM backend + VM lifecycle)
├── config.json      # Module config
├── skill.md         # This file
└── test/
    └── test_firecracker.py
```

## Storage

- Registry: `~/.firecracker/vms.json`
- Sockets: `~/.firecracker/sockets/`
- Logs: `~/.firecracker/logs/`
- Mod rootfs: `~/.firecracker/mod-rootfs.ext4` (after build_rootfs)

## Mod Protocol

- Module: `pm/firecracker`
- Load: `m.mod('pm/firecracker')()`
- Config: `config.json` in module root
- PM backend: Use as `pm='pm/firecracker'` in `m.serve()`

### Default VM mod protocol support

VMs are provisioned with the full mod ecosystem:
- Python 3 + pip
- Node.js + npm
- `m` CLI in PATH
- `mod` Python package installed
- Standard dirs: `/root/mod/mod/orbit/`, `/tmp/mod/`, `/root/.mod/`
- SSH server for exec/deploy access

Set `mod_rootfs` in config to a pre-built image (via `build_rootfs()`) to
skip runtime provisioning and boot VMs with mod protocol instantly.
