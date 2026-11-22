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
    # results, `--files` to find a file by name in a user folder, and `--open` to find gio (opening a missing
    # file fails in gio, never for want of it).
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
      mkdir -p $TMPDIR/Documents/.notes && touch "$TMPDIR/Documents/.notes/Quarterly report.txt"
      echo report | HOME=$TMPDIR PATH=/no-such ${search}/bin/flow-search --files > files.json
      ${lib.getExe pkgs.python3} -c 'import json; rows = json.load(open("files.json")); assert [r["title"] for r in rows] == ["Quarterly report.txt"], rows'
      rc=0; HOME=$TMPDIR PATH=/no-such ${search}/bin/flow-search --open file file:///no-such || rc=$?
      [ "$rc" -ne 0 ] && [ "$rc" -ne 127 ] || { echo "flow-search --open exited $rc: gio missing or a missing file opened"; exit 1; }
      mkdir $out
      ln -s ${niriConfig} $out/niri.kdl
      printf '%s\n' ${lib.escapeShellArgs wrappers} > $out/wrappers
    '';
    # A pointer for the guest tests (tests/flow-pointer/flow-pointer.c), as wtype is their keyboard. Test tooling:
    # the module does not install it; the test guest's own configuration does. CI builds it.
    packages.aarch64-linux.flow-pointer = let pkgs = nixpkgs.legacyPackages.aarch64-linux; in
      pkgs.stdenv.mkDerivation {
        pname = "flow-pointer";
        version = "1";
        src = ./tests/flow-pointer;
        strictDeps = true;
        nativeBuildInputs = [ pkgs.wayland-scanner pkgs.pkg-config ];
        buildInputs = [ pkgs.wayland ];
        buildPhase = ''
          xml=${pkgs.wlr-protocols}/share/wlr-protocols/unstable/wlr-virtual-pointer-unstable-v1.xml
          wayland-scanner client-header $xml virtual-pointer.h
          wayland-scanner private-code $xml virtual-pointer.c
          $CC -O2 -Wall -Wextra -Werror flow-pointer.c virtual-pointer.c $(pkg-config --cflags --libs wayland-client) -o flow-pointer
        '';
        installPhase = "install -Dm755 flow-pointer $out/bin/flow-pointer";
        # Steps are checked before it connects, so a typo moves nothing: each bad call must fail without a
        # display, and a good one must get as far as looking for one.
        doInstallCheck = true;
        installCheckPhase = ''
          p=$out/bin/flow-pointer
          for bad in "10" "0 10 move 1 1" "10 10 move 10 1" "10 10 move 1" "10 10 jump" "10 10 scroll 101" \
                     "10 10 scroll" "10 10 wait -1" "10 10 click 5"; do
            if out=$(WAYLAND_DISPLAY=no-such $p $bad 2>&1); then echo "flow-pointer $bad was accepted"; exit 1; fi
            case $out in *"cannot connect"*) echo "flow-pointer $bad reached the display: $out"; exit 1;; esac
          done
          out=$(XDG_RUNTIME_DIR=$TMPDIR WAYLAND_DISPLAY=no-such $p 10 10 move 9 9 down up click right scroll -3 hscroll 2 wait 0 2>&1) || true
          case $out in *"cannot connect"*) ;; *) echo "good steps did not reach the display: $out"; exit 1;; esac
        '';
        meta.mainProgram = "flow-pointer";
      };
    nixosModules.default = import ./modules/flow.nix;
    nixosConfigurations.test = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [ ./tests/configuration.nix ./modules/flow.nix ];
    };
  };
}
