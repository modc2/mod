# modenv — the reusable nix environment

One shared, pinned dev environment for every mod — the way pm2 is the one shared
supervisor. Define the env here once; modules consume it instead of each
re-deriving node/python/rust/caddy.

## What it provides (general + light)
Language interpreters/compilers + the system tools the gateway and processes
need — **not** every library (pip/npm/cargo still handle app deps, so the layer
stays small): `nodejs_20`, `pm2`, `python312`+pip+virtualenv, `rustc`/`cargo`,
`caddy`, `jq`, `iproute2` (ss), `git`, `curl`, gcc/make/pkg-config/openssl/libffi.

The single source of truth is [`packages.nix`](packages.nix), consumed by both
`flake.nix` (modern, pinned via `flake.lock`) and `shell.nix` (classic).

## Use it directly
```bash
nix develop path:/root/mod/mod/core/nix          # full shell
nix develop path:/root/mod/mod/core/nix#node     # lighter: node-only
nix print-dev-env path:/root/mod/mod/core/nix    # sourceable env (used by core/pm)
```

## Reuse from a module
Drop [`templates/module.flake.nix`](templates/module.flake.nix) into a module as
`flake.nix` (it `follows` this flake's nixpkgs and reuses `modenv.lib.mkShell`),
then add module-specific tools via `extra = [ ... ]`. The claude process backend
and `core/pm` auto-run a module's launchers inside its image when it ships a
`flake.nix`/`shell.nix`; otherwise they fall back to this shared env.

Compose from another flake:
```nix
inputs.modenv.url = "path:../../core/nix";
# ...
devShells.${system}.default = modenv.lib.mkShell { inherit pkgs; extra = [ pkgs.ffmpeg ]; };
```

Pin is `nixpkgs nixos-24.11`; bump by editing `flake.nix` + `nix flake update`.
