{
  description = "Flow: a multiplatform Linux session";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs, ... }: {
    checks.aarch64-linux.spaces = let pkgs = nixpkgs.legacyPackages.aarch64-linux; in
      pkgs.runCommand "flow-spaces-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
        cd ${./.}
        python -B -m unittest discover -s tests -v
        touch $out
      '';
    # Flow's own pieces of the test system, without the rest of it: the rendered Niri config, which Niri
    # must accept, and the wrappers. Building one runs its shell syntax check; a placeholder that
    # replaceVars cannot fill fails the Niri config. CI builds this (.github/workflows/floe-tests.yml).
    checks.aarch64-linux.module = let
      inherit (self.nixosConfigurations.test) config pkgs;
      inherit (nixpkgs) lib;
      niriConfig = config.environment.etc."niri/config.kdl".source;
      wrappers = builtins.filter (p: lib.hasPrefix "flow" (lib.getName p)) config.environment.systemPackages
        ++ [ config.systemd.user.services.flow-shell.serviceConfig.ExecStart
             config.systemd.user.services.flow-api.serviceConfig.ExecStart ];
    in pkgs.runCommand "flow-module-check" { } ''
      ${lib.getExe config.programs.niri.package} validate -c ${niriConfig}
      mkdir $out
      ln -s ${niriConfig} $out/niri.kdl
      printf '%s\n' ${lib.escapeShellArgs wrappers} > $out/wrappers
    '';
    nixosModules.default = import ./modules/flow.nix;
    nixosConfigurations.test = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [ ./tests/configuration.nix ./modules/flow.nix ];
    };
  };
}
