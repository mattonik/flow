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
    # Wrappers are found by name, flow*, so one named otherwise is left out: systemsettings, which is
    # installed only with Plasma, is not checked. Screen readers, and the opt-in app menu, need the
    # accessibility bus: the evaluated setting must be on, whichever module sets it.
    # The installed flow-search runs for real: the shell's search panel needs `--json` to answer with
    # results, and `--open` to find gio (opening a missing file fails in gio, never for want of it).
    checks.aarch64-linux.module = let
      inherit (self.nixosConfigurations.test) config pkgs;
      inherit (nixpkgs) lib;
      niriConfig = config.environment.etc."niri/config.kdl".source;
      wrappers = builtins.filter (p: lib.hasPrefix "flow" (lib.getName p)) config.environment.systemPackages
        ++ [ config.systemd.user.services.flow-shell.serviceConfig.ExecStart
             config.systemd.user.services.flow-api.serviceConfig.ExecStart ];
      search = lib.findFirst (p: lib.getName p == "flow-search") (throw "no flow-search") config.environment.systemPackages;
    in assert wrappers != [ ]; assert config.services.gnome.at-spi2-core.enable; pkgs.runCommand "flow-module-check" { } ''
      ${lib.getExe config.programs.niri.package} validate -c ${niriConfig}
      HOME=$TMPDIR PATH=/no-such ${search}/bin/flow-search --json > rows.json
      ${lib.getExe pkgs.python3} -c 'import json; rows = json.load(open("rows.json")); assert rows and rows[0]["kind"] == "settings", rows'
      rc=0; HOME=$TMPDIR PATH=/no-such ${search}/bin/flow-search --open file file:///no-such || rc=$?
      [ "$rc" -ne 0 ] && [ "$rc" -ne 127 ] || { echo "flow-search --open exited $rc: gio missing or a missing file opened"; exit 1; }
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
