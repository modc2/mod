# nanovm

Micro-VMs out of nothing but the Linux kernel. Fully open source (MIT), pure
Go, zero dependencies — one static binary that is the library, the CLI, the
HTTP API and the web console at once.

A **nanovm** is a process behind:

- its own **PID, UTS, mount and IPC namespaces** (and, by default, an empty
  **network namespace** — no network unless you ask),
- its own **root filesystem** via `pivot_root`,
- a **cgroup v2 slice** for `memory.max`, `cpu.max` and `pids.max`, entered at
  clone time (`CLONE_INTO_CGROUP`) so there is no window before the caps apply.

No daemon, no image format, no C, nothing downloaded at build or run time. The
entire runtime is `src/` — a `vm` package and a `cmd/nanovm` main, ~1000 lines
you can read end to end.

## Quick start

```sh
m nanovm/build                                  # go build → src/bin/nanovm
m nanovm/run name=demo cmd='sleep 300' mem=64M  # boot one, detached
m nanovm/vms                                    # list, liveness refreshed
m nanovm/exec name=demo cmd='ps aux'            # run a command inside
m nanovm/logs name=demo                         # tail its output
m nanovm/rm name=demo                           # stop and forget
m nanovm/serve                                  # API + console on :51230
```

Or the binary directly:

```sh
nanovm run --mem 64M --cpu 0.5 --pids 128 demo sh    # foreground shell, pid 1
nanovm run -d --net web python3 -m http.server       # detached, with network
nanovm exec demo ps aux
nanovm ls
nanovm stop demo && nanovm rm demo
```

## Two kinds of root

**Host lens (default, `rootfs` empty).** The host's root bind-mounted
**read-only**, with a fresh `/proc` (the vm sees only its own processes), a
private tmpfs `/tmp`, and optional `--rw PATH` islands bound read-write.
Useful with zero setup: every tool on the host is there, but nothing can be
changed outside the paths you grant.

**Pivot root (`--rootfs DIR`).** `pivot_root` into any directory tree:

```sh
nanovm rootfs /tmp/root          # minimal busybox root from the host's busybox
docker export $(docker create alpine) | tar -C /tmp/alpine -x   # or a real distro
nanovm run --rootfs /tmp/alpine --mem 128M demo sh
```

## HTTP API

`nanovm serve --port 51230` — console at `/nanovm/`, JSON at the root.
Errors are 4xx with a reason in the body, never 5xx.

| Route | Does |
|---|---|
| `GET /health` | liveness, vm counts, backend |
| `GET /vms` · `POST /vms` | list · boot (body = Spec JSON, always detached) |
| `GET /vms/{name}` · `DELETE` | state · stop and forget |
| `POST /vms/{name}/stop` | SIGTERM, then SIGKILL |
| `POST /vms/{name}/exec` | `{"cmd":["sh","-c","..."]}`, output captured |
| `GET /vms/{name}/logs?tail=100` | a detached vm's output |

As a library: `import "nanovm/vm"` — `vm.Run(vm.Spec{...})`, `vm.List()`,
`vm.Exec()`, `vm.Stop()`, `vm.Remove()`, `vm.Serve(port)`.

## Honest limits

- **Container-tier isolation, not hardware virtualization.** Namespaces and
  cgroups share the host kernel; a kernel exploit escapes. For hostile code
  you want KVM (Firecracker/Cloud Hypervisor) under this same Spec.
- **Root required** to create namespaces and cgroup slices.
- **`exec` shells out to util-linux `nsenter`** — `setns(CLONE_NEWNS)` refuses
  multithreaded callers, which every Go process is before `main`.
- **`/proc/meminfo` shows host totals** (it is not cgroup-aware). The cap is
  real: the OOM killer answers at `memory.max`.
- The host-lens read-only remount covers the top mount; separately-mounted
  host filesystems under it keep their own flags.

## Layout

```
mod.py              anchor — every verb is a CLI verb and an API route
config.json         module manifest (:51230, /nanovm)
src/
  go.mod            module nanovm, stdlib only
  vm/               the framework: spec+store, run (clone/pivot), cgroup,
                    manage (stop/rm/logs/exec), rootfs builder, server, console
  cmd/nanovm/       the CLI
tests/              offline pytest suite (scratch NANOVM_DATA, no network)
LICENSE             MIT
```

State lives in `~/.mod/nanovm` — one JSON per vm, logs for detached ones.
Liveness is never trusted from the file: each read re-checks `/proc/<pid>`
for the `NANOVM_NAME` marker the init plants in the payload's environment.
