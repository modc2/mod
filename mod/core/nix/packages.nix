# The shared mod toolchain — the single source of truth for what every module's
# environment provides. Imported by both flake.nix (modern) and shell.nix
# (classic) so there is ONE list to maintain.
#
# Philosophy (general + light): provide the language INTERPRETERS/COMPILERS and
# the system tools the gateway + processes need — NOT every library. App-level
# deps stay with each language's own package manager (pip / npm / cargo), so the
# nix layer is small and shared, exactly like pm2 is one shared supervisor.
{ pkgs }:

with pkgs; [
  # ── Node (next apps + the pm2 supervisor + the activator) ──
  nodejs_20
  nodePackages.pm2

  # ── Python (fastapi/uvicorn modules + the `m` CLI) ──
  python312
  python312Packages.pip
  python312Packages.virtualenv

  # ── Rust (the compiled module APIs: claude/polymarket/venice/… ) ──
  rustc
  cargo
  rustfmt
  clippy

  # ── Gateway / ops tooling ──
  caddy          # the host reverse proxy
  jq             # config.json wrangling in start scripts
  iproute2       # `ss` — port/conn probes (activator + process.rs)
  git
  curl
  cacert         # TLS roots for curl/https inside the shell

  # ── Native build deps commonly needed by python wheels (PyNaCl, sr25519,
  #    py-ed25519) and rust crates (openssl-sys) ──
  gcc
  gnumake
  pkg-config
  openssl
  libffi
]
