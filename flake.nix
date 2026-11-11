{
  description = "Flow: a multiplatform Linux session";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }: {
    checks.aarch64-linux.spaces = let pkgs = nixpkgs.legacyPackages.aarch64-linux; in
      pkgs.runCommand "flow-spaces-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
        cd ${./.}
        python -B -m unittest discover -s tests -v
        touch $out
      '';
    nixosModules.default = import ./modules/flow.nix;
    nixosConfigurations.test = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [ ./tests/configuration.nix ./modules/flow.nix ];
    };
  };
}
