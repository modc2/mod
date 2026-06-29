# Classic nix-shell entrypoint for the reusable mod env — for `nix-shell` users
# and the process.rs `nix-shell --run` launcher path (modules that ship a
# shell.nix). Mirrors flake.nix's `default` devShell, sharing packages.nix.
#
# Reuse from a module's own shell.nix:
#   import ../../core/nix/shell.nix { extra = [ ]; }
#
# nixpkgs is pinned to the same nixos-24.11 channel the flake locks; override by
# passing `pkgs`.
{ extra ? [ ]
, pkgs ? import (fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/refs/heads/nixos-24.11.tar.gz";
  }) { config.allowUnfree = true; }
}:

pkgs.mkShell {
  name = "mod-env";
  packages = (import ./packages.nix { inherit pkgs; }) ++ extra;
  shellHook = ''
    export MOD_NIX=1
    export PATH="$HOME/.cargo/bin:$PATH"
  '';
}
