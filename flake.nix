{
  description = "CLI and MCP server for ORTEC Employee Self Service: shifts, swaps and leave";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
          "x86_64-darwin"
          "aarch64-darwin"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.callPackage ./nix/package.nix { };
      });

      devShells = forAllSystems (pkgs: {
        default =
          with pkgs;
          mkShell {
            buildInputs = [
              (python3.withPackages (ps: with ps; [
                httpx
                mcp
                pytest
              ]))
              ruff
            ];

            shellHook = ''
              export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            '';
          };
      });

      overlays.default = final: _prev: {
        ortec-cli = final.callPackage ./nix/package.nix { };
      };

      homeManagerModules.default = ./nix/hm-module.nix;
    };
}
