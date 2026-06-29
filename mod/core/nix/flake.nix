{
  description = "modenv — the reusable mod dev environment (node + python + rust + gateway tools). Shared across every module the way pm2 is the shared supervisor: define the env once, consume it everywhere.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f s);
      pkgsFor = system: import nixpkgs { inherit system; config.allowUnfree = true; };

      shellHook = ''
        export MOD_NIX=1
        export PATH="$HOME/.cargo/bin:$PATH"
      '';
    in
    {
      # ── Reusable building blocks for OTHER flakes/modules ──
      # A module flake can `inputs.modenv.url = "path:../../core/nix"` then either
      # reuse a devShell directly or compose with extra packages:
      #   devShells.${system}.default =
      #     modenv.lib.mkShell { pkgs = ...; extra = [ pkgs.ffmpeg ]; };
      lib = {
        packages = pkgs: import ./packages.nix { inherit pkgs; };
        mkShell = { pkgs, extra ? [ ], name ? "mod-env" }:
          pkgs.mkShell {
            inherit name shellHook;
            packages = (import ./packages.nix { inherit pkgs; }) ++ extra;
          };
      };

      # ── Ready-to-use dev shells ──  `nix develop path:.../core/nix#<name>`
      devShells = forAll (system:
        let pkgs = pkgsFor system; in {
          default = self.lib.mkShell { inherit pkgs; };          # the full env
          full = self.lib.mkShell { inherit pkgs; };
          node = pkgs.mkShell {
            name = "mod-node"; inherit shellHook;
            packages = [ pkgs.nodejs_20 pkgs.nodePackages.pm2 pkgs.caddy pkgs.jq pkgs.iproute2 ];
          };
          python = pkgs.mkShell {
            name = "mod-python"; inherit shellHook;
            packages = [ pkgs.python312 pkgs.python312Packages.pip pkgs.python312Packages.virtualenv pkgs.gcc pkgs.pkg-config pkgs.openssl pkgs.libffi ];
          };
          rust = pkgs.mkShell {
            name = "mod-rust"; inherit shellHook;
            packages = [ pkgs.rustc pkgs.cargo pkgs.rustfmt pkgs.clippy pkgs.pkg-config pkgs.openssl pkgs.gcc ];
          };
        });
    };
}
