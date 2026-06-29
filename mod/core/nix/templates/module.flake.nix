{
  # Per-module flake template — drop this in a module as `flake.nix` to opt the
  # module into the shared mod env. The claude process backend (process.rs) then
  # auto-runs the module's launchers inside `nix develop`. Add module-specific
  # tools via `extra` rather than forking the shared list.
  description = "MODULE_NAME — uses the shared modenv";

  inputs.modenv.url = "path:../../core/nix"; # relative path within the mod repo
  inputs.nixpkgs.follows = "modenv/nixpkgs";

  outputs = { self, modenv, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f s);
    in {
      devShells = forAll (system:
        let pkgs = import nixpkgs { inherit system; config.allowUnfree = true; }; in {
          default = modenv.lib.mkShell {
            inherit pkgs;
            extra = [ ]; # e.g. [ pkgs.ffmpeg pkgs.imagemagick ]
          };
        });
    };
}
