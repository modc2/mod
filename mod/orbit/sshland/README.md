# sshland

SSH host and connection manager for the mod fleet.

Keeps a local inventory of SSH hosts and exposes fns to add, list, remove, and
test connections. The inventory is private (addresses, users, key paths) and
lives off-tree in `~/.mod/sshland/hosts.json` — never in the committed
`config.json`.

## Usage

```bash
m sshland                        # module info
m sshland/hosts                  # list known hosts
m sshland/add box1 root@1.2.3.4  # add a host
m sshland/add box2 me@host.tld:2222 key=~/.ssh/id_ed25519
m sshland/remove box1
m sshland/test box2              # non-interactive connectivity check
```

## fns

| fn       | what it does                                              |
|----------|-----------------------------------------------------------|
| `info`   | module summary + host count                               |
| `hosts`  | list known hosts (key paths omitted)                      |
| `add`    | add a host from `user@host[:port]` (optional `key=`, `port=`) |
| `remove` | drop a host from the inventory                            |
| `test`   | `ssh -o BatchMode=yes` connectivity check                 |

## State

- `~/.mod/sshland/hosts.json` — the host inventory (off-tree, private).
