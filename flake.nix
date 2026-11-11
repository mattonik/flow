{
  description = "Flow: a multiplatform Linux session";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }: {
    nixosModules.default = import ./modules/flow.nix;
    nixosConfigurations.test = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [ ./tests/configuration.nix ./modules/flow.nix ];
    };
  };
}
