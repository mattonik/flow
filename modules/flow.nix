{ config, lib, pkgs, ... }:
let
  cfg = config.services.flow;
  spaces = pkgs.writeShellScriptBin "flow-spaces" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 ${../session/spaces.py} "$@"
  '';
  recovery = pkgs.writeShellScriptBin "flow-recovery" ''
    export PYTHONPATH="${../session}"
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 ${../session/restore.py} "$@"
  '';
  appmenu = pkgs.writeShellScriptBin "flow-appmenu" ''
    export GI_TYPELIB_PATH="${pkgs.at-spi2-core}/lib/girepository-1.0:${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pyatspi p.pygobject3 ])}/bin/python3 ${../session/appmenu.py} "$@"
  '';
  launcher = pkgs.writeShellScriptBin "flow-apps" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    exec ${lib.getExe pkgs.fuzzel} "$@"
  '';
  search = pkgs.writeShellScriptBin "flow-search" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export GI_TYPELIB_PATH="${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pygobject3 ])}/bin/python3 ${../session/search.py}
  '';
  niriConfig = pkgs.replaceVars ../session/niri.kdl {
    terminal = lib.getExe pkgs.foot;
    lock = lib.getExe pkgs.swaylock;
    shell = lib.getExe pkgs.quickshell;
    shellConfig = ../session/shell.qml;
  };
in {
  options.services.flow.enable = lib.mkEnableOption "the experimental Flow session";
  config = lib.mkIf cfg.enable {
    programs.niri.enable = true;
    security.pam.services.swaylock = {};
    hardware.graphics.enable = true;
    security.polkit.enable = true;
    environment.systemPackages = with pkgs; [
      foot fuzzel quickshell swaylock firefox nautilus spaces launcher appmenu
      xwayland-satellite mesa-demos wayland-utils recovery search wireplumber networkmanager
    ];
    environment.etc."niri/config.kdl".source = niriConfig;
    environment.etc."xdg/fuzzel/fuzzel.ini".text = ''
      [main]
      font=sans:size=12
      prompt="Search apps: "
      [colors]
      background=0b1015f2
      text=ecf2f0ff
      prompt=b8ddcbff
      input=ecf2f0ff
      match=b8ddcbff
      selection=172128ff
      selection-text=ecf2f0ff
      selection-match=b8ddcbff
      border=b8ddcbff
      [border]
      radius=12
    '';
    environment.sessionVariables.NIXOS_OZONE_WL = "1";
  };
}
